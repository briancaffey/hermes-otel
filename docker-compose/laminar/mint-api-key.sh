#!/usr/bin/env bash
# Mint a Laminar project API key without opening the UI.
#
# Signs in through the self-hosted passwordless local-email flow, creates a
# workspace + project if the user has none, then asks the frontend's CLI
# endpoint for a project API key and prints it.
#
# Usage:
#   docker-compose/laminar/mint-api-key.sh [email] [project-name]
#   export LMNR_PROJECT_API_KEY=$(docker-compose/laminar/mint-api-key.sh)

set -euo pipefail

BASE="${LAMINAR_UI_URL:-http://localhost:${LAMINAR_UI_PORT:-5667}}"
EMAIL="${1:-dev@example.com}"
PROJECT="${2:-hermes-otel}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

post() {
  curl -fsS -b "$JAR" -c "$JAR" -X POST "$BASE$1" \
    -H 'content-type: application/json' -H "origin: $BASE" -d "$2"
}

post /api/auth/sign-in/local-email "{\"email\":\"$EMAIL\",\"name\":\"${EMAIL%%@*}\"}" >/dev/null

if [ "$(curl -fsS -b "$JAR" "$BASE/api/workspaces")" = "[]" ]; then
  post /api/workspaces "{\"name\":\"hermes\",\"projectName\":\"$PROJECT\"}" >/dev/null
fi

post /api/cli/api-key '{}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["apiKey"])'
