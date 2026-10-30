#!/usr/bin/env bash
# E2E full-hop gate (realtime-consumer D2, ADR 0013 decision 4).
#
# The wall's live-delta spec is only meaningful when its only input is the
# bus, so the e2e gate runs the full hop around Playwright: the outbox relay,
# the realtime consumer, the payroll calculation worker and the CDC projection
# sink (real Debezium → kafka-test → ClickHouse canonical test database)
# against the throwaway kafka-test broker (:9093), Connect-test (:8084) and
# clickhouse-test store (:8124), while the dev PostgreSQL and Redis — the same
# services Playwright's uvicorn reads — keep serving the app. `make check-e2e`
# owns service startup, migrations and seeding; this script owns the worker
# processes and exposes them as the Playwright run's surroundings.
#
# Readiness is deterministic log lines, never sleeps: `relay_started` lands
# after the publisher connected, `realtime_consumer_started` after the
# consumer's first completed fetch round pinned its `latest` position (the
# R6 race the log line closes). Teardown is a bounded SIGTERM the workers
# handle gracefully, with a KILL fallback.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
API_DIR="$ROOT_DIR/apps/api"
# `uv run python` resolves to exactly this interpreter — used directly so the
# recorded PID is the worker itself (no wrapper to forward signals through).
PYTHON="$API_DIR/.venv/bin/python"
BOOTSTRAP="localhost:9093" # the kafka-test service the Makefile brings up
# Reports read and the CDC sink projects the throwaway analytics store. Pin it
# rather than honoring a caller's CLICKHOUSE_URL: synthetic fixtures must never
# reach another store, and the Playwright-managed API must read the same
# database the sink writes (the DSN is the clickhouse-test service). An empty
# CDC_PROJECTION_DATABASE targets that canonical database after the T9 cutover.
CLICKHOUSE_URL="http://workpulse:workpulse@127.0.0.1:8124/workpulse_test"
export CLICKHOUSE_URL
# The CDC sink reads the real Debezium connector on kafka-test/Connect-test.
# `cdc-test-up` owns the connector; this run only consumes its topics.
CDC_PROJECTION_DATABASE=""
CDC_CONNECT_URL="http://localhost:8084"
CDC_SLOT_NAME="workpulse_cdc_test"
CDC_SINK_METRICS_PORT=0
export CDC_PROJECTION_DATABASE CDC_CONNECT_URL CDC_SLOT_NAME CDC_SINK_METRICS_PORT

if [[ ! -x "$PYTHON" ]]; then
  echo "full-hop: missing $PYTHON — run 'make install' first." >&2
  exit 1
fi

LOG_DIR="$(mktemp -d)"
RELAY_LOG="$LOG_DIR/relay.log"
REALTIME_LOG="$LOG_DIR/realtime.log"
PAYROLL_LOG="$LOG_DIR/payroll.log"
CDC_SINK_LOG="$LOG_DIR/cdc-sink.log"
RELAY_PID=""
REALTIME_PID=""
PAYROLL_PID=""
CDC_SINK_PID=""

# Ids-only worker logs: safe to surface when the hop misbehaves (a readiness
# bug otherwise reads as a product flake — R4).
dump_logs() {
  echo "full-hop: is kafka-test (:9093) up? (make check-e2e owns its startup)" >&2
  echo "full-hop: relay log (tail):" >&2
  tail -n 40 "$RELAY_LOG" >&2 || true
  echo "full-hop: realtime log (tail):" >&2
  tail -n 40 "$REALTIME_LOG" >&2 || true
  echo "full-hop: payroll log (tail):" >&2
  tail -n 40 "$PAYROLL_LOG" >&2 || true
  echo "full-hop: cdc sink log (tail):" >&2
  tail -n 40 "$CDC_SINK_LOG" >&2 || true
}

