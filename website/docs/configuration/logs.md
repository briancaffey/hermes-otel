---
sidebar_position: 8
title: "OTel logs"
description: "Ship Python logger.info(...) calls to Loki or any OTLP logs receiver with automatic trace-id correlation — the third OTel signal."
---

# OTel logs

Opt-in pipeline that captures Python `logging` records and ships them to any log-capable backend (Loki via the [LGTM stack](/backends/lgtm), SigNoz, OpenObserve, Uptrace, or any OTLP collector) as the OTel logs signal, and into the dashboard's Logs tab. One processor runs ahead of every sink and makes each record the same everywhere: it stamps the trace and span ids of the turn the line belongs to (never by guessing, see below), adds the session attributes, drops the host-internal attributes Hermes puts on every record, and redacts secrets with Hermes's own redactor. That is what makes the "jump from this log line to the span that emitted it" workflow in Grafana / SigNoz work, and what keeps a backend from seeing anything `agent.log` would not.

**Off by default.** Attaching a handler to Python's root logger is invasive — it exports records from every library hermes-agent imports, not just the plugin. Turn it on deliberately.

## Configuration

Everything about logs lives under one `logs:` block in `$HERMES_HOME/hermes_otel.yaml`:

```yaml
logs:
  capture: true                  # the switch; off by default
  level: INFO                    # handler level: DEBUG, INFO, WARN, ERROR
  attach_logger: null            # null = root logger; "agent", "gateway", "tools", "hermes_cli",
                                 # "hermes_otel" or any logger name to scope capture
  exclude_loggers: [opentelemetry, urllib3, httpx, httpcore, requests, openai,
                    asyncio, hpack, grpc, websockets, charset_normalizer, markdown_it]
  logger_levels: {gateway.config: WARNING}   # per-logger floors
  only_in_turn: false            # drop lines the plugin cannot attribute to a session
  max_attribute_length: 4096     # longest exported string attribute
  live_min_level: INFO           # floor for the dashboard's live Logs tab
  batch:                         # BatchLogRecordProcessor knobs
    schedule_delay_ms: 1000
    max_queue_size: 2048
    max_export_batch_size: 512
    export_timeout_ms: 30000
  events:                        # structured hermes.* / GenAI events (Phase 2 of #240; off)
    enabled: false
    content: inherit             # inherit (follow content_capture) | full | preview | off
```

Every key is also a flat field (`capture_logs`, `log_level`, `log_attach_logger`, `log_exclude_loggers`, `log_logger_levels`, `log_only_in_turn`, `log_max_attribute_length`, `log_batch_*`, `log_live_min_level`, `log_events`, `log_events_content`) and a `HERMES_OTEL_<FIELD>` environment variable (lists are comma-separated), so an older `capture_logs: true` keeps working; the loader prints one line saying the block is preferred, and when both spellings disagree the block wins with a warning. The [config schema](/reference/config-schema) lists each field with its block path.

### Per backend

A backend entry's `logs:` is either `true` / `false` (the signal switch, as before) or a mapping, which turns the signal on and narrows the global settings for that backend only:

```yaml
backends:
  - type: openobserve
    endpoint: http://localhost:5080/api/default/v1/traces
    logs:
      level: WARN                # this backend gets WARN and above
      exclude_loggers: [tools]   # added to the global list
      logger_levels: {gateway: ERROR}
      only_in_turn: true
      events: {content: off}     # no prompt content on events here (Phase 2)
```

Per-backend settings can only be **stricter**: a lower `level`, a wider `events.content` or `only_in_turn: false` against a global `true` is refused with a warning and the global value stays. Secret redaction is global and cannot be turned down per backend. The dashboard's OTel → Settings tab shows each backend's effective values, and its effective-YAML view renders the block form.

### Default exclusions

