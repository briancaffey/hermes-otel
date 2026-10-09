---
sidebar_position: 18
title: "LangWatch"
description: "LangWatch as a Hermes backend — OTLP/HTTP traces, metrics and logs under /api/otel into a self-hosted LangWatch or LangWatch cloud, threads per session, evaluation workflow."
---

# LangWatch

[LangWatch](https://github.com/langwatch/langwatch) (Apache-2.0) is an LLM observability and evaluation platform whose OTLP routes cover **traces, metrics and logs** under one `/api/otel` prefix. Each Hermes session becomes a thread, every trace shows input, output, token totals and timing, and an onboarding option is dedicated to "AI coding agents".

**Signals:** traces + metrics + logs. **Deployment:** self-hosted (the trimmed compose, five containers, about 1.9 GB resident, five minutes to first health), the official Helm chart, or LangWatch cloud. **Cost:** open source; the cloud has a free tier.

:::tip Verified live
One real `hermes` turn through `type: langwatch` into the bundled compose stack (2026-10-08): three span batches and eight log batches all `SUCCESS`, one metrics flush; LangWatch's ClickHouse then held 5 stored spans, 39 log records and 73 metric data points, and its search API returned the trace with `thread_id` = the Hermes session id. See [Verified behavior](#verified-behavior).
:::

## Quick start

### Self-hosted

```bash
docker compose -f docker-compose/langwatch/docker-compose.yaml up -d      # ≈ 5 min until /api/health answers 204
uv run --with playwright python -m playwright install chromium            # once
export LANGWATCH_API_KEY=$(uv run --with playwright python docker-compose/langwatch/mint-api-key.py)
```

The helper signs up a local user, completes the onboarding wizard (organisation → starting point → first project) and prints the project's `sk-lw-…` key; the same key is under Settings → API keys in the UI.

```yaml
backends:
  - type: langwatch
    endpoint: http://localhost:5560
    api_key_env: LANGWATCH_API_KEY
capture_logs: true
```

The startup banner reads `✓ LangWatch at http://localhost:5560/api/otel/v1/traces`. Open `http://localhost:5560` → the project → Traces.

### LangWatch cloud

```bash
export LANGWATCH_API_KEY="sk-lw-..."
```

```yaml
backends:
  - type: langwatch                       # endpoint defaults to https://app.langwatch.ai
    api_key_env: LANGWATCH_API_KEY
capture_logs: true
```

## Why a dedicated `type: langwatch`

The `/api/otel` prefix means the generic type needs three hand-written URLs, and the key is mandatory. The explicit type:

1. Completes the URL from the base (`http://localhost:5560`, the `/api/otel` prefix, or a full per-signal URL) and derives `/api/otel/v1/metrics` and `/api/otel/v1/logs` from it; defaults to LangWatch cloud.
2. Requires a project API key (`api_key` / `api_key_env` / `OTEL_LANGWATCH_API_KEY` / `LANGWATCH_API_KEY`) and sends `Authorization: Bearer <key>`; adds `X-Project-Id` from `project` for service keys.
3. Keeps all three signals on.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | Base URL (`http://localhost:5560`), the `/api/otel` prefix, or a full traces URL. Default `https://app.langwatch.ai`. Also via `OTEL_LANGWATCH_ENDPOINT` / `LANGWATCH_ENDPOINT`. |
| `api_key` / `api_key_env` | **Required.** Project API key (`sk-lw-…`) → `Authorization: Bearer`. Prefer `api_key_env`. |
| `project` / `project_env` | Optional project id → `X-Project-Id`; only service keys need it. Falls back to `LANGWATCH_PROJECT_ID`. |
| `metrics` / `logs` / `traces` | All default **on**. |
| `headers` | Extra headers, merged on top (a user `Authorization` wins; the legacy `X-Auth-Token` can be added here). |

## Single backend (env vars)

```bash
export OTEL_LANGWATCH_API_KEY="sk-lw-..."          # enables LangWatch; cloud by default
# export OTEL_LANGWATCH_ENDPOINT=http://localhost:5560
export OTEL_PROJECT_NAME=hermes-otel-langwatch
```

The SDK's `LANGWATCH_API_KEY`, `LANGWATCH_ENDPOINT` and `LANGWATCH_PROJECT_ID` fill in values but never switch export on by themselves; an endpoint without a key is skipped.

## Verified behavior

```bash
NOW=$(date +%s000); AUTH="X-Auth-Token: $LANGWATCH_API_KEY"
curl -s -H "$AUTH" -X POST http://localhost:5560/api/traces/search -H 'content-type: application/json' \
  -d "{\"pageSize\":3,\"startDate\":$((NOW-3600000)),\"endDate\":$NOW}"
docker exec hermes-otel-langwatch-clickhouse clickhouse-client --password langwatch -d langwatch \
  -q "select table, sum(rows) from system.parts where database='langwatch' and active group by table"
```

| | Observed |
|---|---|
| Plugin | `✓ LangWatch at http://localhost:5560/api/otel/v1/traces`; 3 span batches and 8 log batches `SUCCESS`; metrics initialised for the backend |
| ClickHouse | `stored_spans` 5, `log_records` 39, `metric_data_points` 73, `trace_summaries` 4 |
| Search API | one trace, `metadata.thread_id` = the Hermes session id, `metrics.total_time_ms` 10 798, `completion_tokens` 262 |

## Caveats

- **Trace-level token totals double-count.** LangWatch sums `gen_ai.usage.*` over every span and the plugin's `agent` span carries the turn's roll-up: the verified trace shows 57 362 prompt tokens for a turn whose API calls total about half. Per-span numbers are right. Tracked in [#327](https://github.com/briancaffey/hermes-otel/issues/327).
- **Slow first start.** About five minutes of ClickHouse migrations before `/api/health` answers 204; `docker compose ps` shows the app `Up` the whole time.
- **Heavy.** About 1.9 GB resident and a 2.8 GB app image; upstream states 4 CPU / 8 GB for the full stack. The bundled stack drops the NLP and evaluator services, so evaluations and topic clustering are unavailable locally.
- The [Hermes dashboard's OTel tab](/dashboard) has no query adapter for `langwatch`; use LangWatch's UI.

## Troubleshooting

- **`langwatch requires api_key`** — no key resolved; run the helper or copy the key from Settings → API keys.
- **`401`** — the key belongs to another project, or a service key without `project`.
- **Nothing in the UI for minutes after `up -d`** — migrations are still running; wait for `curl -i localhost:5560/api/health` to return 204.
