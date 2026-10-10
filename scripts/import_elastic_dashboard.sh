#!/usr/bin/env bash
# Import the bundled hermes-otel dashboard and index patterns into Kibana.
#
# Usage (stack must be up):
#   ./scripts/import_elastic_dashboard.sh [kibana_base_url]
#
# Defaults to the local compose stack's Kibana (http://127.0.0.1:15602).
#
# Optional authentication (for security-enabled Kibana, e.g. Elastic Cloud):
#   KIBANA_API_KEY                    -> sent as "Authorization: ApiKey <key>"
#   KIBANA_USERNAME / KIBANA_PASSWORD -> sent as HTTP basic auth
# API key takes precedence when both are set. With neither set, the request
# is sent unauthenticated (works for the no-login local compose stack).
# Credentials are never echoed or logged, and are handed to curl on stdin
# (curl --config -) so they never appear in its argv / `ps` output.
#
# Exit status: 0 only when Kibana reports every object imported. Transport
# failures, non-200 responses, and HTTP 200 with "success": false all exit 1.
set -euo pipefail

KIBANA="${1:-http://127.0.0.1:15602}"
KIBANA="${KIBANA%/}"   # a trailing slash would double up in the URLs below
HERE="$(cd "$(dirname "$0")/.." && pwd)"
NDJSON="$HERE/docker-compose/elastic/dashboards.ndjson"

# A newline or carriage return in a credential would end the curl config line
# and let the rest be read as another directive: refuse it up front, in the
# main shell (an `exit` inside a command substitution only ends the subshell).
reject_newlines() {
  local v
  for v in "$@"; do
    case "$v" in
      *$'\n'* | *$'\r'*)
        echo "error: credentials must not contain newlines" >&2
        exit 1
        ;;
    esac
  done
}

# Escape a value for use inside a double-quoted curl config string.
cfg_escape() {
  local v="$1"
  v="${v//\\/\\\\}"
  v="${v//\"/\\\"}"
  printf '%s' "$v"
}

# curl config fed on stdin; stays empty for the unauthenticated local stack.
AUTH_CFG=""
if [[ -n "${KIBANA_API_KEY:-}" ]]; then
  reject_newlines "$KIBANA_API_KEY"
  AUTH_CFG="header = \"Authorization: ApiKey $(cfg_escape "$KIBANA_API_KEY")\""
elif [[ -n "${KIBANA_USERNAME:-}" ]]; then
  if [[ -z "${KIBANA_PASSWORD:-}" ]]; then
    echo "error: KIBANA_USERNAME is set but KIBANA_PASSWORD is empty" >&2
    exit 1
  fi
  reject_newlines "$KIBANA_USERNAME" "$KIBANA_PASSWORD"
  AUTH_CFG="user = \"$(cfg_escape "$KIBANA_USERNAME"):$(cfg_escape "$KIBANA_PASSWORD")\""
fi

BODY_FILE="$(mktemp)"
ERR_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE" "$ERR_FILE"' EXIT

# printf is a shell builtin, so the config never shows up in any argv.
status="$(printf '%s\n' "$AUTH_CFG" | curl --config - -sS -o "$BODY_FILE" -w '%{http_code}' -X POST \
  "$KIBANA/api/saved_objects/_import?createNewCopies=true" \
  -H "kbn-xsrf: true" \
  -F "file=@$NDJSON;type=application/x-ndjson" 2>"$ERR_FILE")" || status=000

if [[ "$status" == "000" ]]; then
  echo "error: could not reach Kibana at $KIBANA" >&2
  if [[ -s "$ERR_FILE" ]]; then
    sed 's/^/  /' "$ERR_FILE" >&2
  fi
  exit 1
fi

if [[ "$status" != "200" ]]; then
  echo "error: import failed with HTTP $status" >&2
  if [[ "$status" == "401" ]]; then
    echo "hint: Kibana has security enabled — set KIBANA_API_KEY, or KIBANA_USERNAME/KIBANA_PASSWORD" >&2
  fi
  exit 1
fi

# Kibana answers HTTP 200 even when individual objects fail to import; the
# real outcome is in the body ("success": false + an "errors" array).
body="$(tr -d '\n\r' <"$BODY_FILE")"
if [[ "$body" =~ \"success\"[[:space:]]*:[[:space:]]*false ]]; then
  echo "error: Kibana accepted the request (HTTP 200) but some objects failed to import:" >&2
  # Failed objects are {"id":...,"type":...} entries inside the "errors" array.
  failed="$(printf '%s' "${body#*\"errors\"}" \
    | grep -oE '\{[[:space:]]*"id"[[:space:]]*:[[:space:]]*"[^"]*"[[:space:]]*,[[:space:]]*"type"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | sed -E 's/.*"id"[[:space:]]*:[[:space:]]*"([^"]*)".*"type"[[:space:]]*:[[:space:]]*"([^"]*)"/  - \2 \1/' || true)"
  if [[ -n "$failed" ]]; then
    printf '%s\n' "$failed" >&2
  else
    echo "  (could not parse object ids from the response)" >&2
  fi
  exit 1
fi

count=""
if [[ "$body" =~ \"successCount\"[[:space:]]*:[[:space:]]*([0-9]+) ]]; then
  count=", successCount=${BASH_REMATCH[1]}"
fi
echo "imported: HTTP $status$count"
echo "Open $KIBANA/app/dashboards and search for \"hermes-otel\"."