stop_worker() {
  local pid="$1"
  [[ -n "$pid" ]] || return 0
  kill -TERM "$pid" 2>/dev/null || return 0
  # Watchdog: KILL after ~5s if the graceful stop stalled; the `wait` returns
  # (and reaps) as soon as the worker exits either way.
  (sleep 5; kill -KILL "$pid" 2>/dev/null) &
  local watchdog=$!
  wait "$pid" 2>/dev/null || true
  kill "$watchdog" 2>/dev/null || true
  wait "$watchdog" 2>/dev/null || true
}

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  stop_worker "$CDC_SINK_PID"
  stop_worker "$PAYROLL_PID"
  stop_worker "$REALTIME_PID"
  stop_worker "$RELAY_PID"
  # Pre-flight guard failures have no worker logs (and no Kafka dependency to
  # diagnose); only dump the tails once a worker was actually launched.
  if ((status != 0)) && [[ -n "$RELAY_PID$REALTIME_PID$PAYROLL_PID$CDC_SINK_PID" ]]; then
    dump_logs
  fi
  rm -rf "$LOG_DIR"
  exit "$status"
}
trap cleanup EXIT

wait_for_line() {
  local name="$1" pid="$2" log="$3" pattern="$4"
  local deadline=$((SECONDS + 90))
  until grep -q "$pattern" "$log" 2>/dev/null; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "full-hop: $name exited before logging '${pattern}'" >&2
      exit 1
    fi
    if ((SECONDS >= deadline)); then
      echo "full-hop: timed out waiting for $name '${pattern}'" >&2
      exit 1
    fi
    sleep 0.2
  done
}

# The gate shares the dev PostgreSQL/Redis with `make dev-relay` /
# `make dev-realtime` / `make dev-payroll`. A concurrently running dev worker
# would race the gate for the same outbox rows or the same queued payroll runs
# (and can win the mark/claim) or deliver the fact over the same dev Redis (a
# false green). Refuse before starting our own workers; the check sees only
# pre-existing processes.
assert_no_other_workers() {
  local pids
  if ! command -v pgrep >/dev/null 2>&1; then
    echo "full-hop: 'pgrep' is required to check for concurrent workers." >&2
    exit 1
  fi
  pids="$(pgrep -f 'workers\.(relay|realtime|payroll|cdc_sink)\.main' || true)"
  if [[ -n "$pids" ]]; then
    echo "full-hop: another relay/realtime/payroll/cdc-sink worker is already running:" >&2
    echo "full-hop:   $(echo "$pids" | tr '\n' ' ')" >&2
    echo "full-hop: stop 'make dev-relay' / 'make dev-realtime' / 'make" >&2
    echo "full-hop: dev-payroll' / 'make dev-cdc-sink' (or" >&2
    echo "full-hop: a leaked worker from an earlier gate) before running the hop." >&2
    exit 1
  fi
}

# Playwright reuses an existing API server outside CI. That server keeps the
# ClickHouse URL it was started with, while this run's CDC sink writes the
# pinned test store — the report polls and UI would read a different database.
# Refuse before anything writes, so the gate can never false-green on stale
# dev rows or timeout against an empty dev projection.
assert_no_existing_api() {
  if "$PYTHON" -c 'import socket, sys
sock = socket.socket()
sock.settimeout(0.2)
sys.exit(0 if sock.connect_ex(("127.0.0.1", 8000)) == 0 else 1)'; then
    echo "full-hop: an API is already listening on :8000. Playwright would" >&2
    echo "full-hop: reuse it with a different ClickHouse URL than this run's" >&2
    echo "full-hop: CDC sink. Stop 'make dev-api' (or the stray" >&2
    echo "full-hop: uvicorn) before running the hop." >&2
    exit 1
  fi
}