The default `exclude_loggers` list is the loop guard (the OTel SDK and the HTTP client stack the exporter uses, so an export can never log itself into the next export) plus the third-party loggers Hermes itself pins at `WARNING`. Replacing the list replaces the loop guard too; keep `opentelemetry`, `urllib3`, `httpx`, `httpcore` and `requests` in it unless you are debugging the exporter from a scoped logger.

### Severity spelling

The live store and every backend use the OTel display spellings (`WARN`, `FATAL`, severity numbers 1 to 24). The dashboard, the skill CLI and the backend adapters read both spellings; `live_min_level`, `level` and `logger_levels` accept either (`WARN` or `WARNING`).

### The SDK handler

The plugin bridges stdlib `logging` with the OTel SDK's `LoggingHandler`, which opentelemetry-python deprecated in 1.40 in favour of `opentelemetry-instrumentation-logging`. The SDK handler is still the right tool here (it attaches to one chosen logger and feeds one provider that several sinks share), and a test pins its import path. When the SDK removes it, the fallback is the instrumentation package's handler behind the same `install_handler` seam; nothing in this page changes.

## Events

Besides forwarding Python log records, the plugin can emit **structured events** from the hooks: one OTel log record per occurrence with an `event_name`, emitted through `Logger.emit` on the plugin's own scope (`hermes_otel`, version = the plugin version). Events never go through the stdlib bridge, so they work with `logs.capture: false` as long as a log-capable backend or the live store exists. They carry the turn's trace context (attribution `context`), the session attributes, the same attribute names as the span they mirror, and they are redacted like every other record. The metrics stay the aggregate; an event is the per-occurrence record, the way Claude Code and Gemini CLI split the two.

**Off by default.** Turn them on with:

```yaml
logs:
  events:
    enabled: true
    content: inherit    # inherit (follow content_capture) | full | preview | off
```

`content` governs only the content attributes (`gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`, `input.value`, `output.value`, the sub-agent goal and summary): `full` keeps what the span has, `preview` clips to `preview_max_chars`, `off` drops them. Metadata (ids, counts, statuses, durations) always travels. Under `content_capture: off` the spans never captured content, so an event has none whatever `content` says.

Per backend, `logs: {events: {enabled: false}}` drops the events for that backend only, and `logs: {events: {content: off}}` (or `preview`) narrows the content for that backend only, through a copy of the record; the other backends keep what the global setting allows. Narrower only: a wider per-backend value warns and is ignored.

| Event | Emitted from | Severity | Attributes |
|---|---|---|---|
| `hermes.turn.start` | pre_llm_call | INFO | `hermes.session_id`, `gen_ai.conversation.id`, `hermes.platform`, `gen_ai.request.model`, `gen_ai.provider.name`, `hermes.turn.number` |
| `hermes.turn.end` | on_session_end | INFO; WARN when interrupted; ERROR when failed | `hermes.turn.number`, `hermes.turn.final_status`, `hermes.turn.exit_reason`, `hermes.turn.api_call_count`, `hermes.turn.tool_count`, `hermes.turn.tools`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.reasoning.output_tokens`, `hermes.cost.usage`, `hermes.turn.duration_s`, `error.type` |
| `hermes.tool.call` | post_tool_call | INFO; WARN when blocked or timed out; ERROR on error | `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.tool.type`, `hermes.tool.outcome`, `hermes.tool.decided_by`, `hermes.tool.duration_s`, `gen_ai.tool.call.arguments (content)`, `gen_ai.tool.call.result (content)` |
| `hermes.approval.decision` | post_approval_response | INFO; WARN when denied or timed out | `hermes.approval.choice`, `hermes.approval.granted`, `hermes.approval.decided_by`, `hermes.approval.surface`, `hermes.approval.timed_out`, `hermes.approval.duration_ms`, `gen_ai.tool.name` |
| `hermes.api.error` | api_request_error | ERROR; WARN when retryable | `error.type`, `http.response.status_code`, `hermes.retryable`, `hermes.retry.count`, `gen_ai.request.model`, `gen_ai.provider.name`, `exception.type`, `exception.message` |
| `hermes.subagent.start` | subagent_start | INFO | `hermes.subagent.role`, `hermes.subagent.child_session_id`, `hermes.subagent.parent_session_id`, `hermes.subagent.goal (content)` |
| `hermes.subagent.stop` | subagent_stop | INFO; ERROR when the sub-agent reports failure | `hermes.subagent.role`, `hermes.subagent.status`, `hermes.subagent.duration_ms`, `hermes.subagent.child_session_id`, `hermes.subagent.parent_session_id`, `hermes.subagent.summary (content)` |
| `hermes.session.finalize` | on_session_finalize / on_session_reset | INFO | `hermes.session.turn_count`, `hermes.session.duration_s`, `hermes.session.finalize_reason / hermes.session.reset_reason` |
| `gen_ai.client.inference.operation.details` | post_api_request | INFO | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.conversation.id`, `gen_ai.input.messages (content)`, `gen_ai.output.messages (content)`, `gen_ai.system_instructions (content)` |

