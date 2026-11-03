#!/usr/bin/env bash
#
# Deploy proof ordering gate (deployment T6).
#
# Creates or reuses a kind cluster and deploys the full stack in the one order
# that makes the migration a gate rather than a readiness detail:
#
#   namespace + Secret + ConfigMap -> infra (waited Ready) ->
#   migration Job (waited Complete) -> seed -> CDC connector ->
#   app workloads (waited Ready) -> smoke
#
# A failing migration exits before any app workload is applied. Rollback is
# image rollback only (`DEPLOY_SKIP_MIGRATION=1`): the schema is already at or
# ahead of the previous image, so it is never downgraded (business rule 4).
#
# Run through `make deploy-check` (after `make images`). Overridables:
#   CLUSTER_NAME  KUBE_CONTEXT  IMAGE_TAG/API_IMAGE/WEB_IMAGE
#   DEPLOY_VALUES_FILE  MIGRATION_JOB_FILE  DEPLOY_SKIP_SECRET=1
#   DEPLOY_SKIP_MIGRATION=1 (rollback only)  DEPLOY_SKIP_SMOKE=1
#   DEPLOY_WAIT_TIMEOUT  DEPLOY_MIGRATION_TIMEOUT
#   CDC_CONNECT_LOCAL_PORT (default 18083)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=ops/deploy/lib.sh
source "$ROOT/ops/deploy/lib.sh"

require_command docker kind kubectl curl jq

MIGRATION_JOB_FILE="${MIGRATION_JOB_FILE:-$ROOT/ops/deploy/k8s/migration-job.yaml}"
#: The in-cluster infra the proof waits on before the migration gate.
INFRA_STATEFULSETS=(postgres redis kafka clickhouse minio)

cleanup() {
  stop_port_forward "${CDC_FORWARD_PID:-}"
  [ -z "${CDC_FORWARD_LOG:-}" ] || rm -f "$CDC_FORWARD_LOG"
}
trap cleanup EXIT

assert_images_exist
load_values

ensure_kind_cluster
load_images

log "applying namespace, Secret and ConfigMap"
apply_manifest "$ROOT/ops/deploy/k8s/namespace.yaml"
if [ "${DEPLOY_SKIP_SECRET:-0}" = "1" ]; then
  log "DEPLOY_SKIP_SECRET=1: not applying workpulse-config"
else
  apply_secret
fi
apply_manifest "$ROOT/ops/deploy/k8s/configmap.yaml"

log "applying infrastructure"
# One-shot Jobs are immutable: delete before re-applying so an upgrade can
# substitute a new image tag. The bucket step is idempotent, and a failed
# migration Job remains inspectable only until the next deploy attempt.
delete_job minio-init
apply_dir "$ROOT/ops/deploy/k8s/infra"

log "waiting for infrastructure (stateful services, bucket, Connect)"
for statefulset in "${INFRA_STATEFULSETS[@]}"; do
  wait_statefulset "$statefulset"
done
if ! wait_job minio-init "$DEPLOY_WAIT_TIMEOUT"; then
  print_job_failure minio-init
  fail "the minio-init Job did not complete"
fi
wait_deployment kafka-connect

if [ "${DEPLOY_SKIP_MIGRATION:-0}" = "1" ]; then
  # Rollback only: the schema is already at or ahead of the previous image.
  # Running that image's `alembic upgrade head` would fail because the database
  # is stamped with a revision the older image does not know.
  log "DEPLOY_SKIP_MIGRATION=1: skipping the migration gate (rollback to an already-migrated schema)"
else
  log "applying the migration Job (gate: apps wait for it)"
  delete_job workpulse-migrate
  apply_manifest "$MIGRATION_JOB_FILE"
  if ! wait_job workpulse-migrate "$DEPLOY_MIGRATION_TIMEOUT"; then
    print_job_failure workpulse-migrate
    fail "the migration Job did not complete; app workloads were NOT applied"
  fi
  log "migration Job completed"
fi

log "seeding the database"
bash "$ROOT/ops/deploy/seed.sh"

log "registering the CDC connector against the in-cluster Kafka Connect"
CDC_CONNECT_LOCAL_PORT="${CDC_CONNECT_LOCAL_PORT:-18083}"
CDC_FORWARD_LOG="$(mktemp)"
start_port_forward kafka-connect "$CDC_CONNECT_LOCAL_PORT" 8083 "$CDC_FORWARD_LOG"
CDC_FORWARD_PID="$PORT_FORWARD_PID"
if ! wait_for_http "http://127.0.0.1:${CDC_CONNECT_LOCAL_PORT}/" \
  "kafka-connect" "$CDC_FORWARD_PID" "$CDC_FORWARD_LOG" 60; then
  fail "Kafka Connect REST did not become reachable"
fi
CONNECT_URL="http://127.0.0.1:${CDC_CONNECT_LOCAL_PORT}" \
  CDC_POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}" \
  CDC_FENCE_ENABLED=0 \
  bash "$ROOT/ops/cdc/register.sh"
stop_port_forward "$CDC_FORWARD_PID"
CDC_FORWARD_PID=""

log "applying app workloads (exactly one replica each, ADR 0025)"
apply_dir "$ROOT/ops/deploy/k8s/apps"

log "waiting for app workloads"
for deployment in "${APP_DEPLOYMENTS[@]}"; do
  if ! wait_deployment "$deployment" "$DEPLOY_WAIT_TIMEOUT"; then
    if [ "$deployment" = "api" ]; then
      diagnose_api_not_ready
    fi
    fail "deployment/$deployment did not become Ready"
  fi
done
assert_app_single_replica

if [ "${DEPLOY_SKIP_SMOKE:-0}" = "1" ]; then
  log "DEPLOY_SKIP_SMOKE=1: skipping the HTTP smoke"
else
  log "running the HTTP smoke (login -> check-in -> board)"
  bash "$ROOT/ops/deploy/smoke.sh"
fi

log "deploy proof passed: $API_IMAGE and $WEB_IMAGE on kind/$CLUSTER_NAME"
