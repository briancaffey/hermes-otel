---
sidebar_position: 2
title: "config.yaml"
description: "Full reference for the plugin's YAML config file — project metadata, sampling, preview shaping, orphan sweep, batch tuning, and multi-backend fan-out."
---

# `config.yaml`

A fully annotated example ships in the repository as `config.yaml.example`. This page walks through the fields by topic, with their defaults. The [config schema](/reference/config-schema) is generated from the code and lists every field, including the few this page only links to.

Location: `$HERMES_HOME/hermes_otel.yaml` (recommended), or `$HERMES_OTEL_CONFIG`, or the legacy `$HERMES_HOME/plugins/hermes_otel/config.yaml`; see [Where does config.yaml live?](/configuration/overview). The file is parsed with PyYAML, which the plugin installs as a dependency. `${VAR}` in a string value is replaced with that environment variable.

## Project / resource attributes

```yaml
# Shown in Phoenix's "Project" dropdown. Set as `openinference.project.name`
# on the Resource, so every span carries it. Env: HERMES_OTEL_PROJECT_NAME;
# when unset, OTEL_PROJECT_NAME is used.
project_name: hermes-agent

# Report each named profile as its own service (service.name=<profile>,
# service.namespace=hermes-agent); the default profile stays hermes-agent.
# See /configuration/profiles#one-service-per-profile.
# Env: HERMES_OTEL_SERVICE_NAME_FROM_PROFILE. Default: false.
service_name_from_profile: false

# Free-form key/value pairs merged into the OTel Resource (shared by every
# span, metric and log record). Good for deployment tags. The defaults
# service.name=hermes-agent, service.instance.id, service.version,
# process.pid and hermes.profile are set for you.
resource_attributes:
  team: platform
  env: prod
  region: us-east-1

# Deprecated spelling of resource_attributes: still accepted, merged into
# resource_attributes at load (which wins on a key conflict), with a warning.
# global_tags:
#   team: platform

# Stamped on every span, every log record and every metric point as a plain
# attribute, for backends that do not index resource attributes per signal
# (Loki labels, Mimir series, OpenObserve columns). Scalars only; keys of at
# most 64 characters; `hermes.`, `gen_ai.` and `service.` are reserved and
# dropped with a warning. See "Which attributes reach which signal" below.
common_attributes:
  team: otel
  cost_center: research

# Extra HTTP headers added to every OTLP request across ALL backends.
# Per-backend `headers:` are merged on top (per-backend wins on conflict).
# A per-backend `headers_helper:` command mints headers that expire; see
# /configuration/short-lived-credentials.
# This file is the only source of headers: the SDK's OTEL_EXPORTER_OTLP_*HEADERS
# variables are ignored so one backend's token never reaches another (#472).
headers:
  X-Scope-OrgID: tenant-a
```

### Which attributes reach which signal {#which-attributes-reach-which-signal}

`resource_attributes` describe the *process* and travel on the OTLP Resource, which every signal shares but which not every backend indexes per signal: Loki keeps a fixed set of resource attributes as labels, Mimir's OTLP write promotes only the standard ones to series labels, OpenObserve stores them in columns that differ per stream type. `common_attributes` (#480) are stamped on the *data points themselves*, so `team=otel` filters traces, logs and metrics alike on any backend.

| Key | Spans | Log records and events | Metric points | Resource |
|---|---|---|---|---|
| `resource_attributes` (and `global_tags`) | via the Resource | via the Resource | via the Resource | yes |
| `common_attributes` | every span on the plugin's provider, third-party (MCP, httpx) spans included | every record and event, before redaction and the length cap | every point of the plugin's instruments (the Views admit the keys); not the host/GPU observables or the SDK's own `otel.sdk.*` series | no |
| `project_name` | via the Resource (`openinference.project.name`) | via the Resource | via the Resource | yes |
| profile | `hermes.profile` on the root span | — | `profile` label on every point | `hermes.profile` |
| session id | `session.id` / `hermes.session.id` on the root | `hermes.session_id` / `gen_ai.conversation.id` | never (compat instruments only, with `metric_name_compat_session_id`) | no |

