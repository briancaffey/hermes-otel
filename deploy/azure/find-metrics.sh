#!/usr/bin/env bash
# Show the metrics hermes-otel has written to the Azure Monitor workspace:
# the metric names, and tokens by type over the last hour. Metrics are
# exported about once a minute, so this polls until some arrive.
#
#   RESOURCE_GROUP=hermes-otel-demo ./find-metrics.sh
#
# Reading needs the "Monitoring Data Reader" role on the workspace. The script
# grants it to whoever is signed in, which needs permission to assign roles in
# the resource group, and the role takes a few minutes to apply the first time.
set -euo pipefail

group="${RESOURCE_GROUP:-hermes-otel-demo}"
workspace=$(az resource list --resource-group "$group" --resource-type Microsoft.Monitor/accounts --query "[0].id" --output tsv)
endpoint=$(az resource show --ids "$workspace" --query properties.metrics.prometheusQueryEndpoint --output tsv)

# The signed-in identity's object id, from its own access token.
payload=$(az account get-access-token --query accessToken --output tsv | cut -d. -f2 | tr '_-' '/+')
while [ $((${#payload} % 4)) -ne 0 ]; do payload="$payload="; done
me=$(base64 -d <<<"$payload" | jq -r .oid)
az role assignment create --assignee-object-id "$me" --assignee-principal-type "${PRINCIPAL_TYPE:-User}" \
  --role "Monitoring Data Reader" --scope "$workspace" --only-show-errors --output none || true

prom() { # prom <path> <key=value>
  az rest --method get --resource https://prometheus.monitor.azure.com \
    --url "$endpoint/api/v1/$1" --url-parameters "$2" --only-show-errors 2>/dev/null || echo '{}'
}

for _ in $(seq 1 30); do
  names=$(prom "label/__name__/values" "start=$(($(date +%s) - 3600))" | jq -c '.data // []')
  if [ "$(jq length <<<"$names")" -gt 0 ]; then
    echo "Metrics in the Azure Monitor workspace (${workspace##*/}):"
    echo
    jq -r '.[] | "- \(.)"' <<<"$names"
    echo
    echo "| token type | tokens, last hour |"
    echo "|---|---|"
    prom query 'query=sum by (token_type) (sum_over_time({__name__="hermes.token.usage"}[1h]))' \
      | jq -r '.data.result // [] | sort_by(.metric.token_type) | .[] | "| \(.metric.token_type) | \(.value[1]) |"'
    exit 0
  fi
  sleep 20
done
echo "No metrics reached the Azure Monitor workspace within 10 minutes." >&2
exit 1
