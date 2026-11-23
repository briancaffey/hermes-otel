---
sidebar_position: 3
title: Hermes on Azure
description: "Run Hermes Agent on Azure Container Apps and send its traces and logs to Azure Monitor / Application Insights with hermes-otel: a deployable demo with Bicep, a container image and a GitHub Actions pipeline. Verified 2026-11-22."
---

# Hermes on Azure

The repo ships a deployable demo under
[`deploy/azure/`](https://github.com/briancaffey/hermes-otel/tree/main/deploy/azure):
Hermes Agent in a container on **Azure Container Apps**, with hermes-otel
sending traces and logs to **Application Insights** and metrics to an **Azure
Monitor workspace**. A chat with Hermes shows
up as a trace in Application Insights and in the OTel tab of the Hermes
dashboard.

It is self-hosted Hermes Agent in your own subscription, not Hermes Enterprise.
Verified on 2026-11-22 in Central US with Hermes Agent v0.23.3.

![One Hermes turn in Application Insights: the agent, its model calls and its tool call on a timeline, with the generative AI properties of the selected call](/img/azure/azure-trace-details.png)

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

Metrics take a second route. An OpenTelemetry Collector runs beside Hermes in
the same replica, signs in with the app's managed identity, and posts to Azure
Monitor's OTLP intake (a data collection rule), which stores them in an Azure
Monitor workspace. The plugin sends metrics, and only metrics, to that
Collector:

```yaml
metrics_histogram: exponential      # what Azure Monitor's metric views expect

backends:
  - type: otlp
    name: azure-monitor-metrics
    endpoint: http://localhost:4318/v1/traces   # the Collector; /v1/metrics is derived
    traces: false
    logs: false
    metrics: true
    metrics_temporality: delta
```

Query them with PromQL in the workspace; names keep their dots:

```promql
sum by (token_type) (sum_over_time({__name__="hermes.token.usage"}[1h]))
```

In Application Insights, spans are in the `dependencies` table and log records
in `traces`; the role name is `<environment name>.hermes-agent`, and every span
attribute arrives in `customDimensions`. See
[Azure Monitor](/backends/azure-monitor) for the queries and for the Collector
route, which also carries metrics.

## What it looks like in Azure

**A turn as a trace.** Application Insights reads the OpenTelemetry GenAI
attributes hermes-otel sets, so a turn opens as `invoke_agent` → `chat` →
`execute_tool`, with input and output tokens and the messages on the right.
Nothing Azure-specific is emitted to get this.

![Trace search in Application Insights: ten Hermes traces with their durations, item counts and token totals](/img/azure/azure-trace-search.png)

**The Agents view.** Application Insights lists `hermes-agent` as an agent,
with its invocations, tool calls, model usage and token consumption:

![The Agents view: hermes-agent with eight invocations and no errors](/img/azure/azure-agents-invocations.png)

![The Agents view: tool calls by tool and model usage by model](/img/azure/azure-agents-tools-models.png)

![The Agents view: token consumption by model, input against output tokens, and the cache ratio](/img/azure/azure-agents-tokens.png)

**Metrics.** The Azure Monitor workspace's Prometheus explorer, with the
token query above:

![Prometheus explorer in the Azure Monitor workspace: hermes.token.usage by token type](/img/azure/azure-prometheus-explorer.png)

**The Hermes dashboard.** The same turns, live, from the plugin's local store:

![The OTel tab of the Hermes dashboard running on Azure](/img/azure/hermes-dashboard-otel-tab.png)

## The pipeline

Seven stages, about six minutes for a first full run:

1. **Create registry**
2. **Build image**
3. **Preview changes**
4. **Deploy Hermes**
5. **Send test chat**
6. **Find trace in Azure Monitor**
7. **Find metrics in Azure Monitor**

The last two stages print the spans of the chat the pipeline sent and the
metrics the plugin wrote, so a green run is proof that a turn on Azure reached
Azure Monitor on all three signals.

## Limits of the demo

- Hermes's data folder is ephemeral; a new deployment starts with empty
  sessions and an empty OTel tab. Telemetry already in Application Insights is
  not affected.
- The app is reachable from the internet, behind an API key and a dashboard
  password.