Attributes marked *(content)* are the ones `content` governs. `hermes.tool.duration_s` and `hermes.turn.duration_s` exist only on events (the spans carry `*_ms`). The `gen_ai.client.inference.operation.details` event is the GenAI semantic convention's inference record: request attributes from the api span's start, response attributes from `post_api_request`, and content under the semconv names; the OpenInference-only names (`llm.*`, `input.value` / `output.value`) stay on the span.

In the dashboard's Logs tab an event shows its name as a badge, and the **events only** filter (`?events=1`) hides plain log lines. The skill CLI and the backend adapters see events as log records with an `event_name` field.

## What correlation looks like

When `capture_logs` is on and hermes-agent code calls:

```python
logger.info("tool complete tool=%s outcome=%s", tool_name, outcome)
```

...inside an active span, the resulting Loki record carries:

```
{
  body: "tool complete tool=Bash outcome=completed",
  severity_text: "INFO",
  trace_id: "4bf92f3577b34da6...",   # ← the turn's trace_id
  span_id:  "00f067aa0ba902b7",       # ← the innermost open span of that turn
  attributes: {
    "hermes.session_id": "20261003_201501_ab12",
    "gen_ai.conversation.id": "20261003_201501_ab12",
    "hermes.platform": "telegram",
    "hermes.log.attribution": "session_tag",   # how the ids were found, see below
    "code.function.name": "...", "code.file.path": "...", "code.line.number": 123
  },
  resource: { "service.name": "hermes-agent", "hermes.profile": "default", ... }
}
```

In Grafana, Loki's built-in derived field picks the `trace_id` up automatically — clicking it opens the Tempo trace view. Conversely, opening a span in Tempo and clicking "Logs for this span" runs `{trace_id="<id>"}` against Loki and surfaces exactly the logs emitted during that span.

No app-side context plumbing required. The stdlib `logging` module is the integration point.

### Where the ids come from

Hermes' loggers write from the agent's own threads, where the plugin's spans are not on the OpenTelemetry context, so the SDK's handler alone would export every record with an empty `trace_id`. The plugin attributes each record itself, through three tiers that are each exact. It never guesses, and it records which tier applied in `hermes.log.attribution`:

