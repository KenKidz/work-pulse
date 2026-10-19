#!/usr/bin/env bash
# E2E full-hop gate (realtime-consumer D2, ADR 0013 decision 4).
#
# The wall's live-delta spec is only meaningful when its only input is the
# bus, so the e2e gate runs the full hop around Playwright: the outbox relay
# and the realtime consumer against the throwaway kafka-test broker (:9093),
# while the dev PostgreSQL and Redis — the same services Playwright's uvicorn
# reads — keep serving the app. `make check-e2e` owns service startup,
# migrations and seeding; this script owns the two worker processes and
# exposes them as the Playwright run's surroundings.
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
RELAY_PID=""
REALTIME_PID=""

# Ids-only worker logs: safe to surface when the hop misbehaves (a readiness
# bug otherwise reads as a product flake — R4).
dump_logs() {
  echo "full-hop: is kafka-test (:9093) up? (make check-e2e owns its startup)" >&2
  echo "full-hop: relay log (tail):" >&2
  tail -n 40 "$RELAY_LOG" >&2 || true
  echo "full-hop: realtime log (tail):" >&2
  tail -n 40 "$REALTIME_LOG" >&2 || true
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
  stop_worker "$REALTIME_PID"
  stop_worker "$RELAY_PID"
  # Pre-flight guard failures have no worker logs (and no Kafka dependency to
  # diagnose); only dump the tails once a worker was actually launched.
  if ((status != 0)) && [[ -n "$RELAY_PID$REALTIME_PID" ]]; then
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
# `make dev-realtime`. A concurrently running dev worker would race the gate
# for the same outbox rows (the dev relay publishes to :9092 and can win the
# mark) or deliver the fact over the same dev Redis (a false green). Refuse
# before starting our own workers; the check sees only pre-existing processes.
assert_no_other_workers() {
  local pids
  if ! command -v pgrep >/dev/null 2>&1; then
    echo "full-hop: 'pgrep' is required to check for concurrent workers." >&2
    exit 1
  fi
  pids="$(pgrep -f 'workers\.(relay|realtime)\.main' || true)"
  if [[ -n "$pids" ]]; then
    echo "full-hop: another relay/realtime worker is already running:" >&2
    echo "full-hop:   $(echo "$pids" | tr '\n' ' ')" >&2
    echo "full-hop: stop 'make dev-relay' / 'make dev-realtime' (or a leaked" >&2
    echo "full-hop: worker from an earlier gate) before running the hop." >&2
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

assert_no_other_workers
assert_dev_outbox_is_clean

# Both workers run inside the apps/api uv environment, reading the same
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

wait_for_line relay "$RELAY_PID" "$RELAY_LOG" relay_started
wait_for_line realtime "$REALTIME_PID" "$REALTIME_LOG" realtime_consumer_started

cd "$ROOT_DIR"
npx playwright test
