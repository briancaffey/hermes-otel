---
sidebar_position: 11
title: "Parseable"
description: "Send Hermes traces, metrics and logs to Parseable and inspect agent runs in Agent Observability."
---

# Parseable

[Parseable](https://www.parseable.com) accepts Hermes telemetry over
OTLP/HTTP. The dedicated backend sends traces, metrics and logs to separate
datasets and supplies the Parseable routing headers for each signal.

## Prerequisites

- A Parseable ingestor URL
- An API key with dataset creation and ingest access
- Three datasets: `hermes-traces`, `hermes-metrics` and `hermes-logs`

Tag `hermes-traces` with `agent-observability` when creating it. This makes
the dataset available in Parseable's Agents view. The
[official Hermes guide](https://www.parseable.com/docs/ingest-data/ai-agents/hermes)
includes dataset-creation commands and verification steps.

## Configure hermes-otel

```yaml
backends:
  - type: parseable
    endpoint: https://parseable.example.com/v1/traces
    api_key_env: PARSEABLE_API_KEY
    traces_dataset: hermes-traces
    metrics_dataset: hermes-metrics
    logs_dataset: hermes-logs

capture_logs: true
```

```bash
export PARSEABLE_API_KEY="<parseable-api-key>"
```

Use the Parseable **ingestor** URL. In distributed deployments, the query/UI
endpoint may reject ingestion. Keep `/v1/traces` on the configured endpoint;
hermes-otel derives `/v1/metrics` and `/v1/logs` for the other exporters.

The dataset fields are optional and default to the names shown above. The
plugin adds these headers automatically:

| Signal  | Dataset header               | Source header                  |
| ------- | ---------------------------- | ------------------------------ |
| Traces  | `X-P-Stream: hermes-traces`  | `X-P-Log-Source: otel-traces`  |
| Metrics | `X-P-Stream: hermes-metrics` | `X-P-Log-Source: otel-metrics` |
| Logs    | `X-P-Stream: hermes-logs`    | `X-P-Log-Source: otel-logs`    |

All three exporters authenticate with `X-API-Key`. Prefer `api_key_env` over
an inline key so credentials do not enter source control.

## Environment-only setup

Without a `backends:` list, hermes-otel can detect Parseable from:

```bash
export OTEL_PARSEABLE_ENDPOINT="https://parseable.example.com/v1/traces"
export PARSEABLE_API_KEY="<parseable-api-key>"
```

Optional dataset overrides are `PARSEABLE_TRACES_DATASET`,
`PARSEABLE_METRICS_DATASET` and `PARSEABLE_LOGS_DATASET`.

## Verify

Run a Hermes invocation, then select `hermes-traces` under **Traces** or
**Agents** in Parseable. A complete run contains a root `agent` span plus
nested `llm.*`, `api.*`, `tool.*` and `skill.*` spans when applicable.

Parseable also publishes a
[Hermes Agent Observability dashboard](https://github.com/parseablehq/dashboards/tree/main/hermes-agent-observability)
covering agent runs, tokens, model calls, tools, latency, errors, traces,
logs and metrics.
