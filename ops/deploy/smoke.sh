#!/usr/bin/env bash
#
# HTTP smoke for the deployed stack (deployment T6).
#
# Reaches the in-cluster API and web through kubectl port-forward (no ingress,
# TLS or DNS in scope) and proves the product path end to end:
#
#   seeded worker logs in -> any open record is closed -> one check-in ->
#   seeded HR user reads the board for that workday and sees the check-in.
#
# Also asserts /healthz (liveness) and /readyz (PostgreSQL readiness), and that
# the web image serves /login. Failure diagnostics name configuration keys
# only: strict-boot lines contain a key name, and the api's sole readiness
# dependency is PostgreSQL (`DATABASE_URL`). Raw logs are never dumped because
# driver errors can carry DSN text (`.ai/security.md`).
#
# Overridables: SMOKE_API_PORT (default 18000), SMOKE_WEB_PORT (default 13000),
# SMOKE_READY_TIMEOUT (default 180 seconds).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=ops/deploy/lib.sh
source "$ROOT/ops/deploy/lib.sh"

require_command kubectl curl jq

API_PORT="${SMOKE_API_PORT:-18000}"
WEB_PORT="${SMOKE_WEB_PORT:-13000}"
READY_TIMEOUT="${SMOKE_READY_TIMEOUT:-180}"
API_BASE="http://127.0.0.1:${API_PORT}"
WEB_BASE="http://127.0.0.1:${WEB_PORT}"

SEEDED_PASSWORD="workpulse-dev" # tracked dev seed identity (synthetic)
WORKER_EMAIL="worker@workpulse.test"
BOARD_EMAIL="hr@workpulse.test"

API_PF_PID=""
WEB_PF_PID=""
API_PF_LOG=""
WEB_PF_LOG=""
WORKER_JAR=""
BOARD_JAR=""

cleanup() {
  stop_port_forward "$API_PF_PID"
  stop_port_forward "$WEB_PF_PID"
  [ -z "$API_PF_LOG" ] || rm -f "$API_PF_LOG"
  [ -z "$WEB_PF_LOG" ] || rm -f "$WEB_PF_LOG"
  [ -z "$WORKER_JAR" ] || rm -f "$WORKER_JAR"
  [ -z "$BOARD_JAR" ] || rm -f "$BOARD_JAR"
}
trap cleanup EXIT

HTTP_STATUS=""
HTTP_BODY=""

do_request() {
  local method="$1" url="$2" jar="$3" data="${4:-}"
  local tmp
  tmp="$(mktemp)"
  if [ -n "$data" ]; then
    HTTP_STATUS="$(curl -sS -o "$tmp" -w '%{http_code}' \
      -b "$jar" -c "$jar" -X "$method" \
      -H 'Content-Type: application/json' --data "$data" "$url")"
  else
    HTTP_STATUS="$(curl -sS -o "$tmp" -w '%{http_code}' \
      -b "$jar" -c "$jar" -X "$method" "$url")"
  fi
  HTTP_BODY="$(cat "$tmp")"
  rm -f "$tmp"
}

expect_status() {
  local expected="$1" label="$2"
  if [ "$HTTP_STATUS" != "$expected" ]; then
    printf 'deploy: %s failed: HTTP %s (expected %s)\n' \
      "$label" "$HTTP_STATUS" "$expected" >&2
    printf 'deploy: response: %s\n' "$HTTP_BODY" >&2
    exit 1
  fi
}

log "waiting for the api and web Deployments"
if ! wait_deployment api "$READY_TIMEOUT"; then
  diagnose_api_not_ready
  exit 1
fi
if ! wait_deployment web "$READY_TIMEOUT"; then
  fail "deployment/web did not become Ready"
fi

log "port-forwarding svc/workpulse-api -> 127.0.0.1:$API_PORT"
API_PF_LOG="$(mktemp)"
start_port_forward workpulse-api "$API_PORT" 8000 "$API_PF_LOG"
API_PF_PID="$PORT_FORWARD_PID"

