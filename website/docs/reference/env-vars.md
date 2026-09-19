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
| `LANGFUSE_PUBLIC_KEY` | `pk-lf-...` | Langfuse public key (SDK-standard) |
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
| `HONEYCOMB_API_KEY` | `hcaik_...` | Honeycomb ingest key (`x-honeycomb-team`); enables Honeycomb in env-var mode |
| `OTEL_HONEYCOMB_API_KEY` | `hcaik_...` | Honeycomb key (plugin-specific alias) |
| `OTEL_HONEYCOMB_ENDPOINT` | URL | Honeycomb endpoint override (default: US region) |
| `WANDB_API_KEY` | string | W&B API key; enables Weave in env-var mode when routing vars are also set |
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
| `HERMES_OTEL_SPAN_BATCH_MAX_EXPORT_BATCH_SIZE` | `span_batch_max_export_batch_size` | int | `512` |
| `HERMES_OTEL_SPAN_BATCH_EXPORT_TIMEOUT_MS` | `span_batch_export_timeout_ms` | int | `30000` |
| `HERMES_OTEL_FORCE_FLUSH_ON_SESSION_END` | `force_flush_on_session_end` | bool | `true` |
| `HERMES_OTEL_CAPTURE_CONVERSATION_HISTORY` | `capture_conversation_history` | bool | `false` |
| `HERMES_OTEL_CONVERSATION_HISTORY_MAX_CHARS` | `conversation_history_max_chars` | int | `20000` |
| `HERMES_OTEL_CAPTURE_FULL_PROMPTS` | `capture_full_prompts` | bool | `false` |
| `HERMES_OTEL_CAPTURE_FULL_RESPONSES` | `capture_full_responses` | bool | `false` |
| `HERMES_OTEL_CAPTURE_SENDER_ID` | `capture_sender_id` | bool | `false` |
| `HERMES_OTEL_CAPTURE_LOGS` | `capture_logs` | bool | `false` |
| `HERMES_OTEL_LOG_LEVEL` | `log_level` | string | `"INFO"` |
| `HERMES_OTEL_LOG_ATTACH_LOGGER` | `log_attach_logger` | string | *(unset)* |
| `HERMES_OTEL_EMIT_GENAI_METRICS` | `emit_genai_metrics` | bool | `true` |
| `HERMES_OTEL_SKILL_SPANS` | `skill_spans` | bool | `true` |
| `HERMES_OTEL_DISCOVERY_PROMPT` | `discovery_prompt` | bool | `false` |
| `HERMES_OTEL_DASHBOARD_LIVE` | `dashboard_live` | bool | `true` |
| `HERMES_OTEL_DASHBOARD_LIVE_MAX_SPANS` | `dashboard_live_max_spans` | int | `1000` |
| `HERMES_OTEL_HOST_METRICS` | `host_metrics` | bool | `false` |
| `HERMES_OTEL_HOST_METRICS_GPU` | `host_metrics_gpu` | string | `"auto"` |
| `HERMES_OTEL_HOST_METRICS_INTERVAL_MS` | `host_metrics_interval_ms` | int | `1000` |
| `HERMES_OTEL_SUPPRESS_MCP_PING_SPANS` | `suppress_mcp_ping_spans` | bool | `true` |

[//]: # (generated:env-overrides:end)

## Paths

| Var | Effect |
|---|---|
| `HERMES_OTEL_CONFIG` | Explicit path to the config file; highest precedence — see [Where does the config live?](/configuration/overview) |
| `HERMES_HOME` | Hermes' home (default `~/.hermes`); the plugin resolves `hermes_otel.yaml` and the live store under it |
| `HERMES_OTEL_LIVE_DB` | Path of the live dashboard's SQLite store (default `$HERMES_HOME/hermes_otel_live.db`) |

## Debug

| Var | Value | Effect |
|---|---|---|
| `HERMES_OTEL_DEBUG` | `true`/`false` | Enables debug log at `~/.hermes/plugins/hermes_otel/debug.log` |

## Boolean accepted values

| True | False |
|---|---|
| `true` / `1` / `yes` / `on` | `false` / `0` / `no` / `off` / `""` |

Case-insensitive. Anything else logs a warning naming the variable and is ignored (the yaml value or default is kept).
