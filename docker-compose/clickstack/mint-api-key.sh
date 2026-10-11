#!/usr/bin/env bash
# Create the first HyperDX account headlessly and print the team's ingestion
# API key, then wait until the bundled Collector's OTLP receivers are up.
#
# ClickStack gates ingest on a per-team key that normally comes from the UI
# (Team Settings → API Keys). The all-in-one image keeps it in its MongoDB,
# and the API's registration route is plain JSON, so one script does the
# first-run click-through:
#
#   docker-compose/clickstack/mint-api-key.sh            # prints the key
#   export OTEL_CLICKSTACK_API_KEY="$(docker-compose/clickstack/mint-api-key.sh)"
#
# Idempotent: when a team already exists the registration call is skipped and
# the existing key is printed. The account is local to this stack.
#
# Env overrides: CLICKSTACK_UI_PORT (3050), CLICKSTACK_OTLP_HTTP_PORT (4408),
# CLICKSTACK_EMAIL (hermes@example.com), CLICKSTACK_PASSWORD (must mix upper
# and lower case; default HermesOtel-local-1), CLICKSTACK_CONTAINER.

set -euo pipefail

UI_PORT="${CLICKSTACK_UI_PORT:-3050}"
OTLP_PORT="${CLICKSTACK_OTLP_HTTP_PORT:-4408}"
EMAIL="${CLICKSTACK_EMAIL:-hermes@example.com}"
PASSWORD="${CLICKSTACK_PASSWORD:-HermesOtel-local-1}"
CONTAINER="${CLICKSTACK_CONTAINER:-hermes-otel-clickstack}"
API="http://127.0.0.1:${UI_PORT}/api"

log() { echo "mint-api-key: $*" >&2; }

# 1. Wait for the API.
for _ in $(seq 1 60); do
  if curl -sf -m 5 "${API}/health" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -sf -m 5 "${API}/health" >/dev/null || { log "API not answering on ${API}"; exit 1; }

# 2. Register the first account unless a team exists already.
EXISTING=$(curl -s -m 10 "${API}/installation" | jq -r '.isTeamExisting // false')
if [ "${EXISTING}" != "true" ]; then
  BODY=$(jq -n --arg e "${EMAIL}" --arg p "${PASSWORD}" '{email: $e, password: $p, confirmPassword: $p}')
  RESP=$(curl -s -m 15 -X POST "${API}/register/password" -H 'content-type: application/json' --data "${BODY}")
  STATUS=$(printf '%s' "${RESP}" | jq -r '.status // empty' 2>/dev/null || true)
  if [ "${STATUS}" != "success" ]; then
    log "registration failed: ${RESP}"
    exit 1
  fi
  log "registered ${EMAIL}"
else
  log "a team already exists; reusing its key"
fi

# 3. Read the team's key from the all-in-one's MongoDB (the UI shows the same
#    value under Team Settings → API Keys).
KEY=$(docker exec "${CONTAINER}" mongo --quiet hyperdx --eval 'var t = db.teams.findOne({}, {apiKey: 1}); print(t ? t.apiKey : "")')
[ -n "${KEY}" ] || { log "no team key in MongoDB"; exit 1; }

# 4. The Collector gets its OTLP receivers through OpAMP once a team exists;
#    wait until 4318 answers (401 without a key is the healthy answer).
for _ in $(seq 1 30); do
  CODE=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/x-protobuf' --data-binary '' "http://127.0.0.1:${OTLP_PORT}/v1/traces" || true)
  if [ "${CODE}" != "000" ]; then break; fi
  sleep 2
done
log "OTLP/HTTP on ${OTLP_PORT} answers ${CODE} without a key"

printf '%s\n' "${KEY}"
