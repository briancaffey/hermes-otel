---
sidebar_position: 2
title: "config.yaml"
description: "Full reference for the plugin's YAML config file — project metadata, sampling, preview shaping, orphan sweep, batch tuning, and multi-backend fan-out."
---

# `config.yaml`

A fully-annotated example ships at `~/.hermes/plugins/hermes_otel/config.yaml.example`. This page is the reference — every field, what it does, what the default is.

Location: `~/.hermes/plugins/hermes_otel/config.yaml`. Parsed only if `pyyaml` is installed in the Hermes venv.

## Project / resource attributes

```yaml
# Shown in Phoenix's "Project" dropdown and as `openinference.project.name`
# on every span. Env: OTEL_PROJECT_NAME or HERMES_OTEL_PROJECT_NAME.
project_name: hermes-agent

# Free-form key/value pairs merged into the OTel Resource on every span.
# Good for deployment tags. Can be overridden per-key by `resource_attributes:`.
global_tags:
  team: platform

# Takes precedence over `global_tags` on key conflict.
resource_attributes:
  env: prod
  region: us-east-1

# Extra HTTP headers added to every OTLP request across ALL backends.
# Per-backend `headers:` are merged on top (per-backend wins on conflict).
headers:
  X-Scope-OrgID: tenant-a
```

## Master switch

```yaml
# Master kill switch. false = no spans, no hooks, no OTel SDK loaded.
# Env: HERMES_OTEL_ENABLED
enabled: true
```

## Sampling

```yaml
# ParentBased(TraceIdRatioBased). null = AlwaysOn (100%).
# 0.25 = sample 25% of traces. Parent-based: if the root is sampled,
# descendants are too — you always see whole traces, never partial ones.
# Env: HERMES_OTEL_SAMPLE_RATE
sample_rate: null
```

See [Sampling](/configuration/sampling) for how to pick a rate.

## Preview shaping

```yaml
# Truncation cap applied to tool input/output, user_message, assistant_response.
# Preview values longer than this are clipped with a trailing "...".
# Env: HERMES_OTEL_PREVIEW_MAX_CHARS
preview_max_chars: 1200

# Global privacy kill switch. When false, ALL input/output previews are
# suppressed (tool args, tool results, user messages, assistant responses).
# Metadata (tool names, durations, token counts) still flows.
# Env: HERMES_OTEL_CAPTURE_PREVIEWS
capture_previews: true

# When true, the llm.* span's input.value becomes the entire conversation
# the model saw (as JSON). api.* spans don't carry message-level detail,
# so this is the way to see what the model actually had in context.
capture_conversation_history: false

# Safety cap on the JSON'd conversation — clipped with "..." if bigger.
conversation_history_max_chars: 20000
```

See [Conversation capture](/configuration/conversation-capture) for a worked example, and [Privacy mode](/configuration/privacy) for the kill switch.

## Logs (opt-in)

```yaml
# Attach an OTel LoggingHandler to Python's logging so stdlib logger.info(...)
# calls ship to any log-capable backend (SigNoz, LGTM, Uptrace, OpenObserve,
# or any OTLP collector). Every
# record gets the active span's trace_id/span_id stamped on it automatically.
# Off by default because attaching to root is invasive.
# Env: HERMES_OTEL_CAPTURE_LOGS
capture_logs: false

# Minimum severity accepted: DEBUG, INFO, WARNING, ERROR, CRITICAL.
# Env: HERMES_OTEL_LOG_LEVEL
log_level: INFO

# Which Python logger to attach to. null = root (everything). "hermes_otel"
# or "hermes" narrows the firehose.
# Env: HERMES_OTEL_LOG_ATTACH_LOGGER
log_attach_logger: null
```

See [OTel logs](/configuration/logs) for the full story, including the loop-avoidance filter for HTTP client libraries and which backends actually accept logs.

## Lifecycle / cleanup

```yaml
# Orphan-turn sweep. If a session root span stays open longer than this,
# subsequent hooks finalize it with `hermes.turn.final_status=timed_out`.
# Env: HERMES_OTEL_ROOT_SPAN_TTL_MS. Default: 10 minutes.
root_span_ttl_ms: 600000

# How often PeriodicExportingMetricReader flushes.
# Env: HERMES_OTEL_FLUSH_INTERVAL_MS. Default: 60s.
flush_interval_ms: 60000

# Synchronously force-flush every BatchSpanProcessor at on_session_end so
# traces appear in the UI immediately. false = rely on the background
# worker's schedule (slightly higher UI latency, better throughput).
# Env: HERMES_OTEL_FORCE_FLUSH_ON_SESSION_END.
force_flush_on_session_end: true
```

See [Orphan-span sweep](/architecture/orphan-sweep).

## BatchSpanProcessor tunables

Each backend gets its own processor with these settings. Defaults are fine for almost every workload — tune only if you're losing spans under sustained high throughput.

```yaml
# Max spans buffered before the oldest ones get dropped.
# Env: HERMES_OTEL_SPAN_BATCH_MAX_QUEUE_SIZE
span_batch_max_queue_size: 2048

# How often the background worker wakes up to drain the queue.
# Env: HERMES_OTEL_SPAN_BATCH_SCHEDULE_DELAY_MS
span_batch_schedule_delay_ms: 1000

# Max spans per OTLP POST.
# Env: HERMES_OTEL_SPAN_BATCH_MAX_EXPORT_BATCH_SIZE
span_batch_max_export_batch_size: 512

# Per-export HTTP timeout.
# Env: HERMES_OTEL_SPAN_BATCH_EXPORT_TIMEOUT_MS
span_batch_export_timeout_ms: 30000
```

See [Batch export tuning](/configuration/batch-export).

## Multi-backend fan-out

See the dedicated [Multi-backend](/backends/multi-backend) page — full grammar, per-backend overrides, env-var interpolation, and debugging tips.

```yaml
backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces
  # ...
```

When `backends:` is absent or empty, the plugin falls back to single-backend env-var detection.

## Full example

```yaml
enabled: true
sample_rate: 0.25
project_name: hermes-prod

global_tags:
  team: platform

resource_attributes:
  env: prod
  region: us-east-1

headers:
  X-Scope-OrgID: tenant-a

preview_max_chars: 1200
capture_previews: true
capture_conversation_history: true
conversation_history_max_chars: 40000

capture_logs: true
log_level: INFO

root_span_ttl_ms: 600000
flush_interval_ms: 60000
force_flush_on_session_end: true

span_batch_max_queue_size: 4096
span_batch_schedule_delay_ms: 500

backends:
  - type: lgtm
    endpoint: http://localhost:4318/v1/traces

  - type: phoenix
    endpoint: http://localhost:6006/v1/traces

  - type: langfuse
    public_key_env: LANGFUSE_PUBLIC_KEY
    secret_key_env: LANGFUSE_SECRET_KEY
    base_url: https://cloud.langfuse.com
```
