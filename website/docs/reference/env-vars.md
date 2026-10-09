---
sidebar_position: 1
title: "Env var reference"
description: "Every environment variable the plugin reads, grouped by purpose."
---

# Env var reference

Complete list. See [Environment variables](/configuration/environment-variables) for where to set them and how precedence works. The `HERMES_OTEL_*` table is generated from `HermesOtelConfig` by `scripts/gen_config_docs.py`; a test fails if it drifts.

## Backend selection

| Var | Value | Effect |
|---|---|---|
| `LANGSMITH_TRACING` | `true`/`false` | Enables LangSmith backend |
| `LANGSMITH_API_KEY` | `lsv2_...` | LangSmith auth |
| `LANGSMITH_ENDPOINT` | URL | LangSmith endpoint (default: `https://api.smith.langchain.com`) |
| `LANGSMITH_PROJECT` | string | LangSmith project name |
| `LANGSMITH_WORKSPACE_ID` | string | LangSmith workspace id header (multi-workspace orgs) |
| `OTEL_LANGFUSE_PUBLIC_API_KEY` | `pk-lf-...` | Langfuse public key (plugin-specific) |
| `OTEL_LANGFUSE_SECRET_API_KEY` | `sk-lf-...` | Langfuse secret key (plugin-specific) |
| `OTEL_LANGFUSE_ENDPOINT` | URL | Langfuse OTLP endpoint |
| `LANGFUSE_PUBLIC_KEY` | `pk-lf-...` | Langfuse public key (SDK-standard fallback; does not enable Langfuse in env-var mode on its own) |
| `LANGFUSE_SECRET_KEY` | `sk-lf-...` | Langfuse secret key (SDK-standard) |
| `LANGFUSE_BASE_URL` | URL | Langfuse base URL (SDK-standard) |
| `OTEL_SIGNOZ_ENDPOINT` | URL | SigNoz OTLP endpoint (self-host: `http://localhost:4328/v1/traces`) |
| `OTEL_SIGNOZ_INGESTION_KEY` | `sz-...` | SigNoz Cloud ingestion key |
| `OTEL_JAEGER_ENDPOINT` | URL | Jaeger OTLP endpoint (`http://localhost:4318/v1/traces`) |
| `OTEL_TEMPO_ENDPOINT` | URL | Tempo OTLP endpoint |
| `OTEL_PHOENIX_ENDPOINT` | URL | Phoenix OTLP endpoint (`http://localhost:6006/v1/traces`) |
| `OTEL_UPTRACE_ENDPOINT` | URL | Uptrace OTLP endpoint; enables Uptrace in env-var mode |
| `OTEL_UPTRACE_DSN` / `UPTRACE_DSN` | DSN | Uptrace DSN (`uptrace-dsn` header) |
| `OTEL_OPENOBSERVE_ENDPOINT` | URL | OpenObserve `.../api/<org>/v1/traces`; enables OpenObserve in env-var mode |
| `OTEL_OPENOBSERVE_USER` / `OPENOBSERVE_USER` | string | OpenObserve Basic-auth user |
| `OTEL_OPENOBSERVE_PASSWORD` / `OPENOBSERVE_PASSWORD` | string | OpenObserve Basic-auth password |
| `OTEL_OPENOBSERVE_STREAM` | string | Optional OpenObserve stream name |
| `OTEL_PARSEABLE_ENDPOINT` | URL | Parseable ingestor `.../v1/traces`; enables Parseable in env-var mode |
| `OTEL_PARSEABLE_API_KEY` / `PARSEABLE_API_KEY` | string | Parseable API key |
| `PARSEABLE_TRACES_DATASET` / `PARSEABLE_METRICS_DATASET` / `PARSEABLE_LOGS_DATASET` | string | Parseable dataset per signal (defaults `hermes-traces` / `hermes-metrics` / `hermes-logs`) |
| `OTEL_ELASTIC_ENDPOINT` | URL | Elastic OTLP endpoint (Elastic Cloud mOTLP or EDOT Collector); enables Elastic in env-var mode |
| `OTEL_ELASTIC_API_KEY` | string | Elastic API key (`Authorization: ApiKey`); needs `OTEL_ELASTIC_ENDPOINT` to enable Elastic in env-var mode (there is no default host) |
| `ELASTIC_API_KEY` | string | Elastic API key fallback; does not enable Elastic in env-var mode on its own |
| `OTEL_OPENLIT_ENDPOINT` | URL | OpenLIT OTLP/HTTP receiver base (`http://localhost:4338` for the bundled stack); enables OpenLIT in env-var mode |
| `OTEL_OPENLIT_API_KEY` | string | OpenLIT API key (`Authorization: Bearer`); optional, needs `OTEL_OPENLIT_ENDPOINT` to enable OpenLIT in env-var mode |
| `OPENLIT_API_KEY` | string | OpenLIT API key fallback; does not enable OpenLIT in env-var mode on its own |
| `OTEL_MLFLOW_ENDPOINT` | URL | MLflow tracking server (`http://localhost:5001` for the bundled stack); enables MLflow in env-var mode |
| `OTEL_MLFLOW_EXPERIMENT_ID` / `MLFLOW_EXPERIMENT_ID` | string | Experiment for the mandatory `x-mlflow-experiment-id` header (default `0`, the Default experiment) |
| `OTEL_MLFLOW_API_KEY` / `MLFLOW_TRACKING_TOKEN` | string | Tracking token (`Authorization: Bearer`); optional, does not enable MLflow on its own |
| `OTEL_OPIK_ENDPOINT` | URL | Opik base URL (`http://localhost:5173` for the bundled stack; default Comet cloud); enables Opik in env-var mode |
| `OTEL_OPIK_API_KEY` | string | Opik API key (bare `Authorization` header); enables Opik in env-var mode (cloud default host) |
| `OPIK_API_KEY` / `OPIK_URL_OVERRIDE` / `OPIK_WORKSPACE` / `OPIK_PROJECT_NAME` | string | Opik SDK variables used as fallbacks for the key, base URL, `Comet-Workspace` and `projectName`; never enable export on their own |
| `OTEL_HONEYCOMB_API_KEY` | `hcaik_...` | Honeycomb ingest key (`x-honeycomb-team`); enables Honeycomb in env-var mode |
| `HONEYCOMB_API_KEY` | `hcaik_...` | Honeycomb key fallback; does not enable Honeycomb in env-var mode on its own |
| `OTEL_HONEYCOMB_ENDPOINT` | URL | Honeycomb endpoint override (default: US region) |
| `OTEL_WEAVE_API_KEY` | string | W&B API key (plugin-specific); enables Weave in env-var mode when routing vars are also set |
| `WANDB_API_KEY` | string | W&B API key fallback; does not enable Weave in env-var mode on its own |
| `WANDB_ENTITY` | string | W&B entity/team for Weave routing (`wandb.entity`) |
| `WANDB_PROJECT` | string | W&B project for Weave routing (`wandb.project`) |
| `DEFAULT_WANDB_ENTITY` | string | Weave entity fallback, useful with an OTel Collector |
| `DEFAULT_WANDB_PROJECT` | string | Weave project fallback, useful with an OTel Collector |
| `OTEL_WEAVE_ENDPOINT` | URL | Full Weave OTLP traces endpoint override |
| `WANDB_OTLP_ENDPOINT` | URL | Weave OTLP endpoint alias used by W&B docs |
| `OTEL_WEAVE_BASE_URL` | URL | W&B base URL; plugin appends the trace ingest path |
| `WANDB_BASE_URL` | URL | W&B base URL alias for Dedicated Cloud / Self-Managed |
| `OTEL_PROJECT_NAME` | string | `openinference.project.name` on the Resource (the Phoenix project). `service.name` is always `hermes-agent`; override it with `resource_attributes:` |

