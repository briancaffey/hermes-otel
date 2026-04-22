---
sidebar_position: 1
title: "Env var reference"
description: "Every environment variable the plugin reads, grouped by purpose."
---

# Env var reference

Complete list. See [Environment variables](/configuration/environment-variables) for the narrative version.

## Backend selection

| Var | Value | Effect |
|---|---|---|
| `LANGSMITH_TRACING` | `true`/`false` | Enables LangSmith backend |
| `LANGSMITH_API_KEY` | `lsv2_...` | LangSmith auth |
| `LANGSMITH_ENDPOINT` | URL | LangSmith endpoint (default: `https://api.smith.langchain.com`) |
| `LANGSMITH_PROJECT` | string | LangSmith project name |
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
| `OTEL_PROJECT_NAME` | string | Resource `service.name` + `openinference.project.name` |

## Shaping overrides

| Var | Maps to | Default |
|---|---|---|
| `HERMES_OTEL_ENABLED` | `enabled` | `true` |
| `HERMES_OTEL_SAMPLE_RATE` | `sample_rate` | `null` (AlwaysOn) |
| `HERMES_OTEL_ROOT_SPAN_TTL_MS` | `root_span_ttl_ms` | `600000` (10 min) |
| `HERMES_OTEL_FLUSH_INTERVAL_MS` | `flush_interval_ms` | `60000` (60s) |
| `HERMES_OTEL_PREVIEW_MAX_CHARS` | `preview_max_chars` | `1200` |
| `HERMES_OTEL_CAPTURE_PREVIEWS` | `capture_previews` | `true` |
| `HERMES_OTEL_CAPTURE_CONVERSATION_HISTORY` | `capture_conversation_history` | `false` |
| `HERMES_OTEL_CONVERSATION_HISTORY_MAX_CHARS` | `conversation_history_max_chars` | `20000` |
| `HERMES_OTEL_PROJECT_NAME` | `project_name` | *(unset)* |
| `HERMES_OTEL_SPAN_BATCH_MAX_QUEUE_SIZE` | `span_batch_max_queue_size` | `2048` |
| `HERMES_OTEL_SPAN_BATCH_SCHEDULE_DELAY_MS` | `span_batch_schedule_delay_ms` | `1000` |
| `HERMES_OTEL_SPAN_BATCH_MAX_EXPORT_BATCH_SIZE` | `span_batch_max_export_batch_size` | `512` |
| `HERMES_OTEL_SPAN_BATCH_EXPORT_TIMEOUT_MS` | `span_batch_export_timeout_ms` | `30000` |
| `HERMES_OTEL_FORCE_FLUSH_ON_SESSION_END` | `force_flush_on_session_end` | `true` |

## Logs

See [OTel logs](/configuration/logs) for behavior.

| Var | Maps to | Default |
|---|---|---|
| `HERMES_OTEL_CAPTURE_LOGS` | `capture_logs` | `false` |
| `HERMES_OTEL_LOG_LEVEL` | `log_level` | `INFO` |
| `HERMES_OTEL_LOG_ATTACH_LOGGER` | `log_attach_logger` | *(unset = root)* |

## Debug

| Var | Value | Effect |
|---|---|---|
| `HERMES_OTEL_DEBUG` | `true`/`false` | Enables debug log at `~/.hermes/plugins/hermes_otel/debug.log` |

## Boolean accepted values

| True | False |
|---|---|
| `true` / `1` / `yes` / `on` | `false` / `0` / `no` / `off` / `""` |

Case-insensitive. Anything else → warning + fallback to default.
