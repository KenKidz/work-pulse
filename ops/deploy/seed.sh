#!/usr/bin/env bash
#
# Seed the in-cluster PostgreSQL with the tracked demo identities (deployment
# T6). Runs after the migration Job and before the smoke, mirroring
# `make db-seed`: the smoke logs in as the seeded worker, so the kind stack
# needs the same identities as the dev database.
#
# Seed identities are tracked product demo data, not infrastructure secrets
# (`.ai/security.md`); they are never applied to a real tenant.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=ops/deploy/lib.sh
source "$ROOT/ops/deploy/lib.sh"

require_command kubectl

POD="$(kube get pod -l app.kubernetes.io/name=postgres \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$POD" ] || fail "the postgres pod is not running (is the cluster up?)"

shopt -s nullglob
seeds=("$ROOT"/database/postgres/seeds/*.sql)
shopt -u nullglob
[ "${#seeds[@]}" -gt 0 ] || fail "no seed files under database/postgres/seeds"

for file in "${seeds[@]}"; do
  log "seeding $(basename "$file")"
  kube exec -i "$POD" -- psql -U workpulse -d workpulse \
    -v ON_ERROR_STOP=1 <"$file" >/dev/null
done

log "seed complete"