## `HERMES_OTEL_*` overrides

Every scalar field of the config file can be overridden by `HERMES_OTEL_<FIELD>` (upper-case field name). Maps (`headers`, `global_tags`, `resource_attributes`) and `backends` are yaml-only. An env var that cannot be parsed as the field's type logs a warning and is ignored.

[//]: # (generated:env-overrides:start)

| Env var | Maps to | Type | Default |
|---|---|---|---|
| `HERMES_OTEL_ENABLED` | `enabled` | bool | `true` |
| `HERMES_OTEL_SAMPLE_RATE` | `sample_rate` | float | `null` |
| `HERMES_OTEL_ROOT_SPAN_TTL_MS` | `root_span_ttl_ms` | int | `600000` |
| `HERMES_OTEL_FLUSH_INTERVAL_MS` | `flush_interval_ms` | int | `60000` |
| `HERMES_OTEL_PREVIEW_MAX_CHARS` | `preview_max_chars` | int | `1200` |
| `HERMES_OTEL_CAPTURE_PREVIEWS` | `capture_previews` | bool | `true` |
| `HERMES_OTEL_TOOL_INPUT_PREVIEW_MAX_CHARS` | `tool_input_preview_max_chars` | int | `null` |
| `HERMES_OTEL_TOOL_OUTPUT_PREVIEW_MAX_CHARS` | `tool_output_preview_max_chars` | int | `null` |
| `HERMES_OTEL_LLM_INPUT_PREVIEW_MAX_CHARS` | `llm_input_preview_max_chars` | int | `null` |
| `HERMES_OTEL_LLM_OUTPUT_PREVIEW_MAX_CHARS` | `llm_output_preview_max_chars` | int | `null` |
| `HERMES_OTEL_PROJECT_NAME` | `project_name` | string | *(unset)* |
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
| `HERMES_OTEL_SKILL_SPANS` | `skill_spans` | bool | `true` |
| `HERMES_OTEL_DISCOVERY_PROMPT` | `discovery_prompt` | bool | `false` |
| `HERMES_OTEL_DASHBOARD_LIVE` | `dashboard_live` | bool | `true` |
| `HERMES_OTEL_DASHBOARD_LIVE_MAX_SPANS` | `dashboard_live_max_spans` | int | `1000` |
| `HERMES_OTEL_DASHBOARD_LIVE_RETENTION_HOURS` | `dashboard_live_retention_hours` | float | `168.0` |
| `HERMES_OTEL_HOST_METRICS` | `host_metrics` | bool | `false` |
| `HERMES_OTEL_HOST_METRICS_GPU` | `host_metrics_gpu` | string | `"auto"` |
| `HERMES_OTEL_HOST_METRICS_INTERVAL_MS` | `host_metrics_interval_ms` | int | `1000` |
| `HERMES_OTEL_SUPPRESS_MCP_PING_SPANS` | `suppress_mcp_ping_spans` | bool | `true` |

[//]: # (generated:env-overrides:end)

## Paths

| Var | Effect |
|---|---|
| `HERMES_OTEL_CONFIG` | Explicit path to the config file; highest precedence — see [Where does the config live?](/configuration/overview) |
| `HERMES_HOME` | Hermes' home (default `~/.hermes`); the plugin resolves `hermes_otel.yaml`, the live store and the debug log under it. Inside a multiplexed gateway each profile's plugin uses that profile's home instead, via Hermes's own resolver ([profiles](/configuration/profiles)) |
| `HERMES_OTEL_LIVE_DB` | Path of the live dashboard's SQLite store (default `$HERMES_HOME/hermes_otel_live.db`) |

## Debug

| Var | Value | Effect |
|---|---|---|
| `HERMES_OTEL_DEBUG` | `true`/`false` | Enables the debug log at `$HERMES_HOME/plugins/hermes_otel/debug.log` (`~/.hermes` by default) |

## Boolean accepted values

| True | False |
|---|---|
| `true` / `1` / `yes` / `on` | `false` / `0` / `no` / `off` / `""` |

Case-insensitive. Anything else logs a warning naming the variable and is ignored (the yaml value or default is kept).
