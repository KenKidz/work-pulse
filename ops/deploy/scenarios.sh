#!/usr/bin/env bash
#
# Deploy failure scenarios (deployment T6).
#
# Proves the failure halves of the deploy proof against the same kind cluster:
#   1. upgrade with a failing migration keeps the previous SHA serving,
#   2. a missing Secret never reaches readiness and names the missing key,
#   3. a wrong-but-present DATABASE_URL names the key only (no value/DSN text),
#   4. a fresh cluster with a failing migration never applies app workloads.
#
# Scenario 4 deletes the kind cluster (a fresh cluster is the requirement), so
# it runs last. Run with `make deploy-check-scenarios` after `make images`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=ops/deploy/lib.sh
source "$ROOT/ops/deploy/lib.sh"

require_command docker kind kubectl curl jq

FAILING_MIGRATION_JOB="$ROOT/ops/deploy/k8s/scenarios/migration-fail-job.yaml"

run_expect_failure() {
  local label="$1" log_file="$2"
  shift 2
  if "$@" >"$log_file" 2>&1; then
    printf 'deploy: error: scenario expected a failure but succeeded: %s\n' "$label" >&2
    cat "$log_file" >&2
    exit 1
  fi
}

expect_in_log() {
  local log_file="$1" pattern="$2" label="$3"
  if ! grep -Fq -- "$pattern" "$log_file"; then
    printf 'deploy: error: %s did not report %q\n' "$label" "$pattern" >&2
    cat "$log_file" >&2
    exit 1
  fi
}

refute_in_log() {
  local log_file="$1" pattern="$2" label="$3"
  # The pattern travels on stdin, not argv (process arguments are visible to
  # other local users). The patterns passed here are non-secret test values.
  if grep -Fq -f - "$log_file" <<<"$pattern"; then
    printf 'deploy: error: %s leaked %q\n' "$label" "$pattern" >&2
    cat "$log_file" >&2
    exit 1
  fi
}

# Secret-specific variant: never puts the needle in argv and never echoes the
# log (the log is exactly what may contain the leaked credential).
refute_secret_in_log() {
  local log_file="$1" secret="$2" label="$3"
  if grep -Fq -f - "$log_file" <<<"$secret"; then
    printf 'deploy: error: %s leaked a generated secret (output suppressed)\n' "$label" >&2
    exit 1
  fi
}

assert_previous_images_serving() {
  local expected_api="$1" expected_web="$2" name image
  for name in "${APP_DEPLOYMENTS[@]}"; do
    image="$(kube get deployment "$name" -o jsonpath='{.spec.template.spec.containers[0].image}')"
    case "$name" in
      web)
        [ "$image" = "$expected_web" ] \
          || fail "deployment/$name uses $image after a failed migration (expected $expected_web)"
        ;;
      *)
        [ "$image" = "$expected_api" ] \
          || fail "deployment/$name uses $image after a failed migration (expected $expected_api)"
        ;;
    esac
  done
  log "previous app images still serving: $expected_api, $expected_web"
}

scenario_upgrade_migration_failure() {
  log "scenario 1/4: upgrade with a failing migration keeps the previous SHA serving"
  local previous_tag="${IMAGE_TAG}-prev"
  local previous_api="workpulse-api:${previous_tag}"
  local previous_web="workpulse-web:${previous_tag}"

  # A synthetic n-1: the proof is the rollout-ordering gate, not image
  # archaeology (the failing Job is likewise deliberate, not a bad revision).
  docker tag "$API_IMAGE" "$previous_api"
  docker tag "$WEB_IMAGE" "$previous_web"

  log "deploying the previous image ($previous_api) with the real migration"
  env IMAGE_TAG="$previous_tag" \
    API_IMAGE="$previous_api" WEB_IMAGE="$previous_web" \
    bash "$ROOT/ops/deploy/deploy.sh"

  log "attempting the current image with a deliberately failing migration"
  local log_file
  log_file="$(mktemp)"
  run_expect_failure "upgrade with failing migration" "$log_file" \
    env IMAGE_TAG="$IMAGE_TAG" API_IMAGE="$API_IMAGE" WEB_IMAGE="$WEB_IMAGE" \
    MIGRATION_JOB_FILE="$FAILING_MIGRATION_JOB" \
    bash "$ROOT/ops/deploy/deploy.sh"
  expect_in_log "$log_file" "Job workpulse-migrate failed" "the upgrade failure"
  expect_in_log "$log_file" "app workloads were NOT applied" "the upgrade failure"
  expect_in_log "$log_file" "exit=1" "the failing migration Job's exit state"
  rm -f "$log_file"

  assert_previous_images_serving "$previous_api" "$previous_web"
  log "smoke against the still-serving previous deployment"
  SMOKE_READY_TIMEOUT=90 bash "$ROOT/ops/deploy/smoke.sh"

  log "restoring the current image"
  env IMAGE_TAG="$IMAGE_TAG" API_IMAGE="$API_IMAGE" WEB_IMAGE="$WEB_IMAGE" \
    bash "$ROOT/ops/deploy/deploy.sh"
  log "scenario 1/4 passed"
}

