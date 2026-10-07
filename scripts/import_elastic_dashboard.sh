#!/usr/bin/env bash
# Import the bundled hermes-otel dashboard and index patterns into Kibana.
#
# Usage (stack must be up):
#   ./scripts/import_elastic_dashboard.sh [kibana_base_url]
#
# Defaults to the local compose stack's Kibana (http://127.0.0.1:15602).
set -euo pipefail

KIBANA="${1:-http://127.0.0.1:15602}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
NDJSON="$HERE/docker-compose/elastic/dashboards.ndjson"

curl -sf -X POST "$KIBANA/api/saved_objects/_import?createNewCopies=true" \
  -H "kbn-xsrf: true" \
  -F "file=@$NDJSON;type=application/x-ndjson" \
  -o /dev/null -w "imported: HTTP %{http_code}\n"
echo "Open $KIBANA/app/dashboards and search for \"hermes-otel\"."
