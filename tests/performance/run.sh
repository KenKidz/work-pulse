#!/usr/bin/env bash
# Reliability load gate (Phase 7, ADR 0025).
#
# `make check-load` owns the database/Redis startup, migrations and seeding;
# this script owns the deterministic synthetic users, a private API process and
# the k6 smoke run. The gate loads the API only (no browser, no workers): 60s
# at 25 check-ins/s plus manager board reads, p95 < 300ms, zero errors.
#
# The API listens on a private port so a running `make dev` cannot be mistaken
# for the gate's server and so `make check` can run the e2e gate first without
# port churn. Fixtures are removed on exit (success or failure) — the dev
# outbox must be clean for the next `make check-e2e` and repeat runs must start
# from the same state.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
API_DIR="$ROOT_DIR/apps/api"
PYTHON="$API_DIR/.venv/bin/python"
PORT="${LOAD_API_PORT:-8010}"
USER_COUNT="${LOAD_USER_COUNT:-50}"
CHECK_IN_RATE="${LOAD_CHECK_IN_RATE:-25}"
DURATION="${LOAD_DURATION:-60s}"

if [[ ! -x "$PYTHON" ]]; then
  echo "check-load: missing $PYTHON — run 'make install' first." >&2
  exit 1
fi

if ! command -v k6 >/dev/null 2>&1; then
  echo "check-load: k6 is not on PATH." >&2
  echo "check-load: install it (https://grafana.com/docs/k6/latest/set-up/install-k6/)" >&2
  echo "check-load: or put the single binary on PATH; the gate fails loudly rather" >&2
  echo "check-load: than skipping the load SLO." >&2
  exit 1
fi

# A private API: refuse an occupied port instead of loading a different server.
if "$PYTHON" -c "import socket, sys
sock = socket.socket()
sock.settimeout(0.2)
sys.exit(0 if sock.connect_ex(('127.0.0.1', $PORT)) == 0 else 1)"; then
  echo "check-load: port $PORT is already in use; set LOAD_API_PORT to a free port." >&2
  exit 1
fi

LOG_DIR="$(mktemp -d)"
API_LOG="$LOG_DIR/api.log"
SESSIONS_FILE="$LOG_DIR/sessions.json"
API_PID=""

dump_logs() {
  echo "check-load: API log (tail):" >&2
  tail -n 60 "$API_LOG" >&2 || true
}

stop_api() {
  if [[ -z "$API_PID" ]]; then
    return 0
  fi
  kill -TERM "$API_PID" 2>/dev/null || return 0
  (sleep 5; kill -KILL "$API_PID" 2>/dev/null) &
  local watchdog=$!
  wait "$API_PID" 2>/dev/null || true
  kill "$watchdog" 2>/dev/null || true
  wait "$watchdog" 2>/dev/null || true
  API_PID=""
}

cleanup() {
  local status=$?
  local cleanup_status=0
  trap - EXIT
  set +e
  stop_api
  # The API is down: drop the synthetic attendance/outbox/session facts.
  (
    cd "$API_DIR" || exit 1
    LOAD_USER_COUNT="$USER_COUNT" PYTHONPATH="../..:." \
      "$PYTHON" scripts/reset_load_fixtures.py --cleanup
  ) || cleanup_status=$?
  if ((cleanup_status != 0)); then
    echo "check-load: fixture cleanup failed (status $cleanup_status)." >&2
    if ((status == 0)); then
      status=$cleanup_status
    fi
  fi
  if ((status != 0)); then
    dump_logs
  fi
  rm -rf "$LOG_DIR"
  exit "$status"
}
trap cleanup EXIT

wait_for_health() {
  local deadline=$((SECONDS + 60))
  until "$PYTHON" -c "import sys, urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:$PORT/healthz', timeout=1) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    sys.exit(1)"; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
      echo "check-load: API exited before /healthz was ready." >&2
      exit 1
    fi
    if ((SECONDS >= deadline)); then
      echo "check-load: timed out waiting for /healthz on :$PORT." >&2
      exit 1
    fi
    sleep 0.2
  done
}

echo "check-load: provisioning $USER_COUNT synthetic users and sessions"
(
  cd "$API_DIR" || exit 1
  LOAD_USER_COUNT="$USER_COUNT" LOAD_SESSIONS_FILE="$SESSIONS_FILE" \
    PYTHONPATH="../..:." \
    "$PYTHON" scripts/reset_load_fixtures.py
)

echo "check-load: starting API on :$PORT"
(
  cd "$API_DIR" || exit 1
  RATE_LIMIT_ENABLED=true \
  CIRCUIT_BREAKER_ENABLED=true \
  OTEL_EXPORTER_OTLP_ENDPOINT= \
  PYTHONPATH="../..:." \
    "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
      --log-level warning --no-access-log
) >"$API_LOG" 2>&1 &
API_PID=$!

wait_for_health

echo "check-load: running k6 smoke (${DURATION} at ${CHECK_IN_RATE} check-ins/s)"
LOAD_BASE_URL="http://127.0.0.1:$PORT" \
LOAD_USER_COUNT="$USER_COUNT" \
LOAD_SESSIONS_FILE="$SESSIONS_FILE" \
LOAD_CHECK_IN_RATE="$CHECK_IN_RATE" \
LOAD_DURATION="$DURATION" \
  k6 run "$ROOT_DIR/tests/performance/smoke.js"

echo "check-load: passed"