Rules: values are scalars (string, number, bool); a key is at most 64 characters and never under `hermes.`, `gen_ai.` or `service.` (the plugin's vocabularies and the service identity); a rejected entry is dropped with a warning at load and the rest apply. A key the plugin already sets on a span, record or point is left alone: the plugin's attribute wins. `${VAR}` references expand like everywhere else in the file. Because the values are constant for the process they do not fan metric series out, but they are part of each series' identity: changing `team: otel` to `team: platform` and restarting starts new series (the old ones go stale), exactly as a change to `service.instance.id` would.

## Master switch

```yaml
# Master kill switch. false = no pipeline, no hooks registered, no spans.
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

## Content capture and previews

```yaml
# How much conversation content is recorded:
#   full     (default) the complete request and response on every api.* span,
#            unclipped; clipped previews on the other spans
#   preview  clipped previews only
#   off      no content at all; names, timings, tokens and status only
# Quote it: a bare `off` is YAML for false (accepted, and read as off).
# Env: HERMES_OTEL_CONTENT_CAPTURE
content_capture: "full"

# Cap on preview strings (tool args and results, user message, assistant
# response). Longer values are clipped with a trailing "...".
# Env: HERMES_OTEL_PREVIEW_MAX_CHARS
preview_max_chars: 1200

# Per-category caps; null = preview_max_chars.
# Env: HERMES_OTEL_TOOL_INPUT_PREVIEW_MAX_CHARS, and so on.
tool_input_preview_max_chars: null
tool_output_preview_max_chars: null
llm_input_preview_max_chars: null
llm_output_preview_max_chars: null

# Also attach the conversation Hermes held at the start of the turn to the
# llm.* span's input.value, as JSON. Mostly redundant with content_capture:
# full, which already puts the complete request on each api.* span.
# Env: HERMES_OTEL_CAPTURE_CONVERSATION_HISTORY
capture_conversation_history: false

# Cap on that JSON; clipped with "..." if bigger.
conversation_history_max_chars: 20000

# Gateway sessions add hermes.sender.id and user.id (`platform:sender`).
# Can identify people, so it is opt-in. Env: HERMES_OTEL_CAPTURE_SENDER_ID
capture_sender_id: false
```

`capture_previews`, `capture_full_prompts` and `capture_full_responses` are the pre-1.11 spellings of `content_capture`. They still work (`capture_previews: false` means `off`) and log a deprecation warning. See [Conversation capture](/configuration/conversation-capture) for what each mode writes, and [Privacy mode](/configuration/privacy) for `off`.

## Logs (opt-in)

```yaml
logs:
  # Forward Python logging records (logger.info(...)) to every log-capable
  # backend and the dashboard's Logs tab, stamped with the turn's trace and
  # span ids. Off by default: attaching to the root logger also exports the
  # logs of every library Hermes imports.
  # Env: HERMES_OTEL_CAPTURE_LOGS
  capture: false

  # Minimum severity: DEBUG, INFO, WARNING (or WARN), ERROR, CRITICAL.
  # Env: HERMES_OTEL_LOG_LEVEL
  level: INFO

  # Which Python logger to attach to. null = root (everything);
  # "hermes_otel" = the plugin's own lines only.
  # Env: HERMES_OTEL_LOG_ATTACH_LOGGER
  attach_logger: null

  # Structured hermes.* / GenAI events from the hooks. Off by default.
  # Env: HERMES_OTEL_LOG_EVENTS
  events:
    enabled: false
```

The flat spellings (`capture_logs`, `log_level`, `log_attach_logger`, …) still work. See [Logs and events](/configuration/logs) for every key, the loop guard for HTTP client libraries, and which backends accept logs.

## Metrics

```yaml
# Emit OTel GenAI semantic-convention metrics (gen_ai.client.*, gen_ai.agent.*)
# alongside the custom hermes.* metrics, so generic OTel-GenAI dashboards and
# alerts work without per-user wiring. Set false for hermes.* only.
# Env: HERMES_OTEL_EMIT_GENAI_METRICS. Default: true.
emit_genai_metrics: true

# Temporality: cumulative (Prometheus family) or delta (Datadog, New Relic,
# Logfire). Unset = the SDK default (cumulative, or
# OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE). SigNoz, Uptrace and
# Elastic entries default to delta; a backend entry's own
# metrics_temporality wins. Env: HERMES_OTEL_METRICS_TEMPORALITY
metrics_temporality: null

# Histogram aggregation: explicit (fixed bucket boundaries) or exponential
# (base-2, for backends that accept it). Env: HERMES_OTEL_METRICS_HISTOGRAM
metrics_histogram: explicit

# Distinct values a label such as `model` may take per process before the
# rest fold into `other`; 0 = no cap. Env: HERMES_OTEL_METRICS_LABEL_LIMIT
metrics_label_limit: 100

# Export session count, tokens, cost and active time under Claude Code's
# metric names: off | claude_code (instead of hermes.*) | both.
# See /guides/claude-code-dashboards. Env: HERMES_OTEL_METRIC_NAME_COMPAT
metric_name_compat: "off"
```

`flush_interval_ms` (below) sets how often metrics are exported. See the [Metrics reference](/reference/metrics#otel-genai-semantic-convention-metrics) for the full instrument list, units, and labels.

## Disabling metrics and span families {#disable}

Metrics are otherwise all-or-nothing per backend (`metrics: false`) and spans can only be sampled (`sample_rate`) or switched off per backend (`traces: false`). These two lists (#479) switch off individual instruments and whole span families while everything else keeps flowing.

```yaml
# Instruments never created, recorded or exported. Write the OTLP name, the
# hook key (tool_duration) or the key with dots (tool.duration); `all` disables
# every instrument. An unknown name logs a warning listing the valid ones.
# Env: HERMES_OTEL_DISABLE_METRICS=tool.duration,token_usage (comma-separated).
disable_metrics:
  - tool.duration
  - hermes.approval.duration

# Span families never recorded. A disabled span is replaced by a non-recording
# stand-in that carries its parent's context, so whatever would have nested
# under it parents to the nearest recording ancestor: with `llm` disabled the
# api.* spans sit directly under the turn root; with `api` disabled the tool.*
# spans sit under llm.*. The hooks still run, so the metrics of a disabled
# family (hermes.tool.duration for `tool`, hermes.approval.count for
# `approval`) are still recorded: use disable_metrics for those. `all` is
# refused (the turn root always records; a backend's `traces: false` is the
# switch for whole traces). Env: HERMES_OTEL_DISABLE_SPANS=approval,skill.
disable_spans:
  - approval
```

A disabled instrument does not exist for the process: it is not created on the meter, `record_metric` records nothing for it (the dashboard's live store included), and a `DropAggregation` View on its name keeps anything else recorded under that name out of the export. The Settings tab shows both lists, and the plugin logs them at init.

Valid `disable_metrics` entries, one row per instrument (the first column is what the config stores and the Settings tab shows):

[//]: # (generated:disable-metrics:start)

| Instrument | Also accepted |
|---|---|
| `hermes.span.swept.count` | `span.swept`, `span.swept.count`, `span_swept` |
| `hermes.session.count` | `session.count`, `session_count` |
| `hermes.session.turns` | `session.turns`, `session_turns` |
| `hermes.session.duration` | `session.duration`, `session_duration` |
| `hermes.active_time.total` | `active.time`, `active_time`, `active_time.total` |
| `hermes.token.usage` | `token.usage`, `token_usage` |
| `hermes.prompt_cache.tokens` | `prompt.cache.tokens`, `prompt_cache.tokens`, `prompt_cache_tokens` |
| `hermes.prompt_cache.observations` | `prompt.cache.observations`, `prompt_cache.observations`, `prompt_cache_observations` |
| `hermes.cost.usage` | `cost.usage`, `cost_usage` |
| `hermes.tool.duration` | `tool.duration`, `tool_duration` |
| `hermes.message.count` | `message.count`, `message_count` |
| `hermes.model.usage` | `model.usage`, `model_usage` |
| `hermes.auxiliary.count` | `auxiliary.count`, `auxiliary_count` |
| `hermes.skill.inferred` | `skill.inferred`, `skill_inferred` |
| `hermes.subagent.count` | `subagent.count`, `subagent_count` |
| `hermes.subagent.duration` | `subagent.duration`, `subagent_duration` |
| `hermes.api.error.count` | `api.error.count`, `api_error_count` |
| `hermes.retry.count` | `retry.count`, `retry_count` |
| `hermes.kanban.task.count` | `kanban.task.count`, `kanban_task_count` |
| `hermes.api.time_to_first_chunk` | `api.time.to.first.chunk`, `api.time_to_first_chunk`, `api_time_to_first_chunk` |
| `hermes.command.count` | `command.count`, `command_count` |
| `hermes.commit.count` | `commit.count`, `commit_count` |
| `hermes.human_input.count` | `human.input.count`, `human_input.count`, `human_input_count` |
| `hermes.skill.lifecycle.count` | `skill.lifecycle.count`, `skill_lifecycle_count` |
| `hermes.transcription.count` | `transcription.count`, `transcription_count` |
| `hermes.kanban.dispatch.count` | `kanban.dispatch.count`, `kanban_dispatch_count` |
| `hermes.kanban.dispatch.result` | `kanban.dispatch.result`, `kanban_dispatch_result` |
| `hermes.kanban.task.updated` | `kanban.task.updated`, `kanban_task_updated` |
| `hermes.approval.count` | `approval.count`, `approval_count` |
| `hermes.approval.duration` | `approval.duration`, `approval_duration` |
| `gen_ai.execute_tool.duration` | `tool.duration.s`, `tool_duration_s` |
| `hermes.approval.wait.duration` | `approval.wait.duration`, `approval.wait.s`, `approval_wait_s` |
| `hermes.subagent.run.duration` | `subagent.run.duration`, `subagent.run.s`, `subagent_run_s` |
| `gen_ai.client.token.usage` | — |
| `gen_ai.client.operation.duration` | — |
| `gen_ai.agent.token.usage` | — |
| `claude_code.session.count` | — |
| `claude_code.token.usage` | — |
| `claude_code.cost.usage` | — |
| `claude_code.active_time.total` | — |

[//]: # (generated:disable-metrics:end)

Valid `disable_spans` families, by the first segment of the span name. `auxiliary` is the auxiliary `api.*` span (titling, compression, MoA; see [Auxiliary calls](/architecture/span-hierarchy#auxiliary)), which `api` leaves alone:

[//]: # (generated:disable-spans:start)

`tool`, `api`, `llm`, `approval`, `skill`, `subagent`, `kanban`, `auxiliary`, `human_input`

[//]: # (generated:disable-spans:end)

## Skills

```yaml
# Open a skill.<name> span when the agent loads a skill (via skill_view or a
# /skills/<name>/ read), spanning from load to the turn boundary. Skills
# overlap freely. The hermes.skill.name attribute and skill_inferred counter
# are emitted regardless of this flag.
# Env: HERMES_OTEL_SKILL_SPANS. Default: true.
skill_spans: true
```

See [Span hierarchy → skill.*](/architecture/span-hierarchy#skill) for the span shape and attributes.

## MCP keepalive pings

```yaml
# MCP Python SDK 2.x (Hermes v0.21.0+) emits an OTel span for every MCP
# request, including the `ping` Hermes sends on each idle keepalive interval
# per connection. Each one lands as a standalone one-span "MCP send ping"
# trace. True drops the successful pings before any exporter or the live
# store sees them; failed pings (error status) are always kept.
# Env: HERMES_OTEL_SUPPRESS_MCP_PING_SPANS. Default: true.
suppress_mcp_ping_spans: true
```

## Joining an outer trace and the startup probe

```yaml
# When the process that launched Hermes exported HERMES_OTEL_TRACEPARENT
# (a W3C traceparent, plus optional HERMES_OTEL_TRACESTATE), every turn
# root becomes a child of that span. false ignores the variables. The
# kanban worker hand-off (plain TRACEPARENT) is separate.
# Env: HERMES_OTEL_REMOTE_PARENT. Default: true.
remote_parent: true

# At startup, open one TCP connection to each backend endpoint on a
# background thread and log "reachable" or "unreachable" per backend.
# Runs once, sends no bytes, and never delays an export.
# Env: HERMES_OTEL_PROBE_BACKENDS. Default: true.
probe_backends: true

# Add a short system-prompt section telling the model that telemetry and
# the hermes_otel:observability skill exist. Changes what the model sees
# on every turn, so it is opt-in. Env: HERMES_OTEL_DISCOVERY_PROMPT.
discovery_prompt: false
```

## Live store and host metrics

```yaml
# Keep recent spans, metrics and logs in $HERMES_HOME/hermes_otel_live.db
# (override the path with HERMES_OTEL_LIVE_DB) for the dashboard's Live mode.
# Env: HERMES_OTEL_DASHBOARD_LIVE. Default: true.
dashboard_live: true
dashboard_live_max_spans: 1000        # rows kept per kind (spans, metrics, logs)
dashboard_live_retention_hours: 168   # older rows are dropped; 0 = row cap only

# CPU / GPU sampling. See /configuration/host-metrics.
host_metrics: false
host_metrics_gpu: auto                # auto | amd | nvidia | off
host_metrics_interval_ms: 1000        # floor 50
```

## Lifecycle / cleanup

```yaml
# Orphan-turn sweep. A turn root still open after this long is closed,
# with `hermes.turn.final_status=timed_out` (or `interrupted` when the
# gateway stopped the turn).
# Env: HERMES_OTEL_ROOT_SPAN_TTL_MS. Default: 10 minutes.
root_span_ttl_ms: 600000

# A subagent.* span stays open across the parent's turns (Hermes runs
# delegations in the background). One whose subagent_stop never arrives
# is closed after this. Env: HERMES_OTEL_SUBAGENT_SPAN_TTL_MS. Default: 24 h.
subagent_span_ttl_ms: 86400000

# How often metrics are exported (PeriodicExportingMetricReader).
# Env: HERMES_OTEL_FLUSH_INTERVAL_MS. Default: 60s.
flush_interval_ms: 60000

# At the end of each turn, flush every backend's spans, then metrics and
# logs, from a background thread (500 ms per backend; a second request
# while one is queued is dropped). The trace shows up in the UI right away.
# false = rely on the batch worker's schedule.
# Env: HERMES_OTEL_FORCE_FLUSH_ON_SESSION_END. Default: true.
force_flush_on_session_end: true

# How long the turn waits for that flush before returning to Hermes.
# A one-shot run (`hermes -z`) exits right after the turn, so this bounds
# what it exports. 0 = do not wait.
# Env: HERMES_OTEL_FORCE_FLUSH_WAIT_MS. Default: 1500.
force_flush_wait_ms: 1500
```

See [Orphan-span sweep](/architecture/orphan-sweep).

## BatchSpanProcessor tunables

Each backend gets its own processor with these settings. Defaults are fine for almost every workload — tune only if you're losing spans under sustained high throughput.

```yaml
# Max spans buffered per backend; when full, the oldest are dropped.
# Env: HERMES_OTEL_SPAN_BATCH_MAX_QUEUE_SIZE
span_batch_max_queue_size: 2048

# How often the background worker wakes up to drain the queue.
# Env: HERMES_OTEL_SPAN_BATCH_SCHEDULE_DELAY_MS
span_batch_schedule_delay_ms: 1000

# Max spans per OTLP request. null = 512, or 64 when content_capture is
# full (the default), so a batch of large spans stays under receiver limits.
# Env: HERMES_OTEL_SPAN_BATCH_MAX_EXPORT_BATCH_SIZE
span_batch_max_export_batch_size: null

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

Per-entry keys you may need beyond `type` and `endpoint`: `headers`, `headers_helper` for tokens that expire ([Short-lived credentials](/configuration/short-lived-credentials)), `traces` / `metrics` / `logs` to switch a signal per backend, `certificate_file` (a private CA) and `client_certificate_file` + `client_key_file` (mTLS) for an HTTPS or gRPC endpoint, and, on a `type: otlp` entry only, `protocol: grpc` with `insecure` ([gRPC and TLS](/backends/otlp#grpc-and-tls)). The [config schema](/reference/config-schema#backends-entries) lists every entry key.

## Full example

```yaml
enabled: true
sample_rate: 0.25
project_name: hermes-prod

resource_attributes:
  team: platform
  env: prod
  region: us-east-1

common_attributes:
  team: platform

headers:
  X-Scope-OrgID: tenant-a

content_capture: "full"
preview_max_chars: 1200
capture_conversation_history: true
conversation_history_max_chars: 40000

logs:
  capture: true
  level: INFO

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

Credentials resolve the same way for the exporter and the dashboard's query adapters: an inline value (`secret_key: ...`) wins over the env var a `*_env` key names, which wins over the type's vendor fallbacks (`LANGFUSE_SECRET_KEY` and friends), and `${VAR}` references in an entry are expanded for both. An entry that sets both therefore exports and queries with one key. Prefer the `*_env` spelling so secrets stay out of the file.
