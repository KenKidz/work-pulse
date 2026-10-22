#!/usr/bin/env bash
# E2E full-hop gate (realtime-consumer D2, ADR 0013 decision 4).
#
# The wall's live-delta spec is only meaningful when its only input is the
# bus, so the e2e gate runs the full hop around Playwright: the outbox relay,
# the realtime consumer and the payroll calculation worker against the
# throwaway kafka-test broker (:9093), while the dev PostgreSQL and Redis — the
# same services Playwright's uvicorn reads — keep serving the app. `make
# check-e2e` owns service startup, migrations and seeding; this script owns the
# worker processes and exposes them as the Playwright run's surroundings.
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

if [[ ! -x "$PYTHON" ]]; then
  echo "full-hop: missing $PYTHON — run 'make install' first." >&2
  exit 1
fi

LOG_DIR="$(mktemp -d)"
RELAY_LOG="$LOG_DIR/relay.log"
REALTIME_LOG="$LOG_DIR/realtime.log"
PAYROLL_LOG="$LOG_DIR/payroll.log"
RELAY_PID=""
REALTIME_PID=""
PAYROLL_PID=""

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
  stop_worker "$PAYROLL_PID"
  stop_worker "$REALTIME_PID"
  stop_worker "$RELAY_PID"
  # Pre-flight guard failures have no worker logs (and no Kafka dependency to
  # diagnose); only dump the tails once a worker was actually launched.
  if ((status != 0)) && [[ -n "$RELAY_PID$REALTIME_PID$PAYROLL_PID" ]]; then
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
  pids="$(pgrep -f 'workers\.(relay|realtime|payroll)\.main' || true)"
  if [[ -n "$pids" ]]; then
    echo "full-hop: another relay/realtime/payroll worker is already running:" >&2
    echo "full-hop:   $(echo "$pids" | tr '\n' ' ')" >&2
    echo "full-hop: stop 'make dev-relay' / 'make dev-realtime' / 'make" >&2
    echo "full-hop: dev-payroll' (or a leaked worker from an earlier gate)" >&2
    echo "full-hop: before running the hop." >&2
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

assert_no_other_workers
assert_dev_outbox_is_clean
reset_leave_fixtures
reset_notification_fixtures
reset_payroll_fixtures

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

wait_for_line relay "$RELAY_PID" "$RELAY_LOG" relay_started
wait_for_line realtime "$REALTIME_PID" "$REALTIME_LOG" realtime_consumer_started
wait_for_line payroll "$PAYROLL_PID" "$PAYROLL_LOG" payroll_worker_started

cd "$ROOT_DIR"
npx playwright test
