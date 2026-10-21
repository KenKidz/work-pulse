#!/usr/bin/env bash
# scripts/dev.sh — run the whole WorkPulse stack for browser testing.
#
# `make dev` is the one command that runs everything: the Makefile owns service
# startup (PostgreSQL :5434, Redis, Kafka), Alembic migrations and seeding,
# then hands the six long-running processes to this script — the FastAPI dev
# server (:8000), the Next.js dev server (:3000), the outbox relay, the
# realtime consumer, the notifications consumer and the payroll worker
# (ADR 0012 / 0013 / 0015 / payroll T8).
#
# Conventions (shared with tests/e2e/full-hop.sh):
# - Readiness is deterministic, never sleeps: TCP accept for the two HTTP
#   servers, the workers' pinned `*_started` log lines.
# - The recorded PID is the process itself (`exec` in the launch subshell), and
#   teardown is a bounded SIGTERM with a KILL fallback, applied recursively to
#   process trees (pnpm and uvicorn --reload both spawn children).
# - Keep the interpreter/PYTHONPATH/module triples below in sync with the
#   Makefile's `dev-api` / `dev-relay` / `dev-realtime` / `dev-notifications` /
#   `dev-payroll` targets — same commands.
# - A process dying on its own stops the rest, with log tails on stderr.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT_DIR/apps/api"
WEB_DIR="$ROOT_DIR/apps/web"
# `uv run python` resolves to exactly this interpreter — used directly so the
# recorded PID is the process itself (no wrapper to forward signals through).
PYTHON="$API_DIR/.venv/bin/python"

LOG_DIR="$(mktemp -d)"
PIDS=()
NAMES=()

# --- output helpers ---------------------------------------------------------

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'
  C_API=$'\033[36m'
  C_WEB=$'\033[32m'
  C_RELAY=$'\033[33m'
  C_REALTIME=$'\033[35m'
  C_NOTIFY=$'\033[34m'
  C_PAYROLL=$'\033[36m'
  C_BAD=$'\033[31m'
else
  C_RESET='' C_API='' C_WEB='' C_RELAY='' C_REALTIME='' C_NOTIFY='' C_PAYROLL='' C_BAD=''
fi

tag_color() {
  case "$1" in
    api) printf '%s' "$C_API" ;;
    web) printf '%s' "$C_WEB" ;;
    relay) printf '%s' "$C_RELAY" ;;
    realtime) printf '%s' "$C_REALTIME" ;;
    notify) printf '%s' "$C_NOTIFY" ;;
    payroll) printf '%s' "$C_PAYROLL" ;;
    *) printf '%s' "$C_RESET" ;;
  esac
}

# Stream lines to the terminal behind a name tag and mirror them to a per-
# process log file (the log is what the readiness waits grep).
tag_lines() {
  local name="$1" log="$2" line
  local color
  color="$(tag_color "$name")"
  while IFS= read -r line; do
    printf '%b[%-8s]%b %s\n' "$color" "$name" "$C_RESET" "$line"
    printf '%s\n' "$line" >>"$log"
  done
}

# --- pre-flight -------------------------------------------------------------

port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

preflight() {
  if [[ ! -x "$PYTHON" ]]; then
    echo "dev: missing $PYTHON — run 'make install' first." >&2
    exit 1
  fi
  if [[ ! -f "$API_DIR/.env" ]]; then
    echo "dev: missing apps/api/.env — 'cp .env.example apps/api/.env' first." >&2
    exit 1
  fi
  if ! command -v pnpm >/dev/null 2>&1; then
    echo "dev: 'pnpm' is required to run the web dev server." >&2
    exit 1
  fi
  if ! command -v pgrep >/dev/null 2>&1; then
    echo "dev: 'pgrep' is required to manage the process trees." >&2
    exit 1
  fi
  local port
  for port in 3000 8000; do
    if port_in_use "$port"; then
      echo "dev: port :$port is already in use — stop whatever is listening" >&2
      echo "dev: there (the browser entry point is http://localhost:3000 and" >&2
      echo "dev: CORS only allows that origin) and run 'make dev' again." >&2
      exit 1
    fi
  done
}

# --- process management -----------------------------------------------------

start() {
  local name="$1" dir="$2"
  shift 2
  local log="$LOG_DIR/$name.log"
  : >"$log"
  (
    cd "$dir"
    exec "$@"
  ) > >(tag_lines "$name" "$log") 2>&1 &
  PIDS+=("$!")
  NAMES+=("$name")
}

