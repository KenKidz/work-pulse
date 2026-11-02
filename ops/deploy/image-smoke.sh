#!/usr/bin/env bash
#
# Image smoke (deployment T4): prove both container images answer over HTTP
# and that the API image's strict boot check fails loudly — naming the key,
# never a value — when required configuration is absent.
#
# Run after `make images` (deployment targets stay outside `make check`):
#   bash ops/deploy/image-smoke.sh
#
# Honors IMAGE_TAG / API_IMAGE / WEB_IMAGE overrides.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${IMAGE_TAG:-sha-$(git -C "$ROOT" rev-parse --short HEAD)}"
API_IMAGE="${API_IMAGE:-workpulse-api:${TAG}}"
WEB_IMAGE="${WEB_IMAGE:-workpulse-web:${TAG}}"

command -v curl >/dev/null 2>&1 || {
  echo "error: curl is required for the image smoke" >&2
  exit 1
}

for image in "$API_IMAGE" "$WEB_IMAGE"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "error: image $image not found — run 'make images' first" >&2
    exit 1
  fi
done

containers=()
missing_env=""
cleanup() {
  local name
  if [ -n "$missing_env" ]; then
    rm -f "$missing_env"
  fi
  for name in ${containers[@]+"${containers[@]}"}; do
    docker rm -f "$name" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

host_port() {
  local container="$1" port="$2"
  docker port "$container" "${port}/tcp" | awk -F: 'NR == 1 {print $NF}'
}

wait_for_http() {
  local url="$1" label="$2" container="$3"
  local deadline=$((SECONDS + 90))
  until curl --connect-timeout 2 --max-time 5 -fsS -o /dev/null "$url" 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "error: timed out waiting for $label ($url)" >&2
      docker logs "$container" >&2 || true
      exit 1
    fi
    sleep 1
  done
  echo "ok: $label"
}

# --- API: liveness answers without any dependency -------------------------
api_ok="workpulse-api-smoke-ok-$$"
containers+=("$api_ok")
# `.env.example` is the tracked mirror of every configuration key; using it
# keeps the smoke in step when `REQUIRED_BOOT_KEYS` grows.
docker run -d --rm --name "$api_ok" -p 127.0.0.1::8000 \
  --env-file "$ROOT/.env.example" \
  -e ENVIRONMENT=production \
  "$API_IMAGE" >/dev/null
wait_for_http \
  "http://127.0.0.1:$(host_port "$api_ok" 8000)/healthz" \
  "api /healthz" \
  "$api_ok"

# --- API (failure): strict boot names the missing key, no values ----------
# Supply every required key except DATABASE_URL, so the assertion is about the
# missing key rather than its position in REQUIRED_BOOT_KEYS.
missing_name="workpulse-api-smoke-missing-$$"
missing_env="$(mktemp)"
grep -v '^DATABASE_URL=' "$ROOT/.env.example" >"$missing_env"
containers+=("$missing_name")
set +e
missing_output="$(timeout 60 docker run --rm --name "$missing_name" \
  --env-file "$missing_env" \
  -e ENVIRONMENT=production \
  "$API_IMAGE" 2>&1)"
missing_status=$?
set -e

if [ "$missing_status" -eq 124 ]; then
  echo "error: api image did not exit within 60s — strict boot check did not fail" >&2
  exit 1
fi
if [ "$missing_status" -eq 0 ]; then
  echo "error: api image booted without required configuration" >&2
  exit 1
fi
if ! grep -Fq "missing configuration: DATABASE_URL" <<<"$missing_output"; then
  echo "error: expected 'missing configuration: DATABASE_URL', got:" >&2
  printf '%s\n' "$missing_output" >&2
  exit 1
fi
echo "ok: api strict boot names the missing key"

# --- Web: the login route is served --------------------------------------
web_ok="workpulse-web-smoke-$$"
containers+=("$web_ok")
docker run -d --rm --name "$web_ok" -p 127.0.0.1::3000 "$WEB_IMAGE" >/dev/null
wait_for_http \
  "http://127.0.0.1:$(host_port "$web_ok" 3000)/login" \
  "web /login" \
  "$web_ok"

echo "image smoke passed: $API_IMAGE, $WEB_IMAGE"
