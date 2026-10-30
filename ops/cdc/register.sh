#!/usr/bin/env bash
# ops/cdc/register.sh — register the Debezium PostgreSQL connector.
#
# Idempotent: creates the connector when absent, updates its config when it
# already exists. Before creating a connector it writes `projection_state.ready
# = 0` into the target ClickHouse database so a report read cannot land in the
# window between connector registration and the sink's own readiness fence
# (feature D5). An update never re-fences a running projection.
#
# Env overrides (defaults match the compose dev services):
#   CONNECT_URL              Kafka Connect REST base (default http://localhost:8083)
#   CDC_CONNECTOR_NAME       logical connector name (default workpulse-cdc)
#   CDC_SLOT_NAME            replication slot + publication name (default workpulse_cdc)
#   CDC_CLICKHOUSE_SERVICE   compose service (default clickhouse; clickhouse-test)
#   CDC_PROJECTION_DATABASE  target database (default workpulse; cdc-test-up
#                            uses workpulse_test)
#   CDC_CLICKHOUSE_USER      ClickHouse user (default workpulse)
#   CDC_CLICKHOUSE_PASSWORD  ClickHouse password (default workpulse)
#
# `cdc-test-up` passes the test values; dev and production use the defaults.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONNECT_URL="${CONNECT_URL:-http://localhost:8083}"
CONNECTOR_NAME="${CDC_CONNECTOR_NAME:-workpulse-cdc}"
SLOT_NAME="${CDC_SLOT_NAME:-workpulse_cdc}"
CLICKHOUSE_SERVICE="${CDC_CLICKHOUSE_SERVICE:-clickhouse}"
PROJECTION_DATABASE="${CDC_PROJECTION_DATABASE:-workpulse}"
CLICKHOUSE_USER="${CDC_CLICKHOUSE_USER:-workpulse}"
CLICKHOUSE_PASSWORD="${CDC_CLICKHOUSE_PASSWORD:-workpulse}"
CONNECTOR_FILE="$ROOT_DIR/ops/cdc/connector.json"
SCHEMA_FILE="$ROOT_DIR/database/clickhouse/schema.sql"

if ! command -v curl >/dev/null 2>&1; then
  echo "cdc: 'curl' is required to reach the Kafka Connect REST API." >&2
  exit 1
fi

