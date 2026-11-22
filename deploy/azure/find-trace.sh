#!/usr/bin/env bash
# Find the trace of a chat in Application Insights by the marker chat.sh sent.
# Telemetry takes a minute or two to become queryable, so this polls.
#
#   RESOURCE_GROUP=hermes-otel-demo ./find-trace.sh <marker>
set -euo pipefail

marker="${1:?usage: find-trace.sh <marker>}"
group="${RESOURCE_GROUP:-hermes-otel-demo}"

az extension add --name application-insights --upgrade --yes --only-show-errors
app=$(az monitor app-insights component show --resource-group "$group" --query "[0].name" --output tsv)

turn="dependencies
| where timestamp > ago(30m) and name == 'agent'
| where tostring(customDimensions['input.value']) has '$marker'
| project operation_Id"
spans="dependencies
| where timestamp > ago(30m)
| where operation_Id in (($turn))
| order by timestamp asc
| project span = name, ms = round(duration), tokens = tostring(customDimensions['gen_ai.usage.total_tokens']), trace = operation_Id"

for _ in $(seq 1 30); do
  rows=$(az monitor app-insights query --resource-group "$group" --app "$app" \
    --analytics-query "$spans" --query "tables[0].rows" --output json)
  if [ "$(jq length <<<"$rows")" -gt 0 ]; then
    echo "Trace for $marker in Application Insights ($app):"
    echo
    echo "| span | ms | tokens | trace id |"
    echo "|---|---|---|---|"
    jq -r '.[] | "| \(.[0]) | \(.[1]) | \(.[2]) | \(.[3]) |"' <<<"$rows"
    exit 0
  fi
  sleep 20
done
echo "No trace for $marker reached Application Insights within 10 minutes." >&2
exit 1