# Historical unpublished rows would be republished to kafka-test and marked
# published in the dev database, and the live check-in would queue behind
# them — turning the <2s assertion into a backlog-drain measurement. Require a
# clean outbox; drain it with `make dev-relay` first.
assert_dev_outbox_is_clean() {
  local unpublished
  if ! unpublished="$(
    cd "$API_DIR" || exit 1
    PYTHONPATH="../..:." "$PYTHON" - <<'PY'
import asyncio

from sqlalchemy import func, select

from app.infrastructure.postgres.engine import (
    dispose_engine,
    get_session_factory,
)
from app.infrastructure.postgres.models_outbox import EventOutboxRow


async def count_unpublished() -> int:
    async with get_session_factory()() as session:
        stmt = (
            select(func.count())
            .select_from(EventOutboxRow)
            .where(EventOutboxRow.published_at.is_(None))
        )
        return (await session.execute(stmt)).scalar_one()


async def main() -> None:
    try:
        print(await count_unpublished())
    finally:
        await dispose_engine()


asyncio.run(main())
PY
  )"; then
    echo "full-hop: could not read the dev event_outbox (is the dev database up?)" >&2
    exit 1
  fi
  if [[ ! "$unpublished" =~ ^[0-9]+$ ]]; then
    echo "full-hop: unexpected outbox check output: '$unpublished'" >&2
    exit 1
  fi
  if ((unpublished != 0)); then
    echo "full-hop: $unpublished unpublished event_outbox row(s) in the dev database." >&2
    echo "full-hop: drain them with 'make dev-relay' first so the gate measures" >&2
    echo "full-hop: the live transition, not historical backlog drain." >&2
    exit 1
  fi
}

# The leave spec needs the seeded worker free to request a range covering the
# wall's UTC workday: an approved request is terminal (no cancel/delete API),
# so a repeat run would otherwise hit `409 leave_overlaps` on that date. Reset
# only the seeded worker's synthetic leave fixtures (`.ai/testing.md` rule 6 —
# synthetic data only). Types stay: the service provisions them per tenant.
reset_leave_fixtures() {
  if ! (
    cd "$API_DIR" || exit 1
    PYTHONPATH="../..:." "$PYTHON" - <<'PY'
import asyncio
from uuid import UUID

from sqlalchemy import delete

from app.domains.leave.models import LeaveBalance, LeaveRequest
from app.infrastructure.postgres.engine import (
    dispose_engine,
    get_session_factory,
)

WORKER_EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000021")


async def reset() -> None:
    async with get_session_factory()() as session:
        await session.execute(
            delete(LeaveRequest).where(
                LeaveRequest.employee_id == WORKER_EMPLOYEE_ID
            )
        )
        await session.execute(
            delete(LeaveBalance).where(
                LeaveBalance.employee_id == WORKER_EMPLOYEE_ID
            )
        )
        await session.commit()


async def main() -> None:
    try:
        await reset()
    finally:
        await dispose_engine()


asyncio.run(main())
PY
  ); then
    echo "full-hop: could not reset the seeded worker's leave fixtures" >&2
    exit 1
  fi
}

