#!/usr/bin/env bash
#
# Shared helpers for the kind deploy proof (deployment T6).
#
# Sourced by `deploy.sh`, `seed.sh`, `smoke.sh` and `scenarios.sh`. Every
# kubectl call carries an explicit context so a developer's current context can
# never redirect the proof at another cluster. Values loaded from the untracked
# `values.env` are never echoed (`.ai/security.md`).

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

CLUSTER_NAME="${CLUSTER_NAME:-workpulse}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-${CLUSTER_NAME}}"
# The manifests hard-code this namespace (ops/deploy/k8s/), so it is not an
# operator override; rendering a different namespace would require rewriting
# every manifest.
K8S_NAMESPACE="workpulse"
IMAGE_TAG="${IMAGE_TAG:-sha-$(git -C "$ROOT" rev-parse --short HEAD)}"
API_IMAGE="${API_IMAGE:-workpulse-api:${IMAGE_TAG}}"
WEB_IMAGE="${WEB_IMAGE:-workpulse-web:${IMAGE_TAG}}"

#: The app workloads that must each run exactly one replica (ADR 0025).
APP_DEPLOYMENTS=(api web relay realtime notifications payroll cdc-sink)
#: Defaults; callers override per step.
DEPLOY_WAIT_TIMEOUT="${DEPLOY_WAIT_TIMEOUT:-600}"
DEPLOY_MIGRATION_TIMEOUT="${DEPLOY_MIGRATION_TIMEOUT:-600}"

VALUES_FILE="${DEPLOY_VALUES_FILE:-$ROOT/ops/deploy/values.env}"

PORT_FORWARD_PID=""

log() {
  printf 'deploy: %s\n' "$*"
}

fail() {
  printf 'deploy: error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  local name
  for name in "$@"; do
    command -v "$name" >/dev/null 2>&1 || fail "'$name' is required"
  done
}

kube() {
  kubectl --context "$KUBE_CONTEXT" -n "$K8S_NAMESPACE" "$@"
}

# Render a manifest with the sha-<commit> image tags the Makefile built. The
# tracked manifests carry `latest` only so a manual `kubectl apply` works after
# a plain `make images`; the proof always pins the commit tag.
apply_manifest() {
  local file="$1"
  sed \
    -e "s|workpulse-api:latest|${API_IMAGE}|g" \
    -e "s|workpulse-web:latest|${WEB_IMAGE}|g" \
    "$file" | kube apply -f -
}

