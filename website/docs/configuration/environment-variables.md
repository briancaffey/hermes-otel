---
sidebar_position: 3
title: "Environment variables"
description: "Every env var the plugin reads — backend selection, shaping overrides, debug, and Langfuse / SigNoz compatibility vars."
---

# Environment variables

All env vars the plugin reads. Most shaping vars are `HERMES_OTEL_*`-prefixed and override the corresponding `config.yaml` field.

## Backend selection

Setting any of these enables the matching backend. First match wins (see [Backends overview](/backends/overview) for priority order).

| Var | Backend | Example |
|---|---|---|
| `LANGSMITH_TRACING` | LangSmith | `true` |
| `LANGSMITH_API_KEY` | LangSmith | `lsv2_...` |
| `LANGSMITH_ENDPOINT` | LangSmith (optional) | `https://api.smith.langchain.com` |
| `LANGSMITH_PROJECT` | LangSmith (optional) | `hermes-langsmith-otel` |
| `OTEL_LANGFUSE_PUBLIC_API_KEY` | Langfuse (plugin-specific) | `pk-lf-...` |
| `OTEL_LANGFUSE_SECRET_API_KEY` | Langfuse (plugin-specific) | `sk-lf-...` |
| `OTEL_LANGFUSE_ENDPOINT` | Langfuse (plugin-specific) | `https://cloud.langfuse.com/api/public/otel` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse (SDK-standard) | `pk-lf-...` |
| `LANGFUSE_SECRET_KEY` | Langfuse (SDK-standard) | `sk-lf-...` |
| `LANGFUSE_BASE_URL` | Langfuse (SDK-standard) | `https://cloud.langfuse.com` |
| `OTEL_SIGNOZ_ENDPOINT` | SigNoz | `http://localhost:4328/v1/traces` |
| `OTEL_SIGNOZ_INGESTION_KEY` | SigNoz Cloud | `sz-...` |
| `OTEL_JAEGER_ENDPOINT` | Jaeger | `http://localhost:4318/v1/traces` |
| `OTEL_TEMPO_ENDPOINT` | Tempo | `http://localhost:4318/v1/traces` |
| `OTEL_PHOENIX_ENDPOINT` | Phoenix | `http://localhost:6006/v1/traces` |
| `OTEL_PROJECT_NAME` | All | `hermes-agent` |

## Shaping overrides

Each of these overrides the corresponding field in `config.yaml`. See [`config.yaml`](/configuration/yaml) for defaults and field-level docs.

| Env var | Maps to | Type |
|---|---|---|
| `HERMES_OTEL_ENABLED` | `enabled` | bool |
| `HERMES_OTEL_SAMPLE_RATE` | `sample_rate` | float 0..1 |
| `HERMES_OTEL_ROOT_SPAN_TTL_MS` | `root_span_ttl_ms` | int (ms) |
| `HERMES_OTEL_FLUSH_INTERVAL_MS` | `flush_interval_ms` | int (ms) |
| `HERMES_OTEL_PREVIEW_MAX_CHARS` | `preview_max_chars` | int |
| `HERMES_OTEL_CAPTURE_PREVIEWS` | `capture_previews` | bool |
| `HERMES_OTEL_CAPTURE_CONVERSATION_HISTORY` | `capture_conversation_history` | bool |
| `HERMES_OTEL_CONVERSATION_HISTORY_MAX_CHARS` | `conversation_history_max_chars` | int |
| `HERMES_OTEL_PROJECT_NAME` | `project_name` | string |
| `HERMES_OTEL_SPAN_BATCH_MAX_QUEUE_SIZE` | `span_batch_max_queue_size` | int |
| `HERMES_OTEL_SPAN_BATCH_SCHEDULE_DELAY_MS` | `span_batch_schedule_delay_ms` | int (ms) |
| `HERMES_OTEL_SPAN_BATCH_MAX_EXPORT_BATCH_SIZE` | `span_batch_max_export_batch_size` | int |
| `HERMES_OTEL_SPAN_BATCH_EXPORT_TIMEOUT_MS` | `span_batch_export_timeout_ms` | int (ms) |
| `HERMES_OTEL_FORCE_FLUSH_ON_SESSION_END` | `force_flush_on_session_end` | bool |

## Debug / diagnostics

| Var | Effect |
|---|---|
| `HERMES_OTEL_DEBUG` | `true` enables per-span debug log to `~/.hermes/plugins/hermes_otel/debug.log`. See [Debug logging](/development/debug-logging). |

## Boolean parsing

Anywhere the plugin reads a boolean env var, the following are accepted:

- **True:** `true`, `1`, `yes`, `on` (case-insensitive)
- **False:** `false`, `0`, `no`, `off`, `""` (case-insensitive)

Anything else logs a warning and falls back to the default.

## Where to set them

The easiest place is `~/.hermes/.env`, which Hermes auto-loads on startup:

```
OTEL_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces
OTEL_PROJECT_NAME=hermes-agent
HERMES_OTEL_CAPTURE_PREVIEWS=true
HERMES_OTEL_SAMPLE_RATE=0.25
```

Or export them in your shell profile (`~/.bashrc`, `~/.zshrc`) for a global default.

:::tip
Prefer `~/.hermes/.env` for per-machine config. Per-shell exports are fine for experimentation but drift from what you've committed to `config.yaml`.
:::