if [[ ! "$PROJECTION_DATABASE" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "cdc: invalid CDC_PROJECTION_DATABASE '$PROJECTION_DATABASE'" >&2
  exit 1
fi

# The API venv is the normal interpreter; fall back to a system Python for a
# standalone ops run. Python parses the JSON so formatting cannot affect the
# connector config or the test slot/publication override.
PYTHON="${PYTHON:-$ROOT_DIR/apps/api/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "cdc: python3 (or apps/api/.venv/bin/python) is required to parse the connector config." >&2
  exit 1
fi

# --- connector registration ------------------------------------------------

# The file is the Connect create payload (name + config). Read the config as
# JSON and set the slot/publication structurally: the test Connect instance
# reads the same dev PostgreSQL and must not share the dev replication slot.
config_json="$("$PYTHON" - "$CONNECTOR_FILE" "$SLOT_NAME" <<'PY'
import json
import sys
from pathlib import Path

path, slot = sys.argv[1], sys.argv[2]
try:
    payload = json.loads(Path(path).read_text())
    config = payload["config"]
    config["slot.name"] = slot
    config["publication.name"] = slot
except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
    print(f"cdc: cannot read the connector config from {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
print(json.dumps(config))
PY
)"

deadline=$((SECONDS + 60))
until curl -fsS "$CONNECT_URL/" >/dev/null 2>&1; do
  if ((SECONDS >= deadline)); then
    echo "cdc: Kafka Connect REST is not answering at $CONNECT_URL" >&2
    exit 1
  fi
  sleep 1
done

status="$(curl -s -o /dev/null -w '%{http_code}' \
  "$CONNECT_URL/connectors/$CONNECTOR_NAME")"
if [[ "$status" == "200" ]]; then
  printf '%s' "$config_json" | curl -fsS -X PUT \
    -H 'Content-Type: application/json' \
    --data-binary @- \
    "$CONNECT_URL/connectors/$CONNECTOR_NAME/config" >/dev/null
  echo "cdc: updated connector '$CONNECTOR_NAME' at $CONNECT_URL"
else
  # Readiness fence: only a new connector group can land in a half-built
  # projection. The reports gate reads projection_state FINAL, so seed ready=0
  # before the connector exists. `schema.sql` is the single DDL source; it is
  # idempotent and gives the target database its full projection schema.
  if docker compose --project-directory "$ROOT_DIR" ps --status running --services 2>/dev/null \
    | grep -qx "$CLICKHOUSE_SERVICE"; then
    docker compose --project-directory "$ROOT_DIR" exec -T "$CLICKHOUSE_SERVICE" \
      clickhouse-client \
      --user "$CLICKHOUSE_USER" \
      --password "$CLICKHOUSE_PASSWORD" \
      --query "CREATE DATABASE IF NOT EXISTS ${PROJECTION_DATABASE}"
    docker compose --project-directory "$ROOT_DIR" exec -T "$CLICKHOUSE_SERVICE" \
      clickhouse-client \
      --user "$CLICKHOUSE_USER" \
      --password "$CLICKHOUSE_PASSWORD" \
      --database "$PROJECTION_DATABASE" \
      --multiquery \
      < "$SCHEMA_FILE"
    docker compose --project-directory "$ROOT_DIR" exec -T "$CLICKHOUSE_SERVICE" \
      clickhouse-client \
      --user "$CLICKHOUSE_USER" \
      --password "$CLICKHOUSE_PASSWORD" \
      --query "INSERT INTO ${PROJECTION_DATABASE}.projection_state
(key, ready, updated_at)
VALUES ('reports', 0, now64(6))"
    echo "cdc: seeded projection_state ready=0 in ${PROJECTION_DATABASE}"
  else
    echo "cdc: ${CLICKHOUSE_SERVICE} is not running — skipping the readiness seed." >&2
    echo "cdc: start it with 'make clickhouse-up' (or clickhouse-test-up) first." >&2
  fi

  payload="$(printf '{"name":"%s","config":%s}' "$CONNECTOR_NAME" "$config_json")"
  printf '%s' "$payload" | curl -fsS -X POST \
    -H 'Content-Type: application/json' \
    --data-binary @- \
    "$CONNECT_URL/connectors" >/dev/null
  echo "cdc: created connector '$CONNECTOR_NAME' at $CONNECT_URL"
fi

# Create/update can answer before the connector is running. Poll until the
# connector and every task report RUNNING; a FAILED component makes the
# registration fail instead of printing a green-looking status.
deadline=$((SECONDS + 120))
while true; do
  if status_json="$(curl -fsS "$CONNECT_URL/connectors/$CONNECTOR_NAME/status" 2>/dev/null)"; then
    if printf '%s' "$status_json" | grep -q '"state":"FAILED"'; then
      printf '%s\n' "$status_json" >&2
      echo "cdc: connector '$CONNECTOR_NAME' is FAILED" >&2
      exit 1
    fi
    total="$(printf '%s' "$status_json" | grep -o '"state":' | wc -l || true)"
    running="$(printf '%s' "$status_json" | grep -o '"state":"RUNNING"' | wc -l || true)"
    if [[ "$running" -eq "$total" && "$total" -ge 2 ]]; then
      printf '%s\n' "$status_json"
      break
    fi
  fi
  if ((SECONDS >= deadline)); then
    echo "cdc: connector '$CONNECTOR_NAME' did not reach RUNNING" >&2
    exit 1
  fi
  sleep 1
done
