#!/usr/bin/env bash
# Create (or sign into) a local Agenta account headlessly and print a project
# API key for the plugin.
#
# Agenta's OTLP route is gated by a project API key that normally comes from
# the UI. Sign-ups are open on a fresh OSS stack, the API is SuperTokens'
# email/password recipe with header-based sessions, and `POST /api/keys`
# mints a key for the user's default project, so one script does the whole
# first-run click-through:
#
#   docker-compose/agenta/mint-api-key.sh                 # prints the key
#   export OTEL_AGENTA_API_KEY="$(docker-compose/agenta/mint-api-key.sh)"
#
# Re-running signs in instead of signing up and mints a NEW key each time
# (the old ones stay valid; `GET /api/keys` lists their prefixes).
#
# Env overrides: AGENTA_PORT (3060), AGENTA_EMAIL (hermes@example.com),
# AGENTA_PASSWORD (at least 8 characters; default HermesOtel-local-1).

set -euo pipefail

PORT="${AGENTA_PORT:-3060}"
EMAIL="${AGENTA_EMAIL:-hermes@example.com}"
PASSWORD="${AGENTA_PASSWORD:-HermesOtel-local-1}"
API="http://127.0.0.1:${PORT}/api"

log() { echo "mint-api-key: $*" >&2; }

# 1. Wait for the API behind nginx.
for _ in $(seq 1 90); do
  if curl -sf -m 5 "${API}/health" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -sf -m 5 "${API}/health" >/dev/null || { log "API not answering on ${API}"; exit 1; }

FORM=$(jq -n --arg e "${EMAIL}" --arg p "${PASSWORD}" '{formFields: [{id: "email", value: $e}, {id: "password", value: $p}]}')
HEADERS=$(mktemp)

# 2. Sign up; an existing account is signed in instead.
BODY=$(curl -s -m 20 -D "${HEADERS}" -X POST "${API}/auth/signup" -H 'content-type: application/json' -H 'rid: emailpassword' --data "${FORM}")
STATUS=$(printf '%s' "${BODY}" | jq -r '.status // empty')
if [ "${STATUS}" != "OK" ]; then
  log "signup answered ${STATUS:-?}; signing in"
  BODY=$(curl -s -m 20 -D "${HEADERS}" -X POST "${API}/auth/signin" -H 'content-type: application/json' -H 'rid: emailpassword' --data "${FORM}")
  STATUS=$(printf '%s' "${BODY}" | jq -r '.status // empty')
  [ "${STATUS}" = "OK" ] || { log "sign-in failed: ${BODY}"; rm -f "${HEADERS}"; exit 1; }
else
  log "registered ${EMAIL}"
fi

# 3. The session is header-based: st-access-token in the response headers.
TOKEN=$(grep -i '^st-access-token:' "${HEADERS}" | head -1 | cut -d' ' -f2 | tr -d '\r')
rm -f "${HEADERS}"
[ -n "${TOKEN}" ] || { log "no st-access-token in the response"; exit 1; }
AUTH="Authorization: Bearer ${TOKEN}"

# 4. Mint a key for the user's default project.
KEY=$(curl -s -m 20 -X POST "${API}/keys" -H "${AUTH}" -H 'content-type: application/json' --data '{}' | jq -r '.')
[ -n "${KEY}" ] && [ "${KEY}" != "null" ] || { log "no key returned by POST /api/keys"; exit 1; }
PROJECT=$(curl -s -m 20 "${API}/projects" -H "${AUTH}" | jq -r '.[0].project_name // "?"')
log "key minted for project ${PROJECT}; OTLP at ${API}/otlp/v1/traces"

printf '%s\n' "${KEY}"
