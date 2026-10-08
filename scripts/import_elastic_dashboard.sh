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
# Credentials are never echoed or logged.
set -euo pipefail

KIBANA="${1:-http://127.0.0.1:15602}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
NDJSON="$HERE/docker-compose/elastic/dashboards.ndjson"

AUTH_ARGS=()
if [[ -n "${KIBANA_API_KEY:-}" ]]; then
  AUTH_ARGS=(-H "Authorization: ApiKey ${KIBANA_API_KEY}")
elif [[ -n "${KIBANA_USERNAME:-}" ]]; then
  if [[ -z "${KIBANA_PASSWORD:-}" ]]; then
    echo "error: KIBANA_USERNAME is set but KIBANA_PASSWORD is empty" >&2
    exit 1
  fi
  AUTH_ARGS=(--user "${KIBANA_USERNAME}:${KIBANA_PASSWORD}")
fi

status="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  "$KIBANA/api/saved_objects/_import?createNewCopies=true" \
  -H "kbn-xsrf: true" \
  ${AUTH_ARGS[@]+"${AUTH_ARGS[@]}"} \
  -F "file=@$NDJSON;type=application/x-ndjson")"

case "$status" in
  200) echo "imported: HTTP $status" ;;
  *)
    echo "error: import failed with HTTP $status" >&2
    if [[ "$status" == "401" ]]; then
      echo "hint: Kibana has security enabled — set KIBANA_API_KEY, or KIBANA_USERNAME/KIBANA_PASSWORD" >&2
    fi
    exit 1
    ;;
esac
echo "Open $KIBANA/app/dashboards and search for \"hermes-otel\"."