| `hermes.log.attribution` | When | What it means |
|---|---|---|
| `context` | A span was current on the thread that logged the line | The record already carried the ids; left as is. |
| `session_tag` | Hermes stamped its own per-thread session id on the record (`%(session_tag)s`, set on the agent's turn thread) | The ids come from that session's innermost open span (the tool or LLM span if one is open, else the turn's root). Exact under any number of concurrent sessions. Between turns the session id is kept and the trace ids stay empty. |
| `single_session` | Exactly one session has a turn in flight | The line can only belong to that session, so it gets that session's innermost open span. |
| *(absent)* | Several sessions active, and the line names none of them | Unattributed, on purpose: a wrong session id on a log line is worse than none. |

How far the `session_tag` tier reaches was measured on a real gateway (20 000 lines of `agent.log`, 2026-10-03): Hermes sets the tag on the turn thread, so `agent.*` lines carry it about a quarter of the time and `cli` lines about half, while tool execution (`tools.*`, 1%), platform adapters and gateway housekeeping (`gateway.*`, `plugins.*`, `hermes_cli.*`, 0%) log from other threads and depend on the `single_session` tier. For a single-user gateway that tier covers them; for a multi-user gateway those lines stay unattributed until Hermes propagates its session context across its thread pools.

### What never leaves the machine

Hermes' record factory puts `hermes_home` (the resolved home directory) and `session_tag` on every record, and the SDK handler would copy both onto the exported record. The processor removes them, together with any attribute ending in `.raw_home` or `_home_path`. `session_tag` is consumed into `hermes.session_id` first.

Secrets are redacted from the body and from every string attribute before any sink sees the record, with Hermes's own `agent.redact` (the same redactor behind `agent.log`, honouring `security.redact_secrets` / `HERMES_REDACT_SECRETS` per profile) when the plugin runs inside Hermes, and with a built-in set (provider key prefixes, bearer and basic auth, `Authorization` and `x-api-key` style headers, `key=value` credential assignments, URL userinfo) when it does not. Measured cost with Hermes's redactor: about 34 µs for a typical 75-character line, 73 µs per 1 KB, 75 ms per 1 MB of plain text and up to 580 ms per 1 MB when it contains secrets; a busy turn's few hundred lines cost a few tens of milliseconds in total. The built-in set runs at 8 µs to 84 µs for the same sizes.

### One-shot runs export no logs

`hermes -z "..."` disables Python logging for the whole process (`logging.disable(CRITICAL)`), before any handler can see a record, so a one-shot run exports traces and metrics but never logs. To exercise the logs signal from a script, use `hermes chat -Q --yolo -q "..."` (the gateway and the interactive CLI keep logging on).

## Fields

The reference for every field, with its default and `logs:` path, is generated from the code: [config schema → Top level](/reference/config-schema#top-level). Two notes that are not in the table:

- `attach_logger`: start broad (root) and narrow when the signal-to-noise ratio gets bad. Hermes' logger families are `agent` (the agent loop, where the session tag lives), `gateway`, `tools`, `hermes_cli`, `cli` and `run_agent`; there is no `hermes` logger.
- `level` applies to the OTLP path; the live Logs tab has its own `live_min_level` (default `INFO`) so a `DEBUG` export does not flood the bounded live buffer.

## Which backends accept logs?

Set per-backend via `supports_logs` (auto-derived from `type`, overrideable via `logs: true|false` in `config.yaml`):

| Backend | Logs |
|---|---|
| [Phoenix](/backends/phoenix) | ❌ (traces-only) |
| [Langfuse](/backends/langfuse) | ❌ |
| [LangSmith](/backends/langsmith) | ❌ (non-OTLP) |
| [SigNoz](/backends/signoz) | ✅ |
| [Jaeger](/backends/jaeger) | ❌ |
| [Grafana Tempo](/backends/tempo) | ❌ (traces-only; use [LGTM](/backends/lgtm) for all three signals) |
| [Generic OTLP](/backends/otlp) | ✅ (default on; collector must accept `/v1/logs`) |
| [LGTM](/backends/lgtm) | ✅ |

If `capture_logs` is on but **no** configured backend accepts logs, the plugin logs a single warning at startup and leaves Python logging alone.

## The loop-avoidance filter (now `exclude_loggers`)

The OTel HTTP exporter uses `urllib3` (via `requests`) to POST log batches to the collector. If the root logger is at DEBUG, `urllib3.connectionpool` emits a DEBUG line like `http://localhost:4318 "POST /v1/logs HTTP/1.1" 200 2` for every export — which would then get captured, batched, exported, producing another line.

The plugin installs a `logging.Filter` on its handler that **drops records from these logger prefixes**:

- `opentelemetry.*` — the SDK's own export-failure warnings
- `urllib3.*`, `httpx`, `httpcore`, `requests` — HTTP client libraries that log outbound calls

The loop isn't infinite (the `BatchLogRecordProcessor` queue is bounded) but it spams real application logs out of Loki. The filter makes the problem go away.

If you need to debug the OTel exporter itself, scope capture to a specific logger instead (`log_attach_logger: hermes_otel`) so the full firehose is out of scope.

## Startup banner

When logs are on, the plugin prints a banner so it's never silent:

```text
[hermes-otel] ✓ Logs → 2 backend(s) (attached to root, level=INFO)
```

If the banner says "attached to `hermes_otel`" you're in scoped mode — hermes-agent's own logs won't flow.

If the banner is **absent** after setting `capture_logs: true`, check:

1. `opentelemetry-sdk` is 1.35 or newer, the declared floor (CI runs the suite on exactly that version); older SDKs lack the `on_emit` log-processor API the plugin's log pipeline uses, and the plugin warns if the import fails.
2. At least one configured backend has `supports_logs=True`.
3. The config file is actually being read (plugin installs default handler when `pyyaml` is missing).

## Not suppressed by privacy mode

[Privacy mode](/configuration/privacy) (`capture_previews: false`) suppresses **span** previews (user messages, tool args/results) but does **not** touch logs. The log body is whatever the application passed to `logger.info(...)`; secrets in it are redacted (see [What never leaves the machine](#what-never-leaves-the-machine)), other sensitive content flows unless the application redacts it first.

This is deliberate: privacy mode reasons about plugin-captured attributes, not about what host-app code chooses to log. If your app logs user messages at INFO and you also want those suppressed, either scope capture with `log_attach_logger` to exclude the chatty logger, or filter at the application's logging layer.

## The live store sees the same record

With `dashboard_live` on, the dashboard's Logs tab is fed by a second processor on the same provider, after the enrichment step, so a row there has the same ids, attributes and redaction as the record a backend stores. The live tail is floored at `INFO` (Hermes' DEBUG firehose would evict useful lines from the bounded buffer) and skips a few gateway housekeeping lines (`gateway.config`, platform probes); OTLP export keeps `log_level`.

## Interaction with other signals

- **Shared resource.** Logs, traces, and metrics all inherit the same `Resource` (`service.name`, `service.version`, `global_tags`, `resource_attributes`, `project_name`). Set attributes once on the plugin's resource and they appear on every signal.
- **Per-backend fan-out.** Just like traces and metrics, each log-capable backend gets its own `BatchLogRecordProcessor` with an independent queue and worker thread. A slow Loki can't block a fast SigNoz.
- **Flushed on shutdown.** The logger provider's `force_flush` is called from the same atexit hook that flushes spans and metrics, so process exit doesn't drop buffered records.

## Verifying

Easiest smoke test: run a Hermes turn with `capture_logs: true`, open Loki, query `{service_name="hermes-agent"}`, and look for a log whose `trace_id` is non-zero. Click the `trace_id` link — it should open the Tempo trace that was active when that log fired.

For a programmatic check:

```bash
curl -s 'http://localhost:3100/loki/api/v1/query?query={service_name="hermes-agent"}' \
  | jq '.data.result[0].values[0]'
```

If the returned JSON includes a `traceID` / `trace_id` field with a 32-char hex value, correlation is working.

## See also

- [LGTM stack](/backends/lgtm) — the recommended all-signals local stack for logs.
- [SigNoz](/backends/signoz) — the other OSS backend with native logs support.
- [Generic OTLP](/backends/otlp) — pointing at any log-capable OTLP collector.
- [Env vars reference](/reference/env-vars#hermes_otel_-overrides) — `HERMES_OTEL_CAPTURE_LOGS`, `HERMES_OTEL_LOG_LEVEL`, `HERMES_OTEL_LOG_ATTACH_LOGGER`.
- [Config schema — top level](/reference/config-schema) — full field reference.
