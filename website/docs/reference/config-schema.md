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
| `log_exclude_loggers` | list of strings (env: comma-separated) | `('opentelemetry', 'urllib3', 'httpx', 'httpcore', 'requests', 'openai', 'asyncio', 'hpack', 'grpc', 'websockets', 'charset_normalizer', 'markdown_it')` | Logger-name prefixes never forwarded (loop guard + Hermes' noisy third-party loggers); `logs.exclude_loggers` |
| `log_logger_levels` | map | *(unset)* | Per-logger minimum level, e.g. `{gateway.config: WARNING}`; `logs.logger_levels` |
| `log_only_in_turn` | bool | `false` | Drop records the plugin cannot attribute to a session; `logs.only_in_turn` |
| `log_max_attribute_length` | int | `4096` | Longest exported string attribute on a log record (characters); `logs.max_attribute_length` |
| `log_batch_schedule_delay_ms` | int | `1000` | Log batch processor: export every N ms; `logs.batch.schedule_delay_ms` |
| `log_batch_max_queue_size` | int | `2048` | Log batch processor: queued records before drops; `logs.batch.max_queue_size` |
| `log_batch_max_export_batch_size` | int | `512` | Log batch processor: records per export request; `logs.batch.max_export_batch_size` |
| `log_batch_export_timeout_ms` | int | `30000` | Log batch processor: per-export timeout; `logs.batch.export_timeout_ms` |
| `log_live_min_level` | string | `"INFO"` | Floor for the dashboard's live Logs tab (`INFO` default; the OTLP path keeps `log_level`); `logs.live_min_level` |
| `log_events` | bool | `false` | Emit structured `hermes.*` / GenAI events from the hooks (Phase 2); `logs.events.enabled` |
| `log_events_content` | string | `"inherit"` | Content on events: `inherit` (follow `content_capture`) / `full` / `preview` / `off`; `logs.events.content` |
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
| `root_usage` | bool | Send the turn's token roll-up (`gen_ai.usage.*` / `llm.token_count.*` on the `agent` / `cron` root) to this backend. Preset `false` for `opik` and `langwatch`, which sum usage over every span and would double the trace total ([#327](https://github.com/briancaffey/hermes-otel/issues/327)); `true` everywhere else. Only this entry's exporter is affected: the span is rebuilt without those keys on its way out, the `api.*` spans and `hermes.cost.*` stay |
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
| `query_api` | string | Dashboard read path: `v3` (trace endpoints), `v4` (`events_only`: v2 observations + v2 metrics), or `auto` (default: probed once per query URL) |
| `endpoint` | string | Override; skips `base_url` construction |

Basic Auth header is constructed automatically from public + secret keys.

#### `signoz`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | OTLP endpoint (self-host: `http://localhost:4328/v1/traces`; cloud: `https://ingest.<region>.signoz.cloud:443/v1/traces`) |
| `ingestion_key` | string | SigNoz Cloud ingestion key (inline; discouraged) |
| `ingestion_key_env` | string | Env var name holding the ingestion key |
| `query_port` | int | Dashboard read path: the SigNoz UI/API port (default `3301`; `443` behind an HTTPS ingress) |
| `api_key` / `api_key_env` | string | Dashboard read path: a SigNoz API key, sent as `SIGNOZ-API-KEY` (current SigNoz issues them from a service account). Ingestion never needs it |

When an ingestion key is set, the plugin adds the `signoz-ingestion-key` header.

#### `jaeger`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** e.g. `http://localhost:4318/v1/traces`. Traces only (metrics/logs off) |
| `query_port` | int | Dashboard read path: the Jaeger UI/query port (default `16686`) |
| `query_api` | string | Dashboard read path: `v1` (classic `/api/traces`), `v3` (Jaeger v2's `/api/v3`), or `auto` (default: probed once per query URL) |
| `service_name` | string | Dashboard read path: the service every search names (default: the plugin's `resource_attributes.service.name`, else `hermes-agent`) |

#### `tempo`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** Traces only (metrics/logs off); use `type: lgtm` for the all-in-one Grafana container |
| `query_port` | int | Dashboard read path: Tempo's query API port (default `3200`) |
| `prometheus_url` / `loki_url` | string | Optional. Naming them gives a Tempo entry the same dashboard metrics/logs as `lgtm` |
| `search_fetch` | int | Dashboard read path: traces asked of Tempo per search page before the newest `limit` are kept (default `500`; Tempo's own `limit` is not newest-first). Also on `lgtm` entries |

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
| `endpoint` | string | **Required.** OTLP traces endpoint — `http://localhost:4318/v1/traces` for the bundled `docker-compose/lgtm/docker-compose.yaml` |
| `metrics` | bool | Default: `true` |
| `logs` | bool | Default: `true` |
| `query_port` | int | Dashboard read path: Tempo's query API port (default `3200`) |
| `prometheus_url` | string | Dashboard metrics. Default: port `9090` on the Tempo host (the otel-lgtm layout); `off` disables |
| `loki_url` | string | Dashboard logs. Default: port `3100` on the Tempo host; `off` disables (e.g. a Loki without OTLP) |
| `metrics_match` | string | Optional Prometheus series selector for the instrument list, e.g. `{__name__=~"hermes_.*"}` on a shared Prometheus |
| `loki_selector` | string | Optional LogQL stream selector the log filters are applied to (default `{service_name=~".+"}`) |

Use `type: lgtm` (not `type: tempo`) when pointing at the `grafana/otel-lgtm` container — `tempo` is traces-only and would disable the logs/metrics fan-out.

#### `uptrace`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** OTLP traces endpoint (self-host: `http://localhost:14318/v1/traces`). Also via `OTEL_UPTRACE_ENDPOINT` |
| `dsn` | string | Uptrace DSN, sent as the `uptrace-dsn` header on every export (inline; discouraged) |
| `dsn_env` | string | Env var name holding the DSN (falls back to `OTEL_UPTRACE_DSN` / `UPTRACE_DSN`) |
| `query_port` | int | Dashboard read path: the Uptrace UI/API port (default `14318`; `443` behind an HTTPS ingress) |
| `user_token` / `user_token_env` | string | Dashboard read path: an Uptrace **user** token (Settings → API tokens); falls back to `UPTRACE_USER_TOKEN`. The DSN's project token only ingests |
| `project_id` | int | Dashboard read path: the Uptrace project to query (default `1`) |

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
| `api_key_env` | string | Env var name holding the key (falls back to `OTEL_HONEYCOMB_API_KEY` / `HONEYCOMB_API_KEY`) |
| `region` | string | `us` (default) or `eu`; selects the endpoint when none is given |
| `dataset` | string | Optional `x-honeycomb-dataset` header. Only honored by Classic keys — modern Environments keys ignore it |
| `endpoint` | string | Override; skips the region default. Also via `OTEL_HONEYCOMB_ENDPOINT` |

The plugin sets `x-honeycomb-team` from the key automatically and enables all three signals. See [Honeycomb](/backends/honeycomb) for dataset routing details.

#### `weave`

| Field | Type | Description |
|---|---|---|
| `api_key` | string | W&B API key (inline; discouraged) |
| `api_key_env` | string | Env var name holding the key (falls back to `OTEL_WEAVE_API_KEY` / `WANDB_API_KEY`) |
| `entity` | string | W&B entity/team; copied to Resource attribute `wandb.entity` |
| `entity_env` | string | Env var name holding the entity (falls back to `WANDB_ENTITY` / `DEFAULT_WANDB_ENTITY`) |
| `project` | string | W&B project; copied to Resource attribute `wandb.project` |
| `project_env` | string | Env var name holding the project (falls back to `WANDB_PROJECT` / `DEFAULT_WANDB_PROJECT`) |
| `base_url` | string | W&B base URL. Cloud default is `https://trace.wandb.ai`; Dedicated Cloud / Self-Managed hosts such as `https://acme.wandb.io` get `/traces/otel/v1/traces` appended |
| `endpoint` | string | Override; skips `base_url` construction. Also via `OTEL_WEAVE_ENDPOINT` / `WANDB_OTLP_ENDPOINT` |

The plugin sets the `wandb-api-key` header automatically. Weave is traces-only by default; set `metrics: true` / `logs: true` only if W&B documents ingest for those signals or you are pointing at a compatible collector. `wandb.entity` and `wandb.project` may also be supplied via top-level `resource_attributes`; conflicting values fail startup.

#### `elastic`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** Elastic Cloud managed OTLP URL (`https://<deployment>.ingest.<region>.<csp>.elastic.cloud`) or a self-hosted EDOT Collector's OTLP/HTTP base. Also via `OTEL_ELASTIC_ENDPOINT`. A base URL gets `/v1/traces` appended; `/v1/metrics` and `/v1/logs` are derived from it |
| `api_key` | string | Elastic API key (inline; discouraged). Sent as `Authorization: ApiKey <key>`; the header is omitted entirely when no key resolves (a local collector) |
| `api_key_env` | string | Env var name holding the key (falls back to `OTEL_ELASTIC_API_KEY` / `ELASTIC_API_KEY`) |
| `dataset` | string | Optional; copied to the `data_stream.dataset` Resource attribute. Lowercase letters, digits, `_` and `.`, no `-`, at most 100 characters. The collector appends `.otel` |
| `namespace` | string | Optional; copied to the `data_stream.namespace` Resource attribute, same character rules |
| `metrics_temporality` | string | Preset `delta` for this type (Elasticsearch does not take cumulative histograms); `cumulative` overrides |

An invalid `dataset` / `namespace` skips the entry at startup with a warning rather than failing the agent. Both attributes are set on the shared Resource, so every configured backend receives them; two `elastic` entries with different values conflict at startup. See [Elastic](/backends/elastic).

#### `openlit`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** The OpenLIT OTLP/HTTP receiver base: `http://localhost:4338` for the bundled stack, `http://<host>:4318` upstream (the UI port does not proxy OTLP on the 2.1.0 image). Also via `OTEL_OPENLIT_ENDPOINT`. A base URL gets `/v1/traces` appended; `/v1/metrics` and `/v1/logs` are derived from it |
| `api_key` | string | OpenLIT API key (inline; discouraged). Sent as `Authorization: Bearer <key>` to scope ingest to an organisation, project and environment; the header is omitted when no key resolves |
| `api_key_env` | string | Env var name holding the key (falls back to `OTEL_OPENLIT_API_KEY` / `OPENLIT_API_KEY`) |
| `metrics` / `logs` / `traces` | bool | Per-signal toggles; all three default **on** |

See [OpenLIT](/backends/openlit).

#### `mlflow`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** MLflow tracking server (`http://localhost:5001` for the bundled stack, `http://<host>:5000` upstream). Also via `OTEL_MLFLOW_ENDPOINT`. A base URL gets `/v1/traces` appended |
| `experiment_id` | string | Experiment that receives the traces, sent as the mandatory `x-mlflow-experiment-id` header. Default `0` (the built-in Default experiment) |
| `experiment_id_env` | string | Env var name holding the experiment id (falls back to `OTEL_MLFLOW_EXPERIMENT_ID` / `MLFLOW_EXPERIMENT_ID`) |
| `workspace` | string | Optional; sent as `X-MLFLOW-WORKSPACE` |
| `api_key` / `api_key_env` | string | Optional tracking token, sent as `Authorization: Bearer <token>` (falls back to `OTEL_MLFLOW_API_KEY` / `MLFLOW_TRACKING_TOKEN`) |
| `metrics` / `logs` | bool | Default **off**: the server has no `/v1/metrics` or `/v1/logs` route (404) |

See [MLflow](/backends/mlflow).

#### `opik`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | Opik base URL: `http://localhost:5173` for the bundled stack, or a full `…/api/v1/private/otel/v1/traces` URL. Default `https://www.comet.com/opik` (Comet cloud). Also via `OTEL_OPIK_ENDPOINT` or the SDK's `OPIK_URL_OVERRIDE` |
| `api_key` / `api_key_env` | string | Comet API key, sent as a bare `Authorization: <key>` header (Opik's scheme; falls back to `OTEL_OPIK_API_KEY` / `OPIK_API_KEY`). Not needed on a self-hosted deployment |
| `workspace` | string | Comet workspace → `Comet-Workspace` header (cloud; falls back to `OPIK_WORKSPACE`) |
| `project` / `project_env` | string | Opik project → `projectName` header (falls back to `OPIK_PROJECT_NAME`; Opik uses "Default Project" when absent) |
| `metrics` / `logs` | bool | Default **off**: only `/traces` exists under the OTLP prefix |

See [Comet Opik](/backends/opik).

#### `laminar`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | Laminar app-server base: `http://localhost:8100` for the bundled stack (upstream publishes 8000; the frontend port 5667 does not ingest). Default `https://api.lmnr.ai` (Laminar cloud). Also via `OTEL_LAMINAR_ENDPOINT` / `LMNR_BASE_URL` |
| `api_key` / `api_key_env` | string | **Required.** Project API key, sent as `Authorization: Bearer <key>` (falls back to `OTEL_LAMINAR_API_KEY` / `LMNR_PROJECT_API_KEY`). The key names the project |
| `metrics` | bool | Default **off**: `/v1/metrics` answers 200 and stores nothing. `true` only with a collector in front |
| `logs` | bool | Default **on** (log records are stored) |

See [Laminar](/backends/laminar).

#### `langwatch`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | LangWatch base: `http://localhost:5560` for the bundled stack, or the `/api/otel` prefix, or a full per-signal URL. Default `https://app.langwatch.ai`. Also via `OTEL_LANGWATCH_ENDPOINT` / `LANGWATCH_ENDPOINT` |
| `api_key` / `api_key_env` | string | **Required.** Project API key (`sk-lw-…`), sent as `Authorization: Bearer <key>` (falls back to `OTEL_LANGWATCH_API_KEY` / `LANGWATCH_API_KEY`) |
| `project` / `project_env` | string | Optional project id → `X-Project-Id`, needed only with a service key (falls back to `LANGWATCH_PROJECT_ID`) |
| `metrics` / `logs` / `traces` | bool | All three default **on**; the plugin derives `/api/otel/v1/metrics` and `/api/otel/v1/logs` from the traces URL |

See [LangWatch](/backends/langwatch).

#### `latitude`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | Latitude ingest base: `http://localhost:3002` for the bundled stack. Default `https://ingest.latitude.so` (Latitude cloud). Also via `OTEL_LATITUDE_ENDPOINT` / `LATITUDE_INGEST_URL`. A base URL gets `/v1/traces` appended |
| `api_key` / `api_key_env` | string | **Required.** API key, sent as `Authorization: Bearer <key>` (falls back to `OTEL_LATITUDE_API_KEY` / `LATITUDE_API_KEY`) |
| `project` / `project_env` | string | **Required.** Project slug for the mandatory `X-Latitude-Project` header (falls back to `LATITUDE_PROJECT`); spans without a project are rejected |
| `metrics` / `logs` | bool | Default **off**: the ingest service has only `/v1/traces` |

See [Latitude](/backends/latitude).

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
