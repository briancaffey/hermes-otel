---
sidebar_position: 3
title: Hermes on Azure
description: "Run Hermes Agent on Azure Container Apps and send its traces and logs to Azure Monitor / Application Insights with hermes-otel: a deployable demo with Bicep, a container image and a GitHub Actions pipeline. Verified 2026-11-22."
---

# Hermes on Azure

The repo ships a deployable demo under
[`deploy/azure/`](https://github.com/briancaffey/hermes-otel/tree/main/deploy/azure):
Hermes Agent in a container on **Azure Container Apps**, with hermes-otel
sending traces and logs to **Application Insights**. A chat with Hermes shows
up as a trace in Application Insights and in the OTel tab of the Hermes
dashboard.

It is self-hosted Hermes Agent in your own subscription, not Hermes Enterprise.
Verified on 2026-11-22 in Central US with Hermes Agent v0.23.3.

## What is in it

| Piece | Where |
|---|---|
| Infrastructure as Bicep: registry, Application Insights, a Container Apps environment and the app | `deploy/azure/bicep/` |
| Container image: the Hermes image plus hermes-otel and its config | `deploy/azure/image/` |
| Scripts to deploy, send a chat and find its trace | `deploy/azure/*.sh` |
| GitHub Actions pipeline that runs the whole thing | `.github/workflows/azure-demo.yml` |

The [README](https://github.com/briancaffey/hermes-otel/blob/main/deploy/azure/README.md)
has the commands.

## How the telemetry gets to Azure

A Container Apps environment can run a **managed OpenTelemetry agent**. It
sets `OTEL_EXPORTER_OTLP_ENDPOINT` in every container, accepts OTLP over gRPC,
and forwards traces and logs to Application Insights. hermes-otel needs only a
generic [`otlp`](/backends/otlp) entry that reads that variable:

```yaml
backends:
  - type: otlp
    name: azure-monitor
    endpoint: ${OTEL_EXPORTER_OTLP_ENDPOINT}
    protocol: grpc     # the managed agent accepts gRPC only
    metrics: false     # it does not forward metrics to Application Insights
```

In Application Insights, spans are in the `dependencies` table and log records
in `traces`; the role name is `<environment name>.hermes-agent`, and every span
attribute arrives in `customDimensions`. See
[Azure Monitor](/backends/azure-monitor) for the queries and for the Collector
route, which also carries metrics.

## The pipeline

Six stages, about six minutes for a first full run:

1. **Create registry**
2. **Build image**
3. **Preview changes**
4. **Deploy Hermes**
5. **Send test chat**
6. **Find trace in Azure Monitor**

The last stage prints the spans of the chat the pipeline sent, so a green run
is proof that a turn on Azure reached Application Insights.

## Limits of the demo

- Hermes's data folder is ephemeral; a new deployment starts with empty
  sessions and an empty OTel tab. Telemetry already in Application Insights is
  not affected.
- Metrics are not sent on this route.
- The app is reachable from the internet, behind an API key and a dashboard
  password.
