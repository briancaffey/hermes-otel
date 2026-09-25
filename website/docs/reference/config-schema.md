---
sidebar_position: 2
title: "Config schema"
description: "Complete config.yaml schema, field by field."
---

# Config schema

Complete schema for `~/.hermes/hermes_otel.yaml` (or a legacy `~/.hermes/plugins/hermes_otel/config.yaml`, or the file named by `HERMES_OTEL_CONFIG`). See [`config.yaml`](/configuration/yaml) for the narrative version.

The field table below is generated from `HermesOtelConfig` by `scripts/gen_config_docs.py`; a test fails if it drifts.

## Top level

[//]: # (generated:config-fields:start)

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Master kill switch; `false` unloads every hook |
| `sample_rate` | float \| null | `null` | Parent-based trace-ID ratio 0.0–1.0; `null` = AlwaysOn (no sampling) |
| `root_span_ttl_ms` | int | `600000` | Orphan-sweep TTL: a turn root older than this with no end hook is closed |
| `flush_interval_ms` | int | `60000` | Metrics export cadence (PeriodicExportingMetricReader) |
| `preview_max_chars` | int | `1200` | Cap on preview strings (tool args/results, user message, assistant response) |
| `capture_previews` | bool | `true` | Deprecated spelling of `content_capture: off` (when `false`); kept consistent with `content_capture` |
| `tool_input_preview_max_chars` | int \| null | `null` | Per-category cap for tool args previews; `null` = `preview_max_chars` |
| `tool_output_preview_max_chars` | int \| null | `null` | Per-category cap for tool result previews; `null` = `preview_max_chars` |
| `llm_input_preview_max_chars` | int \| null | `null` | Per-category cap for LLM input previews; `null` = `preview_max_chars` |
| `llm_output_preview_max_chars` | int \| null | `null` | Per-category cap for LLM output previews; `null` = `preview_max_chars` |
| `headers` | map | *(unset)* | Extra HTTP headers on every OTLP request; per-backend `headers:` are merged onto these |
| `global_tags` | map | *(unset)* | Merged into the OTel Resource; overridden by `resource_attributes` on key conflict |
| `resource_attributes` | map | *(unset)* | Merged into the Resource on top of the defaults `service.name=hermes-agent`, `service.instance.id` (per-process UUID), `service.version`, `process.pid` |
| `project_name` | string \| null | *(unset)* | `openinference.project.name` on the Resource (Phoenix project); overrides `OTEL_PROJECT_NAME` |
| `span_batch_max_queue_size` | int | `2048` | Max buffered spans per backend before drops |
| `span_batch_schedule_delay_ms` | int | `1000` | BatchSpanProcessor worker wake-up cadence |
| `span_batch_max_export_batch_size` | int \| null | `null` | Max spans per OTLP POST; `null` = 512, or 64 when `content_capture` is `full` |
| `span_batch_export_timeout_ms` | int | `30000` | Per-export HTTP timeout |
| `force_flush_on_session_end` | bool | `true` | Flush every backend's span queue at the end of each turn, from a background thread (500 ms per backend, coalesced) |
| `force_flush_wait_ms` | int | `1500` | How long the turn waits for that background flush (spans, then metrics and logs on a second thread) before returning to Hermes; a one-shot run exits right after, so this bounds what it exports; `0` = do not wait |
| `capture_conversation_history` | bool | `false` | Attach the full message JSON to `llm.*` spans |
| `conversation_history_max_chars` | int | `20000` | JSON cap when conversation capture is on |
| `content_capture` | string | `"full"` | `full` (default): complete prompt and response on every `api.*` span · `preview`: clipped previews only · `off`: no content, metadata only; see [Conversation capture](/configuration/conversation-capture) |
| `capture_full_prompts` | bool | `true` | Deprecated: derived from `content_capture`; `false` keeps prompts as previews in `full` mode |
| `capture_full_responses` | bool | `true` | Deprecated: derived from `content_capture`; `false` keeps responses as previews in `full` mode |
| `capture_sender_id` | bool | `false` | Gateway sessions add `hermes.sender.id` and `user.id` (`platform:sender`) |
| `capture_logs` | bool | `false` | Attach an OTel LoggingHandler to Python logging; see [OTel logs](/configuration/logs) |
| `log_level` | string | `"INFO"` | Handler level: `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `log_attach_logger` | string \| null | *(unset)* | Logger to attach to; `null` = root, `hermes_otel` = the plugin only |
| `emit_genai_metrics` | bool | `true` | Also emit the OTel GenAI spec metrics (`gen_ai.client.*`, `gen_ai.agent.*`) |
| `metrics_temporality` | string \| null | *(unset)* | Metric temporality for OTLP export: `cumulative` (Prometheus family) or `delta` (Datadog, New Relic, Logfire); unset = SDK default; a backend entry's own value wins |
| `metrics_histogram` | string | `"explicit"` | Histogram aggregation: `explicit` (spec bucket boundaries) or `exponential` (base-2, for backends that accept it) |
| `metrics_label_limit` | int | `100` | Distinct values a metric label such as `model` may take per process before the rest fold into `other` (0 = no cap) |
| `skill_spans` | bool | `true` | Open a `skill.<name>` span on each successful skill load, closed at turn end |
| `discovery_prompt` | bool | `false` | Register a system-prompt section advertising `hermes_otel:observability` (changes what the model sees every turn; opt-in) |
| `dashboard_live` | bool | `true` | Keep recent spans/metrics/logs in `$HERMES_HOME/hermes_otel_live.db` for the dashboard's Live mode |
| `dashboard_live_max_spans` | int | `1000` | Rows kept per kind (spans, metrics, logs) in the live store |
| `dashboard_live_retention_hours` | float | `168.0` | Rows older than this are dropped from the live store (`0` = only the row cap applies) |
| `host_metrics` | bool | `false` | Sample CPU/GPU and emit `process.*` / `system.*` / `hw.*` metrics; see [Host & GPU metrics](/configuration/host-metrics) |
| `host_metrics_gpu` | string | `"auto"` | `auto` · `amd` · `nvidia` · `off` — which GPU SDK to probe |
| `host_metrics_interval_ms` | int | `1000` | Host sampling cadence (floor 50 ms) |
| `suppress_mcp_ping_spans` | bool | `true` | Drop successful MCP keepalive `ping` spans before export |
| `backends` | list | *(unset)* | Multi-backend fan-out list; see the `backends[]` section |

[//]: # (generated:config-fields:end)

When `backends:` is present and non-empty, single-backend env-var detection is skipped.

## `backends[]` entries

Shared fields (all optional unless noted):

| Field | Type | Description |
|---|---|---|
| `type` | string | **Required.** One of: `phoenix`, `langfuse`, `signoz`, `jaeger`, `tempo`, `otlp`, `lgtm`, `uptrace`, `openobserve`, `parseable`, `honeycomb`, `weave`. (LangSmith is env-var only — `LANGSMITH_TRACING=true`; it is not an OTLP backend.) |
| `name` | string | Friendly name shown in logs (default: `type`) |
| `endpoint` | string | Full OTLP traces endpoint URL. **Required** for every type except `langfuse` (built from `base_url`), `honeycomb` (built from `region`) and `weave` (built from `base_url`) |
| `ui_url` | string | Where the backend's web UI is, for the dashboard's Settings tab to link its card to. Optional: without it the tab derives the link from `endpoint` for the types whose UI is served on the OTLP origin (Phoenix, Langfuse, OpenObserve, Uptrace, Parseable), from `query_port` for Jaeger and SigNoz, and from a port-less endpoint host for the rest; it never guesses a port |
| `traces` | bool | Override trace-export default (`true`). Set `false` for dashboard/query-only backends that should not receive span exports. `trace` is accepted as an alias. |
| `metrics` | bool | Override metrics-export default for this backend |
| `metrics_temporality` | string | `cumulative` or `delta` for this backend's metric reader; overrides the top-level default and the type preset (`signoz`, `uptrace` → `delta`) |
| `logs` | bool | Override logs-export default (on for `signoz`, `otlp`, `lgtm`, `uptrace`, `openobserve`, `parseable`, `honeycomb`; off elsewhere) |
| `headers` | map | Per-backend HTTP headers (merged onto top-level `headers`) |

### Type-specific fields

#### `phoenix`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** e.g. `http://localhost:6006/v1/traces`. Traces only (Phoenix rejects `/v1/metrics`) |

#### `langfuse`

| Field | Type | Description |
|---|---|---|
| `public_key` | string | Langfuse public key (inline; discouraged) |
| `public_key_env` | string | Env var name holding the public key |
| `secret_key` | string | Langfuse secret key (inline; discouraged) |
| `secret_key_env` | string | Env var name holding the secret key |
| `base_url` | string | Langfuse base URL (e.g. `https://cloud.langfuse.com`); the plugin appends `/api/public/otel/v1/traces` |
| `endpoint` | string | Override; skips `base_url` construction |

Basic Auth header is constructed automatically from public + secret keys.

#### `signoz`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | OTLP endpoint (self-host: `http://localhost:4328/v1/traces`; cloud: `https://ingest.<region>.signoz.cloud:443/v1/traces`) |
| `ingestion_key` | string | SigNoz Cloud ingestion key (inline; discouraged) |
| `ingestion_key_env` | string | Env var name holding the ingestion key |

When an ingestion key is set, the plugin adds the `signoz-ingestion-key` header.

#### `jaeger`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** e.g. `http://localhost:4318/v1/traces`. Traces only (metrics/logs off) |

#### `tempo`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** Traces only (metrics/logs off); use `type: lgtm` for the all-in-one Grafana container |

#### `otlp`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** OTLP traces endpoint |
| `metrics` | bool | Default: `true`; set false for traces-only collectors |
| `logs` | bool | Default: `true`; set false if the collector doesn't accept `/v1/logs` |

Use `headers:` for auth (`${VAR}` interpolation supported).

#### `lgtm`

Alias over `otlp` with a dedicated display name and all signals on by default. See [Grafana LGTM](/backends/lgtm).

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** OTLP traces endpoint — `http://localhost:4318/v1/traces` for the bundled `docker-compose/lgtm.yaml` |
| `metrics` | bool | Default: `true` |
| `logs` | bool | Default: `true` |

Use `type: lgtm` (not `type: tempo`) when pointing at the `grafana/otel-lgtm` container — `tempo` is traces-only and would disable the logs/metrics fan-out.

#### `uptrace`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** OTLP traces endpoint (self-host: `http://localhost:14318/v1/traces`). Also via `OTEL_UPTRACE_ENDPOINT` |
| `dsn` | string | Uptrace DSN, sent as the `uptrace-dsn` header on every export (inline; discouraged) |
| `dsn_env` | string | Env var name holding the DSN (falls back to `OTEL_UPTRACE_DSN` / `UPTRACE_DSN`) |

All three signals on by default. See [Uptrace](/backends/uptrace).

#### `openobserve`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** `http://<host>:5080/api/<org>/v1/traces`. Also via `OTEL_OPENOBSERVE_ENDPOINT` |
| `user` / `user_env` | string | Basic-auth user (falls back to `OTEL_OPENOBSERVE_USER` / `OPENOBSERVE_USER`) |
| `password` / `password_env` | string | Basic-auth password (falls back to `OTEL_OPENOBSERVE_PASSWORD` / `OPENOBSERVE_PASSWORD`) |
| `stream_name` | string | Optional stream name (`OTEL_OPENOBSERVE_STREAM`) |

All three signals on by default. See [OpenObserve](/backends/openobserve).

#### `parseable`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** Parseable ingestor OTLP traces endpoint, ending in `/v1/traces` |
| `api_key` | string | Parseable API key (inline; discouraged) |
| `api_key_env` | string | Env var holding the key (falls back to `PARSEABLE_API_KEY` / `OTEL_PARSEABLE_API_KEY`) |
| `traces_dataset` | string | Traces dataset (default: `hermes-traces`) |
| `metrics_dataset` | string | Metrics dataset (default: `hermes-metrics`) |
| `logs_dataset` | string | Logs dataset (default: `hermes-logs`) |

The plugin supplies signal-specific `X-P-Stream` and `X-P-Log-Source` headers automatically. See [Parseable](/backends/parseable).

#### `honeycomb`

| Field | Type | Description |
|---|---|---|
| `api_key` | string | Honeycomb ingest key (inline; discouraged) |
| `api_key_env` | string | Env var name holding the key (falls back to `HONEYCOMB_API_KEY` / `OTEL_HONEYCOMB_API_KEY`) |
| `region` | string | `us` (default) or `eu`; selects the endpoint when none is given |
| `dataset` | string | Optional `x-honeycomb-dataset` header. Only honored by Classic keys — modern Environments keys ignore it |
| `endpoint` | string | Override; skips the region default. Also via `OTEL_HONEYCOMB_ENDPOINT` |

The plugin sets `x-honeycomb-team` from the key automatically and enables all three signals. See [Honeycomb](/backends/honeycomb) for dataset routing details.

#### `weave`

| Field | Type | Description |
|---|---|---|
| `api_key` | string | W&B API key (inline; discouraged) |
| `api_key_env` | string | Env var name holding the key (falls back to `WANDB_API_KEY`) |
| `entity` | string | W&B entity/team; copied to Resource attribute `wandb.entity` |
| `entity_env` | string | Env var name holding the entity (falls back to `WANDB_ENTITY` / `DEFAULT_WANDB_ENTITY`) |
| `project` | string | W&B project; copied to Resource attribute `wandb.project` |
| `project_env` | string | Env var name holding the project (falls back to `WANDB_PROJECT` / `DEFAULT_WANDB_PROJECT`) |
| `base_url` | string | W&B base URL. Cloud default is `https://trace.wandb.ai`; Dedicated Cloud / Self-Managed hosts such as `https://acme.wandb.io` get `/traces/otel/v1/traces` appended |
| `endpoint` | string | Override; skips `base_url` construction. Also via `OTEL_WEAVE_ENDPOINT` / `WANDB_OTLP_ENDPOINT` |

The plugin sets the `wandb-api-key` header automatically. Weave is traces-only by default; set `metrics: true` / `logs: true` only if W&B documents ingest for those signals or you are pointing at a compatible collector. `wandb.entity` and `wandb.project` may also be supplied via top-level `resource_attributes`; conflicting values fail startup.

## Env var interpolation in `headers:`

Inside any `headers:` value — and any other string field of a `backends:` entry, such as `api_key`, `dsn`, `password` or `endpoint` — `${VAR_NAME}` is replaced with the env var's value when the config is loaded:

```yaml
headers:
  Authorization: "Bearer ${OTEL_AUTH_TOKEN}"
  x-honeycomb-team: ${HONEYCOMB_API_KEY}
```

Missing env vars result in a startup warning and the literal `${VAR}` being sent (which will fail auth, but visibly so).

## Precedence

For every field, precedence (highest → lowest) is:

1. `HERMES_OTEL_*` env var (if applicable — see [Env var reference](/reference/env-vars))
2. `config.yaml` value
3. Built-in default

## Validation

On startup the plugin validates the config and:

- Logs a warning naming the key and keeps the default for a value that cannot be parsed as the field's type (yaml or `HERMES_OTEL_*` env var)
- Logs a single warning and uses an empty config if the YAML fails to parse
- Logs a warning and ignores the file if it exists but `pyyaml` is not installed in the Hermes venv
- Logs a warning and skips a `backends:` entry it cannot resolve (missing endpoint, unknown type)

The plugin never crashes Hermes because of config — at worst it disables itself with a clear log line.
