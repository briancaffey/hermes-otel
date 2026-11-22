#!/usr/bin/env bash
# Print the deployment parameters for main.bicep as JSON.
#
#   NVIDIA_API_KEY=... HERMES_API_SERVER_KEY=... HERMES_DASHBOARD_PASSWORD=... \
#   HERMES_DASHBOARD_SESSION_SECRET=... ./parameters.sh hermes-otel-demo:dev > parameters.json
#
# The output holds secrets: keep it out of git and delete it after deploying.
set -euo pipefail

image="${1:?usage: parameters.sh <repository:tag>}"
: "${NVIDIA_API_KEY:?set NVIDIA_API_KEY}"
: "${HERMES_API_SERVER_KEY:?set HERMES_API_SERVER_KEY (openssl rand -hex 32)}"
: "${HERMES_DASHBOARD_PASSWORD:?set HERMES_DASHBOARD_PASSWORD (openssl rand -hex 16)}"
: "${HERMES_DASHBOARD_SESSION_SECRET:?set HERMES_DASHBOARD_SESSION_SECRET (openssl rand -hex 32)}"

jq -n \
  --arg image "$image" \
  --arg model "${HERMES_DEMO_MODEL:-nvidia/nemotron-3.5-lightning-30b-a3b}" \
  --arg provider "${HERMES_DEMO_PROVIDER:-nvidia}" \
  --arg user "${HERMES_DASHBOARD_USERNAME:-admin}" \
  --arg nvidia "$NVIDIA_API_KEY" \
  --arg api "$HERMES_API_SERVER_KEY" \
  --arg password "$HERMES_DASHBOARD_PASSWORD" \
  --arg session "$HERMES_DASHBOARD_SESSION_SECRET" \
  '{
    "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
    contentVersion: "1.0.0.0",
    parameters: {
      image: {value: $image},
      environment: {value: {
        API_SERVER_ENABLED: "true",
        API_SERVER_HOST: "0.0.0.0",
        HERMES_DASHBOARD: "1",
        HERMES_DASHBOARD_BASIC_AUTH_USERNAME: $user,
        HERMES_DEMO_MODEL: $model,
        HERMES_DEMO_PROVIDER: $provider
      }},
      secretEnvironment: {value: {
        NVIDIA_API_KEY: $nvidia,
        API_SERVER_KEY: $api,
        HERMES_DASHBOARD_BASIC_AUTH_PASSWORD: $password,
        HERMES_DASHBOARD_BASIC_AUTH_SECRET: $session
      }}
    }
  }'
