---
sidebar_position: 1
title: "Env var reference"
description: "Every environment variable the plugin reads, grouped by purpose."
---

# Env var reference

Complete list. See [Environment variables](/configuration/environment-variables) for where to set them and how precedence works. The `HERMES_OTEL_*` table is generated from `HermesOtelConfig` by `scripts/gen_config_docs.py`; a test fails if it drifts.

## Backend selection

Generated from `hermes_otel/backend_specs.py` by `scripts/gen_config_docs.py`; a test fails if it drifts. The `OTEL_*` spelling of a vendor's key is the env-var-mode opt-in ([why](/configuration/environment-variables)); the vendor's own variable is a fallback value.

[//]: # (generated:backend-env-vars:start)

| Var | Backend | Effect |
|---|---|---|
| `OTEL_PHOENIX_ENDPOINT` | Phoenix | Single-backend mode: Phoenix OTLP traces URL |
| `OTEL_LANGFUSE_ENDPOINT` | Langfuse | Single-backend mode: Langfuse OTLP traces URL |
| `LANGFUSE_BASE_URL` | Langfuse | Langfuse base URL (alternative to the endpoint) |
| `LANGFUSE_PUBLIC_KEY` | Langfuse | Langfuse public key fallback |
| `LANGFUSE_SECRET_KEY` | Langfuse | Langfuse secret key fallback |
| `OTEL_LANGFUSE_PUBLIC_API_KEY` | Langfuse | Langfuse public key (preferred name) |
| `OTEL_LANGFUSE_SECRET_API_KEY` | Langfuse | Langfuse secret key (preferred name) |
| `OTEL_SIGNOZ_ENDPOINT` | SigNoz | Single-backend mode: SigNoz OTLP URL |
| `OTEL_SIGNOZ_INGESTION_KEY` | SigNoz | SigNoz Cloud ingestion key |
| `OTEL_JAEGER_ENDPOINT` | Jaeger | Single-backend mode: Jaeger OTLP URL |
| `OTEL_ZIPKIN_ENDPOINT` | Zipkin | Single-backend mode: Zipkin OTLP URL (zipkin-otel image) |
| `OTEL_GREPTIME_ENDPOINT` | GreptimeDB | Single-backend mode: GreptimeDB HTTP origin (OTLP under /v1/otlp) |
| `OTEL_GREPTIME_DB` | GreptimeDB | GreptimeDB database for all three signals (X-Greptime-DB-Name; default public) |
| `OTEL_GREPTIME_USER` | GreptimeDB | GreptimeDB user for Basic auth (only with a user provider) |
| `OTEL_GREPTIME_PASSWORD` | GreptimeDB | GreptimeDB password for Basic auth |
| `OTEL_CLICKSTACK_ENDPOINT` | ClickStack | Single-backend mode: ClickStack Collector OTLP/HTTP base |
| `OTEL_CLICKSTACK_API_KEY` | ClickStack | ClickStack ingestion API key (bare authorization header; preferred name) |
| `HYPERDX_API_KEY` | ClickStack | ClickStack / HyperDX ingestion API key fallback (the vendor SDK variable) |
| `OTEL_VICTORIA_ENDPOINT` | Victoria | Single-backend mode: VictoriaTraces origin (OTLP at /insert/opentelemetry/v1/traces) |
| `OTEL_VICTORIA_METRICS_ENDPOINT` | Victoria | VictoriaMetrics origin (OTLP at /opentelemetry/v1/metrics); metrics off without it |
| `OTEL_VICTORIA_LOGS_ENDPOINT` | Victoria | VictoriaLogs origin (OTLP at /insert/opentelemetry/v1/logs); logs off without it |
| `OTEL_VICTORIA_USER` | Victoria | Basic auth user for the Victoria stores (only with -httpAuth.*) |
| `OTEL_VICTORIA_PASSWORD` | Victoria | Basic auth password for the Victoria stores |
| `OTEL_ASPIRE_ENDPOINT` | Aspire Dashboard | Single-backend mode: Aspire Dashboard OTLP/HTTP base |
| `OTEL_ASPIRE_API_KEY` | Aspire Dashboard | Aspire Dashboard OTLP API key (x-otlp-api-key); only in ApiKey auth mode |
| `OTEL_GIGAPIPE_ENDPOINT` | Gigapipe | Single-backend mode: Gigapipe (qryn) OTLP/HTTP base |
| `OTEL_GIGAPIPE_USER` | Gigapipe | Basic auth user for Gigapipe (only with GIGAPIPE_LOGIN/PASSWORD) |
| `OTEL_GIGAPIPE_PASSWORD` | Gigapipe | Basic auth password for Gigapipe |
| `OTEL_AGENTA_ENDPOINT` | Agenta | Agenta origin for a self-hosted stack (cloud by default) |
| `OTEL_AGENTA_API_KEY` | Agenta | Agenta project API key (Authorization: ApiKey; preferred name) |
| `AGENTA_API_KEY` | Agenta | Agenta project API key fallback (the SDK variable) |
| `AGENTA_HOST` | Agenta | Agenta origin fallback (the SDK variable) |
| `OTEL_TEMPO_ENDPOINT` | Tempo | Single-backend mode: Tempo / LGTM OTLP URL |
| `OTEL_UPTRACE_ENDPOINT` | Uptrace | Single-backend mode: Uptrace OTLP URL |
| `OTEL_UPTRACE_DSN` | Uptrace | Uptrace DSN (preferred name) |
| `UPTRACE_DSN` | Uptrace | Uptrace DSN fallback |
| `UPTRACE_USER_TOKEN` | Uptrace | Uptrace user token fallback for the dashboard's query API |
| `OTEL_OPENOBSERVE_ENDPOINT` | OpenObserve | Single-backend mode: OpenObserve OTLP URL |
| `OTEL_OPENOBSERVE_STREAM` | OpenObserve | OpenObserve stream name (default `default`) |
| `OTEL_OPENOBSERVE_USER` | OpenObserve | OpenObserve user (preferred name) |
| `OTEL_OPENOBSERVE_PASSWORD` | OpenObserve | OpenObserve password (preferred name) |
| `OPENOBSERVE_USER` | OpenObserve | OpenObserve user fallback |
| `OPENOBSERVE_PASSWORD` | OpenObserve | OpenObserve password fallback |
| `OTEL_PARSEABLE_ENDPOINT` | Parseable | Single-backend mode: Parseable OTLP URL |
| `OTEL_PARSEABLE_API_KEY` | Parseable | Parseable API key (preferred name) |
| `PARSEABLE_API_KEY` | Parseable | Parseable API key fallback |
| `PARSEABLE_TRACES_DATASET` | Parseable | Parseable dataset for traces (default `hermes-traces`) |
| `PARSEABLE_METRICS_DATASET` | Parseable | Parseable dataset for metrics (default `hermes-metrics`) |
| `PARSEABLE_LOGS_DATASET` | Parseable | Parseable dataset for logs (default `hermes-logs`) |
| `OTEL_HONEYCOMB_ENDPOINT` | Honeycomb | Single-backend mode: Honeycomb OTLP URL |
| `OTEL_HONEYCOMB_API_KEY` | Honeycomb | Honeycomb API key (preferred name) |
| `HONEYCOMB_API_KEY` | Honeycomb | Honeycomb API key fallback |
| `OTEL_ELASTIC_ENDPOINT` | Elastic | Single-backend mode: Elastic OTLP URL (mOTLP or EDOT Collector) |
| `OTEL_ELASTIC_API_KEY` | Elastic | Elastic API key (preferred name; needs the endpoint too) |
| `ELASTIC_API_KEY` | Elastic | Elastic API key fallback |
| `OTEL_OPENLIT_ENDPOINT` | OpenLIT | Single-backend mode: OpenLIT OTLP/HTTP URL |
| `OTEL_OPENLIT_API_KEY` | OpenLIT | OpenLIT API key (preferred name; needs the endpoint too) |
| `OPENLIT_API_KEY` | OpenLIT | OpenLIT API key fallback |
| `OTEL_MLFLOW_ENDPOINT` | MLflow | Single-backend mode: MLflow tracking server URL |
| `OTEL_MLFLOW_EXPERIMENT_ID` | MLflow | MLflow experiment id for the x-mlflow-experiment-id header (preferred name) |
| `MLFLOW_EXPERIMENT_ID` | MLflow | MLflow experiment id fallback |
| `OTEL_MLFLOW_API_KEY` | MLflow | MLflow tracking token (preferred name; needs the endpoint too) |
| `MLFLOW_TRACKING_TOKEN` | MLflow | MLflow tracking token fallback |
| `OTEL_OPIK_ENDPOINT` | Comet Opik | Single-backend mode: Opik base URL (default: Comet cloud) |
| `OTEL_OPIK_API_KEY` | Comet Opik | Opik API key (preferred name; enables Opik in env-var mode) |
| `OPIK_API_KEY` | Comet Opik | Opik API key fallback |
| `OPIK_URL_OVERRIDE` | Comet Opik | Opik SDK base URL fallback (…/api form accepted) |
| `OPIK_WORKSPACE` | Comet Opik | Opik workspace for the Comet-Workspace header (cloud) |
| `OPIK_PROJECT_NAME` | Comet Opik | Opik project for the projectName header |
| `OTEL_LAMINAR_ENDPOINT` | Laminar | Single-backend mode: Laminar app-server URL (default: Laminar cloud) |
| `OTEL_LAMINAR_API_KEY` | Laminar | Laminar project API key (preferred name; enables Laminar in env-var mode) |
| `LMNR_PROJECT_API_KEY` | Laminar | Laminar project API key fallback |
| `LMNR_BASE_URL` | Laminar | Laminar SDK base URL fallback |
| `OTEL_LANGWATCH_ENDPOINT` | LangWatch | Single-backend mode: LangWatch base URL (default: LangWatch cloud) |
| `OTEL_LANGWATCH_API_KEY` | LangWatch | LangWatch project API key (preferred name; enables LangWatch in env-var mode) |
| `LANGWATCH_API_KEY` | LangWatch | LangWatch API key fallback |
| `LANGWATCH_ENDPOINT` | LangWatch | LangWatch SDK base URL fallback |
| `LANGWATCH_PROJECT_ID` | LangWatch | LangWatch project id for the X-Project-Id header (service keys) |
| `OTEL_LATITUDE_ENDPOINT` | Latitude | Single-backend mode: Latitude ingest URL (default: Latitude cloud) |
| `OTEL_LATITUDE_API_KEY` | Latitude | Latitude API key (preferred name; enables Latitude in env-var mode) |
| `LATITUDE_API_KEY` | Latitude | Latitude API key fallback |
| `LATITUDE_PROJECT` | Latitude | Latitude project slug for the mandatory X-Latitude-Project header |
| `LATITUDE_INGEST_URL` | Latitude | Latitude ingest URL fallback |
| `OTEL_WEAVE_ENDPOINT` | W&B Weave | Single-backend mode: W&B Weave OTLP URL |
| `WANDB_OTLP_ENDPOINT` | W&B Weave | Weave OTLP URL fallback |
| `OTEL_WEAVE_BASE_URL` | W&B Weave | Weave base URL |
| `WANDB_BASE_URL` | W&B Weave | Weave base URL fallback |
| `OTEL_WEAVE_API_KEY` | W&B Weave | Weave API key (preferred name) |
| `WANDB_API_KEY` | W&B Weave | Weave API key fallback |
| `WANDB_ENTITY` | W&B Weave | Weave entity |
| `WANDB_PROJECT` | W&B Weave | Weave project |
| `DEFAULT_WANDB_ENTITY` | W&B Weave | Weave entity fallback |
| `DEFAULT_WANDB_PROJECT` | W&B Weave | Weave project fallback |
| `OTEL_LANGSMITH_API_KEY` | LangSmith | LangSmith API key (preferred name; the env opt-in) |
| `OTEL_LANGSMITH_ENDPOINT` | LangSmith | LangSmith endpoint override (default `https://api.smith.langchain.com/otel/v1/traces`; `region: eu` picks the EU host) |
| `OTEL_LANGSMITH_PROJECT` | LangSmith | LangSmith project (the `Langsmith-Project` header) |
| `LANGSMITH_API_KEY` | LangSmith | LangSmith API key fallback |
| `LANGSMITH_ENDPOINT` | LangSmith | LangSmith API base fallback (`/otel/v1/traces` is appended) |
| `LANGSMITH_PROJECT` | LangSmith | LangSmith project fallback |

[//]: # (generated:backend-env-vars:end)

## `HERMES_OTEL_*` overrides

Every scalar field of the config file can be overridden by `HERMES_OTEL_<FIELD>` (upper-case field name). Maps (`headers`, `global_tags`, `resource_attributes`, `common_attributes`) and `backends` are yaml-only. An env var that cannot be parsed as the field's type logs a warning and is ignored.

[//]: # (generated:env-overrides:start)

| Env var | Maps to | Type | Default |
|---|---|---|---|
| `HERMES_OTEL_ENABLED` | `enabled` | bool | `true` |
| `HERMES_OTEL_SAMPLE_RATE` | `sample_rate` | float | `null` |
| `HERMES_OTEL_ROOT_SPAN_TTL_MS` | `root_span_ttl_ms` | int | `600000` |
| `HERMES_OTEL_SUBAGENT_SPAN_TTL_MS` | `subagent_span_ttl_ms` | int | `86400000` |
| `HERMES_OTEL_FLUSH_INTERVAL_MS` | `flush_interval_ms` | int | `60000` |
| `HERMES_OTEL_PREVIEW_MAX_CHARS` | `preview_max_chars` | int | `1200` |
| `HERMES_OTEL_CAPTURE_PREVIEWS` | `capture_previews` | bool | `true` |
| `HERMES_OTEL_TOOL_INPUT_PREVIEW_MAX_CHARS` | `tool_input_preview_max_chars` | int | `null` |
| `HERMES_OTEL_TOOL_OUTPUT_PREVIEW_MAX_CHARS` | `tool_output_preview_max_chars` | int | `null` |
| `HERMES_OTEL_LLM_INPUT_PREVIEW_MAX_CHARS` | `llm_input_preview_max_chars` | int | `null` |
| `HERMES_OTEL_LLM_OUTPUT_PREVIEW_MAX_CHARS` | `llm_output_preview_max_chars` | int | `null` |
| `HERMES_OTEL_PROJECT_NAME` | `project_name` | string | *(unset)* |
| `HERMES_OTEL_SERVICE_NAME_FROM_PROFILE` | `service_name_from_profile` | bool | `false` |
| `HERMES_OTEL_SPAN_BATCH_MAX_QUEUE_SIZE` | `span_batch_max_queue_size` | int | `2048` |
| `HERMES_OTEL_SPAN_BATCH_SCHEDULE_DELAY_MS` | `span_batch_schedule_delay_ms` | int | `1000` |
| `HERMES_OTEL_SPAN_BATCH_MAX_EXPORT_BATCH_SIZE` | `span_batch_max_export_batch_size` | int | `null` |
| `HERMES_OTEL_SPAN_BATCH_EXPORT_TIMEOUT_MS` | `span_batch_export_timeout_ms` | int | `30000` |
| `HERMES_OTEL_FORCE_FLUSH_ON_SESSION_END` | `force_flush_on_session_end` | bool | `true` |
| `HERMES_OTEL_FORCE_FLUSH_WAIT_MS` | `force_flush_wait_ms` | int | `1500` |
| `HERMES_OTEL_CAPTURE_CONVERSATION_HISTORY` | `capture_conversation_history` | bool | `false` |
| `HERMES_OTEL_CONVERSATION_HISTORY_MAX_CHARS` | `conversation_history_max_chars` | int | `20000` |
| `HERMES_OTEL_CONTENT_CAPTURE` | `content_capture` | string | `"full"` |
| `HERMES_OTEL_CAPTURE_FULL_PROMPTS` | `capture_full_prompts` | bool | `true` |
| `HERMES_OTEL_CAPTURE_FULL_RESPONSES` | `capture_full_responses` | bool | `true` |
| `HERMES_OTEL_CAPTURE_SENDER_ID` | `capture_sender_id` | bool | `false` |
| `HERMES_OTEL_CAPTURE_LOGS` | `capture_logs` | bool | `false` |
| `HERMES_OTEL_LOG_LEVEL` | `log_level` | string | `"INFO"` |
| `HERMES_OTEL_LOG_ATTACH_LOGGER` | `log_attach_logger` | string | *(unset)* |
| `HERMES_OTEL_LOG_ONLY_IN_TURN` | `log_only_in_turn` | bool | `false` |
| `HERMES_OTEL_LOG_MAX_ATTRIBUTE_LENGTH` | `log_max_attribute_length` | int | `4096` |
| `HERMES_OTEL_LOG_BATCH_SCHEDULE_DELAY_MS` | `log_batch_schedule_delay_ms` | int | `1000` |
| `HERMES_OTEL_LOG_BATCH_MAX_QUEUE_SIZE` | `log_batch_max_queue_size` | int | `2048` |
| `HERMES_OTEL_LOG_BATCH_MAX_EXPORT_BATCH_SIZE` | `log_batch_max_export_batch_size` | int | `512` |
| `HERMES_OTEL_LOG_BATCH_EXPORT_TIMEOUT_MS` | `log_batch_export_timeout_ms` | int | `30000` |
| `HERMES_OTEL_LOG_LIVE_MIN_LEVEL` | `log_live_min_level` | string | `"INFO"` |
| `HERMES_OTEL_LOG_EVENTS` | `log_events` | bool | `false` |
| `HERMES_OTEL_LOG_EVENTS_CONTENT` | `log_events_content` | string | `"inherit"` |
| `HERMES_OTEL_EMIT_GENAI_METRICS` | `emit_genai_metrics` | bool | `true` |
| `HERMES_OTEL_METRICS_TEMPORALITY` | `metrics_temporality` | string | *(unset)* |
| `HERMES_OTEL_METRICS_HISTOGRAM` | `metrics_histogram` | string | `"explicit"` |
| `HERMES_OTEL_METRICS_LABEL_LIMIT` | `metrics_label_limit` | int | `100` |
| `HERMES_OTEL_METRIC_NAME_COMPAT` | `metric_name_compat` | string | `"off"` |
| `HERMES_OTEL_METRIC_NAME_COMPAT_SESSION_ID` | `metric_name_compat_session_id` | bool | `false` |
| `HERMES_OTEL_SKILL_SPANS` | `skill_spans` | bool | `true` |
| `HERMES_OTEL_REMOTE_PARENT` | `remote_parent` | bool | `true` |
| `HERMES_OTEL_PROBE_BACKENDS` | `probe_backends` | bool | `true` |
| `HERMES_OTEL_DISCOVERY_PROMPT` | `discovery_prompt` | bool | `false` |
| `HERMES_OTEL_DASHBOARD_LIVE` | `dashboard_live` | bool | `true` |
| `HERMES_OTEL_DASHBOARD_LIVE_MAX_SPANS` | `dashboard_live_max_spans` | int | `1000` |
| `HERMES_OTEL_DASHBOARD_LIVE_RETENTION_HOURS` | `dashboard_live_retention_hours` | float | `168.0` |
| `HERMES_OTEL_HOST_METRICS` | `host_metrics` | bool | `false` |
| `HERMES_OTEL_HOST_METRICS_GPU` | `host_metrics_gpu` | string | `"auto"` |
| `HERMES_OTEL_HOST_METRICS_INTERVAL_MS` | `host_metrics_interval_ms` | int | `1000` |
| `HERMES_OTEL_SUPPRESS_MCP_PING_SPANS` | `suppress_mcp_ping_spans` | bool | `true` |

[//]: # (generated:env-overrides:end)

## Join an outer trace

| Var | Effect |
|---|---|
| `HERMES_OTEL_TRACEPARENT` | A W3C `traceparent` (`00-<trace id>-<span id>-<flags>`) exported by whatever launched Hermes (a CI job, a script, another agent). Every turn root of the process is parented under that span, so the Hermes run shows up inside the launcher's trace, with `hermes.trace.remote_parent=true` on the root. A malformed value is reported once and ignored. `remote_parent: false` turns this off. The kanban worker hand-off uses plain `TRACEPARENT` and only inside a worker; the two do not interact (#474) |
| `HERMES_OTEL_TRACESTATE` | A W3C `tracestate` carried along with the parent above |

## Paths

| Var | Effect |
|---|---|
| `HERMES_OTEL_CONFIG` | Explicit path to the config file; highest precedence — see [Where does the config live?](/configuration/overview) |
| `HERMES_HOME` | Hermes' home (default `~/.hermes`); the plugin resolves `hermes_otel.yaml`, the live store and the debug log under it. Inside a multiplexed gateway each profile's plugin uses that profile's home instead, via Hermes's own resolver ([profiles](/configuration/profiles)) |
| `HERMES_OTEL_LIVE_DB` | Path of the live dashboard's SQLite store (default `$HERMES_HOME/hermes_otel_live.db`) |

## OpenTelemetry SDK

| Var | Effect |
|---|---|
| `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_EXPORTER_OTLP_TRACES_HEADERS`, `OTEL_EXPORTER_OTLP_METRICS_HEADERS`, `OTEL_EXPORTER_OTLP_LOGS_HEADERS` | **Ignored** since 1.30.1 (#472). The SDK adds them to every exporter built without headers, so a token exported for another tool reached every credential-less backend in a multi-backend file. Put headers in the file instead: top-level `headers:` for all backends, a backend entry's `headers:` for one |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | Metric temporality (`cumulative` / `delta`) for a backend when neither its entry, its type preset nor the top-level `metrics_temporality` sets one; read by the OTLP exporter itself and shown on the Settings tab |

## Debug

| Var | Value | Effect |
|---|---|---|
| `HERMES_OTEL_DEBUG` | `true`/`false` | Enables the debug log at `$HERMES_HOME/plugins/hermes_otel/debug.log` (`~/.hermes` by default) |

## Boolean accepted values

| True | False |
|---|---|
| `true` / `1` / `yes` / `on` | `false` / `0` / `no` / `off` / `""` |

Case-insensitive. Anything else logs a warning naming the variable and is ignored (the yaml value or default is kept).
