---
sidebar_position: 9
title: "ClickStack"
description: "Send Hermes traces, metrics and logs to ClickStack (HyperDX) — ClickHouse's open-source observability stack in one container, read back by the dashboard adapter straight from ClickHouse."
---

# ClickStack (HyperDX)

[ClickStack](https://clickhouse.com/docs/use-cases/observability/clickstack) (MIT) is ClickHouse's open-source observability stack: ClickHouse, an OpenTelemetry Collector, MongoDB and the HyperDX UI, shipped as one all-in-one image or as a four-container compose file. It stores **all three signals** in the standard OTel ClickHouse-exporter tables, and the plugin's dashboard adapter reads those tables directly.

**Signals:** traces + metrics + logs. **Deployment:** local (one container) · the upstream multi-container compose · Helm · ClickHouse Cloud. **Cost:** open source, no account beyond the local one the stack creates.

:::tip Verified live
One real `hermes` turn through `type: clickstack` into the bundled compose stack (all-in-one 2.40.0, ClickHouse 26.8, 2026-10-11) stored 7 spans, 28 log records, 6 sum and 7 histogram metrics, and the dashboard-verify harness read every signal back through the adapter. See [Verified behavior](#verified-behavior).
:::

## Quick start

```bash
docker compose -f docker-compose/clickstack/docker-compose.yaml up -d
export OTEL_CLICKSTACK_API_KEY="$(docker-compose/clickstack/mint-api-key.sh)"
```

The script creates the first HyperDX account headlessly, reads the team's **ingestion API key** and waits for the Collector's OTLP receivers (they only start once a team exists). Then declare the backend:

```yaml
backends:
  - type: clickstack
    endpoint: http://localhost:4408          # the Collector's OTLP/HTTP (4318 in the container)
    api_key_env: OTEL_CLICKSTACK_API_KEY
    query_port: 8124                         # dashboard read path: ClickHouse over HTTP
    user: api
    password: api
    ui_url: http://localhost:3050
capture_logs: true
```

The startup banner reads `✓ ClickStack at http://localhost:4408/v1/traces`. Open http://localhost:3050 (log in with the account the script created, `hermes@example.com` / `HermesOtel-local-1` by default) and the turn is under Traces, with Logs and Metrics next to it.

For a ClickStack elsewhere, point `endpoint` at its Collector's OTLP/HTTP port and take the key from Team Settings → API Keys.

## Why a dedicated `type: clickstack`

Export works through the [generic OTLP](/backends/otlp) type with a hand-written `authorization` header, which is also the vendor's own recipe. Declaring `type: clickstack` instead asks the plugin to:

1. Complete `endpoint` to the traces URL (a base URL gets `/v1/traces`) and derive the metrics and logs URLs from it.
2. Read the ingestion key from `api_key` / `api_key_env` (falling back to `OTEL_CLICKSTACK_API_KEY` / `HYPERDX_API_KEY`) and send it as a **bare** `authorization: <key>` header, the spelling the Collector's `bearertokenauth` extension is configured with (a `Bearer` prefix is accepted too).
3. Skip the entry at startup when no key resolves, with the hint, instead of filling the debug log with 401s.
4. Enable all three signals by default, with cumulative metrics (the ClickHouse exporter stores every point as sent).
5. Enable the [dashboard adapter](/dashboard) for the entry.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | **Required.** The Collector's OTLP/HTTP base, e.g. `http://localhost:4408` (bundled stack) or `http://<host>:4318`. Also via `OTEL_CLICKSTACK_ENDPOINT`. |
| `api_key` / `api_key_env` | **Required.** The team's ingestion API key (fallbacks `OTEL_CLICKSTACK_API_KEY`, `HYPERDX_API_KEY`). Prefer `api_key_env`. |
| `metrics` / `logs` / `traces` | Per-signal toggles; all three default **on**. |
| `headers` | Extra headers, merged on top of the generated ones. |
| `query_port` | Dashboard read path: the ClickHouse HTTP port (default `8123`; `8124` on the bundled stack). |
| `user` / `user_env`, `password` / `password_env` | Dashboard read path: ClickHouse credentials. The all-in-one's `default` user only answers from inside the container; its `api` / `api` user answers from the host. |
| `database` | Dashboard read path: the ClickHouse database (default `default`). |
| `traces_table`, `logs_table` | Dashboard read path: table names (defaults `otel_traces`, `otel_logs`). |
| `metrics_match` | Dashboard read path: a regular expression on metric names (default: the plugin's namespaces, which keeps the Collector's own `otelcol_*` gauges out). |
| `ui_url` | Dashboard read path: the HyperDX UI origin, for an "open in" link to its search page. |
| `service_name` | Dashboard read path: the service every trace search names (default: the plugin's `resource_attributes.service.name`, else `hermes-agent`). |

No metric-temporality preset.

## Single backend (env vars)

```bash
export OTEL_CLICKSTACK_ENDPOINT=http://localhost:4408
export OTEL_CLICKSTACK_API_KEY=...          # or HYPERDX_API_KEY, the vendor SDK's variable
export OTEL_PROJECT_NAME=hermes-otel-clickstack
```

`HYPERDX_API_KEY` alone never switches export on (it is often set for the HyperDX SDK); the plugin-namespaced endpoint is the opt-in.

## What you'll see

HyperDX indexes whatever arrives: every span attribute is searchable (`SpanAttributes.gen_ai.usage.input_tokens:>1000`), the trace view shows the `agent` → `llm.*` → `api.*` / `tool.*` tree, and log lines written inside a span link to it through `TraceId` / `SpanId`. Nothing is LLM-specific; the plugin emits both the OpenInference (`llm.*`) and OTel GenAI (`gen_ai.*`) spellings, so either search works.

In ClickHouse (`default` database):

- `otel_traces`: one row per span, `SpanAttributes` and `ResourceAttributes` as `Map(String, String)` (every value a string), `Duration` in ns, `StatusCode` `Ok` / `Error` / `Unset`, `Events.*` arrays, `ParentSpanId` empty on the root.
- `otel_logs`: `Body`, `SeverityText` (lower-cased by the Collector), `SeverityNumber`, `ScopeName` (the Python logger), `TraceId`, `SpanId`, `LogAttributes` map.
- `otel_metrics_sum`, `otel_metrics_gauge`, `otel_metrics_histogram` (`Count`, `Sum`, `BucketCounts`): keyed by the **dotted** `MetricName` (`hermes.token.usage`), `TimeUnix` in seconds, `Attributes` map with the original keys (`gen_ai.tool.name`, `token_type`).

## Dashboard adapter

The [Hermes dashboard's OTel tab](/dashboard) reads ClickHouse over its HTTP interface (`query_port`, Basic auth from `user` / `password`):

- **Traces**: SQL on `otel_traces`. Every search-bar field becomes a server-side predicate, including roots-only (`ParentSpanId = ''`), `status = ok`, free text (`SpanAttributes['input.value']` / `output.value` `ILIKE`) and a raw SQL predicate ANDed into the `WHERE`.
  - A kind or tool filter widens the search to every span and keeps the earliest match in each trace. With a kind filter that span is the card; with a tool filter alone the card is the trace's root (one more query), carrying the matched span's attributes.
  - String values are turned back into numbers and booleans, except on id-like keys.
  - Each trace links to HyperDX's search page when `ui_url` is set.
- **Metrics**: dotted names as stored; histograms are listed as `<name>.sum` and `<name>.count` so the curated panels find them. Counters and histogram sums / counts are read as raw cumulative samples with a five-minute look-behind and turned into per-bucket increases with first-sample counting; gauges stay raw. `group_by` takes the attribute key in either spelling (`tool.name` finds `tool_name`).
- **Logs**: SQL on `otel_logs` with trace, span, logger, severity, text, session (`LogAttributes['hermes.session_id']`) and event (`LogAttributes['event.name']`) filters.

## Verified behavior

| Check | Result |
|---|---|
| Key gate | `/v1/traces` without a key → 401; bare key → 200; `Bearer <key>` → 200; wrong key → 401 `provided authorization does not match expected scheme or token` |
| Receivers | 4317 / 4318 absent until the first team exists, up about 20 s after registration |
| Spans | 7 in `otel_traces` (`agent` root, `llm.nvidia/…`, three `api.nvidia/…`, `tool.terminal`, `tool.read_file`), all `Internal` / `Ok` |
| Logs | 28 rows, 12 with a `TraceId`; `hermes.session_id` in `LogAttributes` |
| Metrics | sums `hermes.token.usage`, `hermes.model.usage`, `hermes.session.count`, `hermes.message.count`, `hermes.prompt_cache.*`; histograms `hermes.tool.duration`, `hermes.session.duration`, `hermes.session.turns`, `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, `gen_ai.execute_tool.duration`, `gen_ai.agent.token.usage`; the Collector's `otelcol_*` gauges beside them |
| Dashboard verify harness | fresh four-turn batch: **PASS 51**, no skips (traces, metrics, logs, filters, paging, the HyperDX link and the browser checks) |
| Resident memory | about 1.1 GB (four processes) |

## Caveats

- **The ingestion key is mandatory** and minted per team; `mint-api-key.sh` is the headless route for the all-in-one image (registration API + the key from MongoDB). The all-in-one image has no documented env var to pre-seed it.
- **An account is mandatory** on the published image; the Dockerfile has an `all-in-one-noauth` target without a published tag.
- Every map value is a string; ints, bools and doubles come back coerced by the adapter, not by ClickHouse.
- Retention defaults to 3 days (`TABLES_TTL`).
- Upstream calls the all-in-one image unfit for production (four processes in one container); the four-container compose file in the HyperDX repo is the production shape, and the same `type: clickstack` entry drives it (OTLP on its Collector, ClickHouse on its own port).
- Image size 2.6 GB; about 1.1 GB resident.

## Troubleshooting

- **`clickstack requires api_key`** — no key resolved; run `mint-api-key.sh` or copy it from Team Settings → API Keys.
- **Every export is a 401** — wrong team key, or the key was regenerated in the UI.
- **`connection refused` on 4408 right after start** — the Collector's receivers appear only once a team exists; create the account (or run the script) and retry.
- **Dashboard: `ClickHouse rejected the credentials`** — the `default` user is localhost-only on the all-in-one image; set `user: api`, `password: api`.