# The notification-inbox spec asserts an exact badge count, so it starts from a
# clean, deterministic inbox for the seeded worker: drop the worker's existing
# rows (and their delivery history) and insert three unread fixtures. The
# notification worker is deliberately NOT part of the hop — delivery is already
# proven at the worker level (spec decision 8), so these rows are the only
# inbox input. Synthetic data only (`.ai/testing.md` rule 6).
reset_notification_fixtures() {
  if ! (
    cd "$API_DIR" || exit 1
    PYTHONPATH="../..:." "$PYTHON" - <<'PY'
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select

from app.infrastructure.postgres.engine import (
    dispose_engine,
    get_session_factory,
)
from app.infrastructure.postgres.models_notifications import (
    NotificationDeliveryRow,
    NotificationRow,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKER_EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000021")
WORKER_USER_ID = UUID("00000000-0000-4000-8000-000000000013")

FIXTURES = (
    (
        "leave_approved",
        "E2E fixture: leave approved",
        "Your seeded leave request was approved.",
    ),
    (
        "leave_requested",
        "E2E fixture: leave request",
        "A leave request is waiting for a decision.",
    ),
    (
        "attendance_late",
        "E2E fixture: late check-in",
        "A late check-in was recorded for the seeded worker.",
    ),
)


async def reset() -> None:
    async with get_session_factory()() as session:
        recipient_rows = (
            select(NotificationRow.id)
            .where(NotificationRow.tenant_id == TENANT_ID)
            .where(
                NotificationRow.recipient_employee_id == WORKER_EMPLOYEE_ID
            )
        )
        await session.execute(
            delete(NotificationDeliveryRow).where(
                NotificationDeliveryRow.notification_id.in_(recipient_rows)
            )
        )
        await session.execute(
            delete(NotificationRow)
            .where(NotificationRow.tenant_id == TENANT_ID)
            .where(
                NotificationRow.recipient_employee_id == WORKER_EMPLOYEE_ID
            )
        )

        now = datetime.now(UTC)
        for index, (kind, subject, body) in enumerate(FIXTURES):
            session.add(
                NotificationRow(
                    tenant_id=TENANT_ID,
                    event_id=f"e2e-notification-inbox-{index + 1}",
                    event_type="e2e.fixture",
                    kind=kind,
                    recipient_employee_id=WORKER_EMPLOYEE_ID,
                    recipient_user_id=WORKER_USER_ID,
                    subject=subject,
                    body=body,
                    created_at=now - timedelta(minutes=index),
                )
            )
        await session.commit()


async def main() -> None:
    try:
        await reset()
    finally:
        await dispose_engine()


asyncio.run(main())
PY
  ); then
    echo "full-hop: could not reset the seeded worker's notification fixtures" >&2
    exit 1
  fi
}

# The payroll spec asserts an exact net, so it starts from a clean payroll
# ledger for the seeded tenant. The shared script is also called by the spec
# before its finance test, so a Playwright retry starts clean too. Synthetic
# dev data only (`.ai/testing.md` rule 6).
reset_payroll_fixtures() {
  if ! (
    cd "$API_DIR" || exit 1
    PYTHONPATH="../..:." "$PYTHON" scripts/reset_payroll_fixtures.py
  ); then
    echo "full-hop: could not reset the seeded tenant's payroll fixtures" >&2
    exit 1
  fi
}

# Overtime claims are terminal once approved or verified (no delete API), and
# the overtime spec needs a free work date while the payroll seam hop asserts
# exact overtime hours. Reset only the two seeded employees' rows. The shared
# script is also called by both specs before their flows, so a Playwright retry
# starts clean too. Synthetic dev data only (`.ai/testing.md` rule 6).
reset_overtime_fixtures() {
  if ! (
    cd "$API_DIR" || exit 1
    PYTHONPATH="../..:." "$PYTHON" scripts/reset_overtime_fixtures.py
  ); then
    echo "full-hop: could not reset the seeded overtime fixtures" >&2
    exit 1
  fi
}

# Recruitment rows and the employees a hire creates are mutable through the
# UI; a repeat run would collide on the unique candidate (tenant, job, email)
# key and the employees' email/code uniqueness. Reset the seeded tenant's
# jobs/candidates/interviews and the hires' employees. The shared script is
# also called by the spec before its flow, so a Playwright retry starts clean
# too. Synthetic dev data only (`.ai/testing.md` rule 6).
reset_recruitment_fixtures() {
  if ! (
    cd "$API_DIR" || exit 1
    PYTHONPATH="../..:." "$PYTHON" scripts/reset_recruitment_fixtures.py
  ); then
    echo "full-hop: could not reset the seeded recruitment fixtures" >&2
    exit 1
  fi
}

# Performance rows are mutable through the UI and the skill name is unique per
# tenant, so a repeat run would otherwise leave drafts, feedback rows and catalog
# names behind. The shared script also creates the deterministic peer login the
# feedback hop nominates (the seeds link only Wendy and Morgan), and the spec
# calls it again before its flow so a Playwright retry starts clean too.
# Synthetic dev data only (`.ai/testing.md` rule 6).
reset_performance_fixtures() {
  if ! (
    cd "$API_DIR" || exit 1
    PYTHONPATH="../..:." "$PYTHON" scripts/reset_performance_fixtures.py
  ); then
    echo "full-hop: could not reset the performance fixtures" >&2
    exit 1
  fi
}

# The reports spec asserts exact, labelled rows, so the OLTP fixture rows are
# written before the workers start and captured by real Debezium. The
# projection is truncated first and the synthetic People Ops employee uses
# fixed dates, so earlier specs and reruns cannot satisfy the assertions or
# leave a duplicate month-partitioned leave row. Synthetic data only
# (`.ai/testing.md` rule 6).
reset_reports_fixtures() {
  if ! (
    cd "$API_DIR" || exit 1
    env KAFKA_BOOTSTRAP_SERVERS="$BOOTSTRAP" PYTHONPATH="../..:." "$PYTHON" - <<'PY'
import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from aiokafka import AIOKafkaConsumer, TopicPartition
from sqlalchemy import delete

from app.config import get_settings
import app.domains.organizations.models  # noqa: F401 - registers FK targets
from app.domains.attendance.models import AttendanceRecord
from app.domains.employees.models import Employee, Employment
from app.domains.leave.models import LeaveRequest
from app.domains.overtime.models import OvertimeRequest
from app.infrastructure.clickhouse.client import create_clickhouse_client
from app.infrastructure.clickhouse.schema import SCHEMA_TABLES
from app.infrastructure.postgres.engine import (
    dispose_engine,
    get_session_factory,
)
from app.infrastructure.postgres.repositories.leave import (
    PostgresLeaveRepository,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
# A synthetic employee in People Ops, deliberately not the seeded worker that
# the overtime/payroll/leave specs write to. Fixed fact dates keep reruns on the
# same rows (including the month-partitioned fact_leave row) no matter when the
# gate runs; the e2e sets its report window to these dates.
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-00000000e2e1")
WORKER_USER_ID = UUID("00000000-0000-4000-8000-000000000013")
MANAGER_USER_ID = UUID("00000000-0000-4000-8000-000000000014")
HR_USER_ID = UUID("00000000-0000-4000-8000-000000000012")
DEPARTMENT_ID = UUID("00000000-0000-4000-8000-0000000d0005")  # People Ops
POSITION_ID = UUID("00000000-0000-4000-8000-0000000e0004")  # HR Specialist
LOCATION_ID = UUID("00000000-0000-4000-8000-0000000c0002")  # Berlin Office
EMPLOYMENT_ID = UUID("00000000-0000-4000-8000-00000000e2e2")
OVERTIME_ID = UUID("00000000-0000-4000-8000-0000000f0001")
ATTENDANCE_ID = UUID("00000000-0000-4000-8000-0000000a0001")
LEAVE_REQUEST_ID = UUID("00000000-0000-4000-8000-0000000b0001")
LEAVE_TYPE_TOPIC = "workpulse.cdc.leave_types"

OVERTIME_HOURS = Decimal("2.50")
HIRED_AT = date(2024, 1, 15)
WORK_DATE = date(2026, 6, 15)
LEAVE_START = date(2026, 6, 20)


def _truncate(client: Any) -> None:
    for table in SCHEMA_TABLES:
        client.command(f"TRUNCATE TABLE IF EXISTS {table}")


def reset_projection() -> None:
    """Clear the canonical CDC projection so this run proves itself.

    `clickhouse-test` survives across compose invocations, and a prior run's
    facts (including a month-partitioned leave row) can otherwise satisfy the
    non-empty checks or double-count. The sink applies the DDL afterward.
    """
    client = create_clickhouse_client()
    try:
        _truncate(client)
    finally:
        client.close()


async def reset_oltp_rows() -> UUID:
    """Write the OLTP rows real Debezium captures for the reports fixture.

    The captured columns carry the same ids, dates, hours and approved leave
    the spec asserts. Direct rows keep the API write path unchanged and avoid
    synthetic outbox events for data no endpoint created.
    """
    async with get_session_factory()() as session:
        # Delete children first: the synthetic employee may exist from a prior
        # run and the captured tables carry employee FKs.
        for model in (LeaveRequest, AttendanceRecord, OvertimeRequest, Employment):
            await session.execute(
                delete(model).where(
                    model.tenant_id == TENANT_ID,
                    model.employee_id == EMPLOYEE_ID,
                )
            )
        await session.execute(
            delete(Employee).where(
                Employee.tenant_id == TENANT_ID,
                Employee.id == EMPLOYEE_ID,
            )
        )

        # The leave fact needs its reference row; provisioning is idempotent
        # and gives the row a stable id across reruns.
        types = await PostgresLeaveRepository(session).ensure_leave_types(TENANT_ID)
        annual = next(item for item in types if str(item.code) == "annual")

        now = datetime.now(UTC)
        session.add(
            Employee(
                id=EMPLOYEE_ID,
                tenant_id=TENANT_ID,
                employee_code="EMP-E2E01",
                first_name="E2E",
                last_name="Projection",
                email="e2e.projection@workpulse.test",
                department_id=DEPARTMENT_ID,
                position_id=POSITION_ID,
                location_id=LOCATION_ID,
                employment_type="full_time",
                status="active",
                hired_at=HIRED_AT,
                terminated_at=None,
            )
        )
        # The session has no ORM relationships, so flush the parent before the
        # child rows to satisfy the captured tables' FKs deterministically.
        await session.flush()
        session.add(
            Employment(
                id=EMPLOYMENT_ID,
                tenant_id=TENANT_ID,
                employee_id=EMPLOYEE_ID,
                department_id=DEPARTMENT_ID,
                position_id=POSITION_ID,
                location_id=LOCATION_ID,
                employment_type="full_time",
                effective_from=HIRED_AT,
                effective_to=None,
            )
        )
        session.add(
            OvertimeRequest(
                id=OVERTIME_ID,
                tenant_id=TENANT_ID,
                employee_id=EMPLOYEE_ID,
                work_date=WORK_DATE,
                hours=OVERTIME_HOURS,
                status="hr_verified",
                approved_by=MANAGER_USER_ID,
                approved_at=now,
                verified_by=HR_USER_ID,
                verified_at=now,
            )
        )
        session.add(
            AttendanceRecord(
                id=ATTENDANCE_ID,
                tenant_id=TENANT_ID,
                employee_id=EMPLOYEE_ID,
                workday=WORK_DATE,
                checked_in_at=datetime(2026, 6, 15, 9, 0, tzinfo=UTC),
                checked_out_at=datetime(2026, 6, 15, 16, 30, tzinfo=UTC),
                work_mode="onsite",
                late=False,
                minutes_late=None,
                shift_id=None,
                duration_minutes=450,
            )
        )
        session.add(
            LeaveRequest(
                id=LEAVE_REQUEST_ID,
                tenant_id=TENANT_ID,
                employee_id=EMPLOYEE_ID,
                leave_type_id=annual.id,
                start_date=LEAVE_START,
                end_date=LEAVE_START + timedelta(days=1),
                days=2,
                status="approved",
                decided_by=MANAGER_USER_ID,
                decided_at=now,
            )
        )
        await session.commit()
        return annual.id


async def wait_for_leave_type_capture(leave_type_id: UUID) -> None:
    """Wait until Debezium has published the annual leave-type row.

    The sink warms its reference dictionary from the topic at startup; if the
    connector has not produced the row yet, a leave fact could land on the
    bounded `unknown` fallback. This bounded wait makes the fixture's ordering
    explicit instead of relying on a sleep.
    """
    settings = get_settings()
    consumer = AIOKafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=None,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    partition = TopicPartition(LEAVE_TYPE_TOPIC, 0)
    try:
        consumer.assign([partition])
        await consumer.seek_to_beginning(partition)
        deadline = asyncio.get_running_loop().time() + 60.0
        while asyncio.get_running_loop().time() < deadline:
            batches = await consumer.getmany(timeout_ms=500)
            for messages in batches.values():
                for message in messages:
                    try:
                        payload = json.loads(message.value or b"")
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    source = payload.get("source") or {}
                    if source.get("table") != "leave_types":
                        continue
                    row = payload.get("after") or payload.get("before") or {}
                    if (
                        row.get("id") == str(leave_type_id)
                        and row.get("code") == "annual"
                    ):
                        return
        raise SystemExit(
            "full-hop: Debezium did not publish the annual leave-type row"
        )
    finally:
        await consumer.stop()


async def run_fixture() -> None:
    try:
        annual_id = await reset_oltp_rows()
        await wait_for_leave_type_capture(annual_id)
    finally:
        await dispose_engine()


def main() -> None:
    # Truncation is synchronous (one HTTP round trip per table); the row
    # writes and the capture wait share one event loop.
    reset_projection()
    asyncio.run(run_fixture())


main()
PY
  ); then
    echo "full-hop: could not write the reports projection fixtures" >&2
    exit 1
  fi
}

assert_no_other_workers
assert_no_existing_api
assert_dev_outbox_is_clean
reset_leave_fixtures
reset_notification_fixtures
reset_payroll_fixtures
reset_overtime_fixtures
reset_recruitment_fixtures
reset_performance_fixtures
reset_reports_fixtures

# All workers run inside the apps/api uv environment, reading the same
# settings/.env as uvicorn; only the broker is overridden (kafka-test).
# Keep the interpreter/PYTHONPATH/module triple in sync with the Makefile's
# `dev-relay` / `dev-realtime` targets — same command, plus the `exec` that
# makes the recorded PID the python process itself.
(
  cd "$API_DIR"
  exec env KAFKA_BOOTSTRAP_SERVERS="$BOOTSTRAP" PYTHONPATH="../..:." \
    "$PYTHON" -m workers.relay.main
) >"$RELAY_LOG" 2>&1 &
RELAY_PID=$!

(
  cd "$API_DIR"
  exec env KAFKA_BOOTSTRAP_SERVERS="$BOOTSTRAP" PYTHONPATH="../..:." \
    "$PYTHON" -m workers.realtime.main
) >"$REALTIME_LOG" 2>&1 &
REALTIME_PID=$!

# The payroll worker is database-backed (not bus-backed): it polls QUEUED runs
# and commits calculation, payslips, the event envelope and audit together.
(
  cd "$API_DIR"
  exec env PYTHONPATH="../..:." "$PYTHON" -m workers.payroll.main
) >"$PAYROLL_LOG" 2>&1 &
PAYROLL_PID=$!

# The CDC sink consumes the real Debezium topics `cdc-test-up` registered on
# kafka-test and lands them in the canonical test database; the reset above
# wrote the fixture rows. Its readiness line is the first completed fetch round
# after the leave-type warm-up.
(
  cd "$API_DIR"
  exec env KAFKA_BOOTSTRAP_SERVERS="$BOOTSTRAP" CLICKHOUSE_URL="$CLICKHOUSE_URL" \
    CDC_PROJECTION_DATABASE="$CDC_PROJECTION_DATABASE" \
    CDC_CONNECT_URL="$CDC_CONNECT_URL" CDC_SLOT_NAME="$CDC_SLOT_NAME" \
    CDC_SINK_METRICS_PORT="$CDC_SINK_METRICS_PORT" \
    PYTHONPATH="../..:." "$PYTHON" -m workers.cdc_sink.main
) >"$CDC_SINK_LOG" 2>&1 &
CDC_SINK_PID=$!

wait_for_line relay "$RELAY_PID" "$RELAY_LOG" relay_started
wait_for_line realtime "$REALTIME_PID" "$REALTIME_LOG" realtime_consumer_started
wait_for_line payroll "$PAYROLL_PID" "$PAYROLL_LOG" payroll_worker_started
wait_for_line cdcsink "$CDC_SINK_PID" "$CDC_SINK_LOG" cdc_sink_started

cd "$ROOT_DIR"
npx playwright test