log "port-forwarding svc/workpulse-web -> 127.0.0.1:$WEB_PORT"
WEB_PF_LOG="$(mktemp)"
start_port_forward workpulse-web "$WEB_PORT" 3000 "$WEB_PF_LOG"
WEB_PF_PID="$PORT_FORWARD_PID"

wait_for_http "$API_BASE/healthz" "api /healthz" "$API_PF_PID" "$API_PF_LOG" 90
wait_for_http "$WEB_BASE/login" "web /login" "$WEB_PF_PID" "$WEB_PF_LOG" 90

log "checking /healthz and /readyz"
do_request GET "$API_BASE/healthz" /dev/null
expect_status 200 "GET /healthz"
jq -e '.status == "ok"' <<<"$HTTP_BODY" >/dev/null \
  || fail "/healthz did not report status=ok"

do_request GET "$API_BASE/readyz" /dev/null
expect_status 200 "GET /readyz"
jq -e '.status == "ready"' <<<"$HTTP_BODY" >/dev/null \
  || fail "/readyz did not report status=ready"

WORKER_JAR="$(mktemp)"
BOARD_JAR="$(mktemp)"

log "logging in as the seeded worker"
do_request POST "$API_BASE/api/v1/auth/login" "$WORKER_JAR" \
  "{\"email\":\"${WORKER_EMAIL}\",\"password\":\"${SEEDED_PASSWORD}\"}"
expect_status 200 "worker login"
WORKER_EMPLOYEE_ID="$(jq -r '.employee_id // empty' <<<"$HTTP_BODY")"
[ -n "$WORKER_EMPLOYEE_ID" ] \
  || fail "the worker login returned no linked employee_id"
log "worker authenticated (employee $WORKER_EMPLOYEE_ID)"

# A reused kind cluster can hold the previous smoke's open record; the API
# rejects a second open record for the employee, so close it first (the e2e
# suite uses the same recovery). Closing is idempotent across reruns.
log "closing any open attendance record for the worker"
do_request GET "$API_BASE/api/v1/attendance/records?page_size=100" "$WORKER_JAR"
expect_status 200 "list attendance records"
while IFS= read -r record_id; do
  [ -n "$record_id" ] || continue
  do_request POST "$API_BASE/api/v1/attendance/records/${record_id}/check-out" \
    "$WORKER_JAR" '{}'
  expect_status 200 "check-out ${record_id}"
done < <(jq -r '.items[] | select(.checked_out_at == null) | .id' <<<"$HTTP_BODY")

log "checking in"
do_request POST "$API_BASE/api/v1/attendance/check-ins" "$WORKER_JAR" \
  '{"work_mode":"onsite"}'
expect_status 201 "check-in"
WORKDAY="$(jq -r '.workday // empty' <<<"$HTTP_BODY")"
[ -n "$WORKDAY" ] || fail "the check-in response carried no workday"
log "check-in recorded for $WORKDAY"

log "reading the board as the seeded HR user"
do_request POST "$API_BASE/api/v1/auth/login" "$BOARD_JAR" \
  "{\"email\":\"${BOARD_EMAIL}\",\"password\":\"${SEEDED_PASSWORD}\"}"
expect_status 200 "board user login"
do_request GET "$API_BASE/api/v1/attendance/board?date=${WORKDAY}" "$BOARD_JAR"
expect_status 200 "GET /attendance/board"
jq -e --arg employee "$WORKER_EMPLOYEE_ID" \
  '(.activity[0]? // {}) | .employee_id == $employee and .action == "checked_in"' \
  <<<"$HTTP_BODY" >/dev/null \
  || fail "the new check-in is not the newest activity on the board"
jq -e '(.counts.present + .counts.remote) >= 1' <<<"$HTTP_BODY" >/dev/null \
  || fail "the board counts do not include the checked-in worker"

log "smoke passed: login -> check-in ($WORKDAY) -> board"
