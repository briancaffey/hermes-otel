#!/usr/bin/env bash
# Send one chat to Hermes through its OpenAI-compatible API. The chat asks
# Hermes to run a shell command that prints a marker, so the turn has a model
# call and a tool call, and the marker identifies its trace afterwards.
#
#   HERMES_URL=https://<app address> HERMES_API_SERVER_KEY=... ./chat.sh [marker]
set -euo pipefail

: "${HERMES_URL:?set HERMES_URL to the app address}"
: "${HERMES_API_SERVER_KEY:?set HERMES_API_SERVER_KEY}"
marker="${1:-chat-$(date +%s)}"

for _ in $(seq 1 40); do
  [ "$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$HERMES_URL/health")" = 200 ] && break
  sleep 10
done

body=$(jq -n --arg marker "$marker" '{
  model: "hermes-agent",
  messages: [{role: "user", content: "Run the shell command: echo \($marker) ; then tell me what it printed."}]
}')
response=$(curl -s -m 300 "$HERMES_URL/v1/chat/completions" \
  -H "Authorization: Bearer $HERMES_API_SERVER_KEY" \
  -H "Content-Type: application/json" \
  -d "$body")

reply=$(jq -r '.choices[0].message.content // empty' <<<"$response")
tokens=$(jq -r '.usage.total_tokens // "?"' <<<"$response")
echo "marker: $marker"
echo "reply:  $reply"
echo "tokens: $tokens"
if [[ "$reply" != *"$marker"* ]]; then
  echo "Hermes did not answer with the marker. Response: $response" >&2
  exit 1
fi
