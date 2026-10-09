---
sidebar_position: 17
title: "Laminar"
description: "Laminar as a Hermes backend — OTLP/HTTP traces and logs into a self-hosted Laminar app-server or Laminar cloud, with span types, tokens and cost per call."
---

# Laminar

[Laminar](https://github.com/lmnr-ai/lmnr) (Apache-2.0) is an agent-focused observability platform with cloud and self-hosted editions that take OTLP over HTTP directly. It types the plugin's spans (LLM for `api.*` and `llm.*`, TOOL for `tool.*`), fills token and cost columns from the `gen_ai.usage.*` attributes, and, beyond what its docs promise, stores the plugin's log records next to the trace.

**Signals:** traces + logs. Metrics are **off by default** because Laminar's `/v1/metrics` answers 200 and stores nothing. **Deployment:** self-hosted (the "lite" compose, five containers, about 600 MB resident), the official Helm chart, or Laminar cloud. **Cost:** open source; the cloud has a free tier.

:::tip Verified live
One real `hermes` turn through `type: laminar` into the bundled compose stack (2026-10-08): 5 spans and 39 log records, every batch `SUCCESS`, read back from Laminar's ClickHouse with span types and token counts filled. See [Verified behavior](#verified-behavior).
:::

## Quick start

### Self-hosted

```bash
docker compose -f docker-compose/laminar/docker-compose.yaml up -d
export LMNR_PROJECT_API_KEY=$(docker-compose/laminar/mint-api-key.sh)   # or Settings → API keys in the UI
```

```yaml
backends:
  - type: laminar
    endpoint: http://localhost:8100       # the app-server, not the UI on 5667
    api_key_env: LMNR_PROJECT_API_KEY
capture_logs: true
```

The startup banner reads `✓ Laminar at http://localhost:8100/v1/traces (traces + logs)`. Open `http://localhost:5667` (enter any email; local sign-in has no password) → the project → Traces.

### Laminar cloud

```bash
export LMNR_PROJECT_API_KEY="..."
```

```yaml
backends:
  - type: laminar                         # endpoint defaults to https://api.lmnr.ai
    api_key_env: LMNR_PROJECT_API_KEY
capture_logs: true
```

## Why a dedicated `type: laminar`

The key is required on both editions (it is what names the project), metrics are silently discarded, and the ingest host differs from the UI host. The explicit type:

1. Requires a project API key (`api_key` / `api_key_env` / `OTEL_LAMINAR_API_KEY` / `LMNR_PROJECT_API_KEY`) and sends `Authorization: Bearer <key>`; a missing key skips the entry at startup with a clear message instead of a stream of 401s.
2. Defaults `endpoint` to the cloud and normalises a self-hosted base to `/v1/traces`, deriving `/v1/logs` from it.
3. Keeps **traces and logs on** and **metrics off**, so a user is not left wondering where their metrics went.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | App-server base: `http://localhost:8100` for the bundled stack (upstream publishes 8000). Default `https://api.lmnr.ai`. Also via `OTEL_LAMINAR_ENDPOINT` / `LMNR_BASE_URL`. |
| `api_key` / `api_key_env` | **Required.** Project API key → `Authorization: Bearer`. Prefer `api_key_env`. |
| `metrics` | Default **off**. Set `true` only with a collector in front of Laminar that stores them. |
| `logs` | Default **on**. |
| `headers` | Extra headers, merged on top (a user `Authorization` wins). |

## Single backend (env vars)

```bash
export OTEL_LAMINAR_API_KEY="..."              # enables Laminar; cloud by default
# export OTEL_LAMINAR_ENDPOINT=http://localhost:8100   # self-hosted app-server
export OTEL_PROJECT_NAME=hermes-otel-laminar
```

`LMNR_PROJECT_API_KEY` and `LMNR_BASE_URL` are used as fallbacks but never switch export on by themselves; `OTEL_LAMINAR_ENDPOINT` without a key is skipped.

## Verified behavior

Read back from the stack's ClickHouse after one turn:

```bash
docker exec hermes-otel-laminar-clickhouse clickhouse-client --user ch_user --password ch_passwd \
  -q "SELECT name, span_type, input_tokens, output_tokens FROM spans ORDER BY start_time DESC LIMIT 8; SELECT count() FROM logs"
```

| | Observed |
|---|---|
| Plugin | `export Laminar: 3 span(s) -> SUCCESS`, two single-span batches, nine log batches (39 records) all `SUCCESS`; no metrics reader created |
| Spans | 5: the root (`hermes-agent`), `llm.<model>` and two `api.<model>` typed LLM with `input_tokens` 14 281 / 14 413 and `output_tokens` 93 / 43, `terminal` typed TOOL |
| Logs | 39 records in the `logs` table with `trace_id` and `span_id` |
| Cost | 0 for this run: the model (`nvidia/nemotron-3-nano-omni`) is not in Laminar's price table |

## Caveats

- **Metrics are accepted and dropped.** `/v1/metrics` returns 200 with a placeholder handler; keep the default. Token, tool and cost **metrics** need a second backend ([multi-backend](/backends/multi-backend)).
- **The bundled stack adds a Quickwit init job** that upstream's published images miss; without it span full-text search never indexes. Trace views read ClickHouse and work either way.
- **Ingest is on the app-server port** (8100 bundled, 8000 upstream), not the frontend (5667).
- The published container images are `latest` only; upstream tags no releases for them.
- The [Hermes dashboard's OTel tab](/dashboard) has no query adapter for `laminar`; use Laminar's UI.

## Troubleshooting

- **`laminar requires api_key`** — no key resolved; set `api_key_env` or `LMNR_PROJECT_API_KEY` (the helper script prints one for the bundled stack).
- **`401`** — the key belongs to another project or deployment; mint one in Settings → API keys of the target project.
- **Spans arrive, search finds nothing** — the Quickwit indexes are missing (see Caveats); `docker compose … logs quickwit-init`.