# Recursive teardown: children first, so a wrapper (pnpm, the uvicorn reloader)
# never outlives the process that actually serves traffic.
kill_tree() {
  local pid="$1" sig="$2" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$child" "$sig"
  done
  kill "-$sig" "$pid" 2>/dev/null || true
}

dump_tails() {
  local i
  for i in "${!NAMES[@]}"; do
    echo "dev: ${NAMES[$i]} log (tail):" >&2
    tail -n 30 "$LOG_DIR/${NAMES[$i]}.log" >&2 || true
  done
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  set +e
  if ((${#PIDS[@]} > 0)); then
    local i watchdog
    for i in "${!PIDS[@]}"; do
      kill_tree "${PIDS[$i]}" TERM
    done
    # Watchdog: KILL after ~5s if a graceful stop stalls; the `wait` below
    # returns (and reaps) as soon as each process exits either way.
    (
      sleep 5
      for i in "${!PIDS[@]}"; do
        kill_tree "${PIDS[$i]}" KILL
      done
    ) &
    watchdog=$!
    for i in "${!PIDS[@]}"; do
      wait "${PIDS[$i]}" 2>/dev/null
    done
    kill "$watchdog" 2>/dev/null
    wait "$watchdog" 2>/dev/null
  fi
  if ((status != 0)) && ((${#PIDS[@]} > 0)); then
    dump_tails
  fi
  rm -rf "$LOG_DIR"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- readiness (deterministic, never sleeps) --------------------------------

wait_for_line() {
  local name="$1" pid="$2" log="$3" pattern="$4"
  local deadline=$((SECONDS + 90))
  until grep -q "$pattern" "$log" 2>/dev/null; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "dev: $name exited before logging '${pattern}'" >&2
      exit 1
    fi
    if ((SECONDS >= deadline)); then
      echo "dev: timed out waiting for $name '${pattern}'" >&2
      exit 1
    fi
    sleep 0.2
  done
}

wait_for_port() {
  local name="$1" port="$2" pid="$3"
  local deadline=$((SECONDS + 120))
  until port_in_use "$port"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "dev: $name exited before listening on :$port" >&2
      exit 1
    fi
    if ((SECONDS >= deadline)); then
      echo "dev: timed out waiting for $name on :$port" >&2
      exit 1
    fi
    sleep 0.3
  done
}

# --- run --------------------------------------------------------------------

preflight

# Same commands as the Makefile's dev-* targets (see the header comment).
start api "$API_DIR" \
  "$PYTHON" -m uvicorn app.main:app --reload --port 8000
start web "$WEB_DIR" \
  pnpm run dev
start relay "$API_DIR" \
  env PYTHONPATH="../..:." "$PYTHON" -m workers.relay.main
start realtime "$API_DIR" \
  env PYTHONPATH="../..:." "$PYTHON" -m workers.realtime.main
start notify "$API_DIR" \
  env PYTHONPATH="../..:." "$PYTHON" -m workers.notifications.main
start payroll "$API_DIR" \
  env PYTHONPATH="../..:." "$PYTHON" -m workers.payroll.main

wait_for_line relay "${PIDS[2]}" "$LOG_DIR/relay.log" relay_started
wait_for_line realtime "${PIDS[3]}" "$LOG_DIR/realtime.log" realtime_consumer_started
wait_for_line notify "${PIDS[4]}" "$LOG_DIR/notify.log" notifications_consumer_started
wait_for_line payroll "${PIDS[5]}" "$LOG_DIR/payroll.log" payroll_worker_started
wait_for_port api 8000 "${PIDS[0]}"
wait_for_port web 3000 "${PIDS[1]}"

echo "dev: everything is up"
echo "  web       http://localhost:3000        <- open this in your browser"
echo "  api       http://localhost:8000        (docs at /docs)"
echo "  relay     outbox -> Kafka (workpulse.events)"
echo "  realtime  Kafka -> live wall (Redis fan-out)"
echo "  notify    Kafka -> email/Slack deliveries"
echo "  payroll   queued payroll runs -> calculated results"
echo "dev: Ctrl+C stops everything"

# Supervise: the first process to die stops the run (with its log tail in the
# teardown dump), instead of leaving a half-stack behind.
set +e
while true; do
  wait -n "${PIDS[@]}"
  status=$?
  for i in "${!PIDS[@]}"; do
    if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
      echo "dev: ${NAMES[$i]} exited (status $status) — stopping everything" >&2
      exit 1
    fi
  done
done