apply_dir() {
  local dir="$1" file
  for file in "$dir"/*.yaml; do
    [ -f "$file" ] || continue
    apply_manifest "$file"
  done
}

delete_job() {
  kube delete job "$1" --ignore-not-found --wait=true >/dev/null
}

print_workload_state() {
  local name="$1"
  printf 'deploy: pod state for %s:\n' "$name" >&2
  kube get pods -l "app.kubernetes.io/name=$name" -o wide >&2 || true
}

wait_deployment() {
  local name="$1" timeout="${2:-$DEPLOY_WAIT_TIMEOUT}"
  if ! kube rollout status "deployment/$name" --timeout="${timeout}s"; then
    print_workload_state "$name"
    return 1
  fi
}

wait_statefulset() {
  local name="$1" timeout="${2:-$DEPLOY_WAIT_TIMEOUT}"
  if ! kube rollout status "statefulset/$name" --timeout="${timeout}s"; then
    print_workload_state "$name"
    return 1
  fi
}

# Wait for a one-shot Job without `kubectl wait`'s blind timeout: a Job that
# fails fast (backoffLimit 0) must fail the deploy just as fast, not after the
# whole timeout. Returns 1 on Failed or timeout; the caller prints the state.
wait_job() {
  local name="$1" timeout="${2:-$DEPLOY_WAIT_TIMEOUT}"
  local deadline=$((SECONDS + timeout))
  local payload complete failed
  while true; do
    payload="$(kube get job "$name" -o json 2>/dev/null || true)"
    if [ -n "$payload" ]; then
      complete="$(jq -r '[.status.conditions[]? | select(.type == "Complete" and .status == "True")] | length' <<<"$payload")"
      failed="$(jq -r '[.status.conditions[]? | select(.type == "Failed" and .status == "True")] | length' <<<"$payload")"
      [ "$complete" = "1" ] && return 0
      [ "$failed" = "1" ] && return 1
    fi
    if ((SECONDS >= deadline)); then
      return 1
    fi
    sleep 2
  done
}

print_job_failure() {
  local name="$1" payload
  printf 'deploy: Job %s failed\n' "$name" >&2
  payload="$(kube get job "$name" -o json 2>/dev/null || true)"
  if [ -n "$payload" ]; then
    jq -r '.status.conditions[]? | "deploy: job condition \(.type)=\(.status)\(if .reason then " (\(.reason))" else "" end)"' \
      <<<"$payload" >&2 || true
  fi
  printf 'deploy: Job pod state:\n' >&2
  kube get pods -l "app.kubernetes.io/name=$name" -o wide >&2 || true
  kube get pods -l "app.kubernetes.io/name=$name" -o json 2>/dev/null \
    | jq -r '.items[]? | "deploy: pod \(.metadata.name) phase=\(.status.phase // "unknown") \(([.status.containerStatuses[]? | if .state.terminated then "exit=\(.state.terminated.exitCode)" elif .state.waiting then "waiting=\(.state.waiting.reason // "unknown")" else empty end] | join(" ")))"' \
      >&2 || true
}

# The api's only readiness dependency is PostgreSQL, so a non-Ready api names
# DATABASE_URL and nothing else. A missing-key strict-boot line is surfaced
# verbatim (it contains a key name only); raw logs are never printed because
# driver errors can carry DSN text (`.ai/security.md`).
diagnose_api_not_ready() {
  printf 'deploy: api did not become Ready; pod state:\n' >&2
  kube get pods -l app.kubernetes.io/name=api -o wide >&2 || true
  local missing phases
  missing="$(kube logs -l app.kubernetes.io/name=api --tail=200 2>/dev/null \
    | grep -m1 -F 'missing configuration:' || true)"
  if [ -n "$missing" ]; then
    printf 'deploy: %s\n' "$missing" >&2
    printf 'deploy: apply the workpulse-config Secret before the app workloads (ops/deploy/README.md).\n' >&2
    return 0
  fi
  phases="$(kube get pods -l app.kubernetes.io/name=api \
    -o jsonpath='{.items[*].status.phase}' 2>/dev/null || true)"
  case "$phases" in
    *Running*)
      printf 'deploy: GET /readyz is failing: verify the DATABASE_URL key in the workpulse-config Secret.\n' >&2
      printf 'deploy: Secret values are never printed.\n' >&2
      ;;
    *)
      printf 'deploy: the api pods are not Running (see pod state above).\n' >&2
      ;;
  esac
}

assert_app_single_replica() {
  local name desired ready total
  for name in "${APP_DEPLOYMENTS[@]}"; do
    desired="$(kube get deployment "$name" -o jsonpath='{.spec.replicas}')"
    ready="$(kube get deployment "$name" -o jsonpath='{.status.readyReplicas}')"
    total="$(kube get deployment "$name" -o jsonpath='{.status.replicas}')"
    [ "$desired" = "1" ] || fail "deployment/$name spec.replicas=${desired:-missing} — ADR 0025 requires exactly 1"
    [ "$ready" = "1" ] || fail "deployment/$name readyReplicas=${ready:-0} — ADR 0025 requires exactly 1"
    [ "$total" = "1" ] || fail "deployment/$name status.replicas=${total:-0} — ADR 0025 requires exactly 1"
  done
  log "single replica confirmed from cluster state: ${APP_DEPLOYMENTS[*]}"
}

assert_images_exist() {
  local image
  for image in "$API_IMAGE" "$WEB_IMAGE"; do
    docker image inspect "$image" >/dev/null 2>&1 \
      || fail "image $image not found — run 'make images' first"
  done
}

ensure_kind_cluster() {
  if kind get clusters | grep -Fxq "$CLUSTER_NAME"; then
    log "reusing kind cluster '$CLUSTER_NAME'"
  else
    log "creating kind cluster '$CLUSTER_NAME'"
    kind create cluster --name "$CLUSTER_NAME" --wait 180s
  fi
}

load_images() {
  log "loading $API_IMAGE and $WEB_IMAGE into kind/$CLUSTER_NAME"
  kind load docker-image --name "$CLUSTER_NAME" "$API_IMAGE" "$WEB_IMAGE"
}

random_hex() {
  local bytes="$1"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$bytes"
  else
    head -c "$bytes" /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

# First run only: generate URL-safe credentials outside git. Reusing the file
# across redeploys keeps the Secret and the Postgres/ClickHouse PVC contents in
# agreement; delete both with `make deploy-down` when rotating from scratch.
generate_values_file() {
  local mode
  mode="$(umask)"
  umask 077
  mkdir -p "$(dirname "$VALUES_FILE")"
  local postgres_password clickhouse_password access_key secret_key
  postgres_password="$(random_hex 24)"
  clickhouse_password="$(random_hex 24)"
  access_key="wp$(random_hex 8)"
  secret_key="$(random_hex 24)"
  {
    printf 'DATABASE_URL=postgresql+asyncpg://workpulse:%s@postgres:5432/workpulse\n' "$postgres_password"
    printf 'REDIS_URL=redis://redis:6379/0\n'
    printf 'KAFKA_BOOTSTRAP_SERVERS=kafka:9092\n'
    printf 'CLICKHOUSE_URL=http://workpulse:%s@clickhouse:8123/workpulse\n' "$clickhouse_password"
    printf 'STORAGE_ENDPOINT=http://minio:9000\n'
    printf 'STORAGE_ACCESS_KEY=%s\n' "$access_key"
    printf 'STORAGE_SECRET_KEY=%s\n' "$secret_key"
    printf 'STORAGE_BUCKET=documents\n'
    printf 'POSTGRES_PASSWORD=%s\n' "$postgres_password"
    printf 'CLICKHOUSE_PASSWORD=%s\n' "$clickhouse_password"
  } >"$VALUES_FILE"
  umask "$mode"
  log "generated $VALUES_FILE (untracked, mode 600)"
}

load_values() {
  [ -f "$VALUES_FILE" ] || generate_values_file

  local rel="${VALUES_FILE#"$ROOT"/}"
  if [ "$rel" != "$VALUES_FILE" ] \
    && git -C "$ROOT" ls-files --error-unmatch -- "$rel" >/dev/null 2>&1; then
    fail "$VALUES_FILE is git-tracked; secret values must stay out of git"
  fi

  set -a
  # shellcheck disable=SC1090 # operator-provided values file path
  source "$VALUES_FILE"
  set +a

  local key
  for key in DATABASE_URL REDIS_URL KAFKA_BOOTSTRAP_SERVERS CLICKHOUSE_URL \
    STORAGE_ENDPOINT STORAGE_ACCESS_KEY STORAGE_SECRET_KEY STORAGE_BUCKET \
    POSTGRES_PASSWORD CLICKHOUSE_PASSWORD; do
    [ -n "${!key:-}" ] || fail "$VALUES_FILE is missing $key"
  done
}

apply_secret() {
  kube create secret generic workpulse-config \
    --from-env-file="$VALUES_FILE" \
    --dry-run=client -o yaml | kube apply -f -
}

# Start a port-forward and record its PID in PORT_FORWARD_PID. The caller owns
# the PID and must stop it; `start_port_forward` runs in the caller's shell so
# the variable is not lost to a command-substitution subshell.
# shellcheck disable=SC2034 # PORT_FORWARD_PID is read by the sourcing script
start_port_forward() {
  local service="$1" local_port="$2" remote_port="$3" log_file="$4"
  # `exec` inside the subshell makes $! the kubectl process itself. Without it
  # $! is the wrapper subshell and killing it can strand the real port-forward
  # (and the local port) for the next run.
  (
    exec kubectl --context "$KUBE_CONTEXT" -n "$K8S_NAMESPACE" \
      port-forward "svc/$service" "${local_port}:${remote_port}" \
      --address 127.0.0.1
  ) >"$log_file" 2>&1 &
  PORT_FORWARD_PID=$!
}

stop_port_forward() {
  local pid="${1:-}"
  [ -n "$pid" ] || return 0
  kill "$pid" 2>/dev/null || return 0
  wait "$pid" 2>/dev/null || true
}

wait_for_http() {
  local url="$1" label="$2" pid="$3" log_file="$4" timeout="${5:-120}"
  local deadline=$((SECONDS + timeout))
  until curl --connect-timeout 1 --max-time 5 -fsS -o /dev/null "$url" 2>/dev/null; do
    if ! kill -0 "$pid" 2>/dev/null; then
      printf 'deploy: port-forward for %s exited:\n' "$label" >&2
      cat "$log_file" >&2 || true
      return 1
    fi
    if ((SECONDS >= deadline)); then
      printf 'deploy: timed out waiting for %s (%s)\n' "$label" "$url" >&2
      cat "$log_file" >&2 || true
      return 1
    fi
    sleep 1
  done
}