scenario_missing_secret() {
  log "scenario 2/4: a missing Secret never reaches readiness and names the key"
  load_values

  kube delete secret workpulse-config --ignore-not-found >/dev/null
  kube rollout restart deployment/api >/dev/null

  local log_file
  log_file="$(mktemp)"
  if SMOKE_READY_TIMEOUT=45 bash "$ROOT/ops/deploy/smoke.sh" >"$log_file" 2>&1; then
    printf 'deploy: error: the smoke passed with no workpulse-config Secret\n' >&2
    cat "$log_file" >&2
    exit 1
  fi
  expect_in_log "$log_file" "missing configuration:" "the missing-Secret failure"
  expect_in_log "$log_file" "DATABASE_URL" "the missing-Secret failure"
  # shellcheck disable=SC2153 # assigned by load_values' sourced values file
  refute_secret_in_log "$log_file" "$POSTGRES_PASSWORD" "the missing-Secret failure"
  rm -f "$log_file"

  log "restoring the Secret"
  apply_secret
  kube rollout restart deployment/api >/dev/null
  kube rollout status deployment/api --timeout=300s
  log "scenario 2/4 passed"
}

scenario_wrong_secret() {
  log "scenario 3/4: a wrong-but-present DATABASE_URL names the key only"
  load_values

  local bad_value wrong_file saved_values log_file
  bad_value="$(random_hex 16)"
  wrong_file="$(mktemp)"
  saved_values="$VALUES_FILE"
  sed "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://workpulse:${bad_value}@postgres:5432/workpulse|" \
    "$saved_values" >"$wrong_file"
  chmod 600 "$wrong_file"

  VALUES_FILE="$wrong_file"
  apply_secret
  VALUES_FILE="$saved_values"
  kube rollout restart deployment/api >/dev/null

  log_file="$(mktemp)"
  if SMOKE_READY_TIMEOUT=45 bash "$ROOT/ops/deploy/smoke.sh" >"$log_file" 2>&1; then
    printf 'deploy: error: the smoke passed with a wrong DATABASE_URL\n' >&2
    cat "$log_file" >&2
    exit 1
  fi
  expect_in_log "$log_file" "DATABASE_URL" "the wrong-Secret failure"
  refute_in_log "$log_file" "missing configuration:" "the wrong-Secret failure"
  refute_in_log "$log_file" "$bad_value" "the wrong-Secret failure"
  rm -f "$log_file" "$wrong_file"

  log "restoring the Secret"
  apply_secret
  kube rollout restart deployment/api >/dev/null
  kube rollout status deployment/api --timeout=300s
  log "scenario 3/4 passed"
}

scenario_fresh_migration_failure() {
  log "scenario 4/4: a fresh cluster never applies app workloads when migration fails"
  kind delete cluster --name "$CLUSTER_NAME" >/dev/null 2>&1 || true

  local log_file
  log_file="$(mktemp)"
  run_expect_failure "fresh cluster with failing migration" "$log_file" \
    env IMAGE_TAG="$IMAGE_TAG" API_IMAGE="$API_IMAGE" WEB_IMAGE="$WEB_IMAGE" \
    MIGRATION_JOB_FILE="$FAILING_MIGRATION_JOB" bash "$ROOT/ops/deploy/deploy.sh"
  expect_in_log "$log_file" "Job workpulse-migrate failed" "the fresh migration failure"
  expect_in_log "$log_file" "app workloads were NOT applied" "the fresh migration failure"
  expect_in_log "$log_file" "exit=1" "the failing migration Job's exit state"
  rm -f "$log_file"

  local name
  for name in "${APP_DEPLOYMENTS[@]}"; do
    if kube get deployment "$name" >/dev/null 2>&1; then
      fail "deployment/$name exists after a failed migration on a fresh cluster"
    fi
  done
  log "no app workloads were applied"
  log "scenario 4/4 passed (the cluster keeps infra + the failed Job; run 'make deploy-down' to reclaim it)"
}

scenario_upgrade_migration_failure
scenario_missing_secret
scenario_wrong_secret
scenario_fresh_migration_failure

log "all deploy failure scenarios passed"
