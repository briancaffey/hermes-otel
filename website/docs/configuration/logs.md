---
sidebar_position: 8
title: "Logs and events"
description: "The third signal: Python log records and structured hermes.* / GenAI events, attributed to the turn exactly, redacted, shipped to any OTLP logs backend and the dashboard."
---

# Logs and events

The logs signal has two switches. **`logs.capture`** forwards Python `logging` records (Hermes' own loggers and every library it imports) to the log-capable backends and the dashboard's Logs tab. **`logs.events.enabled`** emits the plugin's own structured events from the hooks: one record per tool call, approval, API error, turn, sub-agent run and model call, with the GenAI semantic-convention inference record among them. Both are **off by default**; either works without the other.

Whatever the source, one processor runs ahead of every sink and makes each record the same everywhere: it stamps the trace and span ids of the turn the line belongs to (never by guessing, see [Where the ids come from](#where-the-ids-come-from)), adds the session attributes, drops the host-internal attributes Hermes puts on every record, and redacts secrets with Hermes' own redactor. That is what makes "jump from this log line to the span that emitted it" work in Grafana, SigNoz, OpenObserve and Uptrace, and what keeps a backend from seeing anything `agent.log` would not. The live store behind the dashboard sits on the same provider, so the dashboard and the backend show the same record.

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

Besides forwarding Python log records, the plugin can emit **structured events** from the hooks; the full catalogue, generated from the code, is the [log events reference](/reference/log-events). An event is one OTel log record per occurrence with an `event_name`, emitted through `Logger.emit` on the plugin's own scope (`hermes_otel`, version = the plugin version). Events never go through the stdlib bridge, so they work with `logs.capture: false` as long as a log-capable backend or the live store exists. They carry the turn's trace context (attribution `context`), the session attributes, the same attribute names as the span they mirror, and they are redacted like every other record. The metrics stay the aggregate; an event is the per-occurrence record, the way Claude Code and Gemini CLI split the two.

**Off by default.** Turn them on with:

```yaml
logs:
  events:
    enabled: true
    content: inherit    # inherit (follow content_capture) | full | preview | off
```

`content` governs only the content attributes (`gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`, `input.value`, `output.value`, the sub-agent goal and summary): `full` keeps what the span has, `preview` clips to `preview_max_chars`, `off` drops them. Metadata (ids, counts, statuses, durations) always travels. Under `content_capture: off` the spans never captured content, so an event has none whatever `content` says.

Per backend, `logs: {events: {enabled: false}}` drops the events for that backend only, and `logs: {events: {content: off}}` (or `preview`) narrows the content for that backend only, through a copy of the record; the other backends keep what the global setting allows. Narrower only: a wider per-backend value warns and is ignored.


The events are `hermes.turn.start`, `hermes.turn.end`, `hermes.tool.call`, `hermes.approval.decision`, `hermes.api.error`, `hermes.subagent.start`, `hermes.subagent.stop`, `hermes.session.finalize` and `gen_ai.client.inference.operation.details`; a blocked tool or a denied approval is `WARN`, a failed turn or a non-retryable API error is `ERROR`. `hermes.tool.duration_s` and `hermes.turn.duration_s` exist only on events (the spans carry `*_ms`). The `gen_ai.client.inference.operation.details` event is the GenAI semantic convention's inference record: request attributes from the api span's start, response attributes from `post_api_request`, and content under the semconv names; the OpenInference-only names (`llm.*`, `input.value` / `output.value`) stay on the span.

In the dashboard's Logs tab an event shows its name as a badge, and the **events only** filter (`?events=1`) hides plain log lines. The skill CLI and the backend adapters see events as log records with an `event_name` field.

## What correlation looks like

When `logs.capture` is on and hermes-agent code calls:

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

## Which backends accept logs?

Per backend, `supports_logs` is derived from the type and overridden with `logs: true|false` (or the mapping form, see [Per backend](#per-backend)):

| Backend | Logs | Notes |
|---|---|---|
| [SigNoz](/backends/signoz) | ✅ | default on |
| [Generic OTLP](/backends/otlp) | ✅ | default on; the collector must accept `/v1/logs` |
| [Grafana LGTM](/backends/lgtm) | ✅ | Loki; default on |
| [OpenObserve](/backends/openobserve) | ✅ | default on; attributes flattened (`hermes_session_id`, `hermes_log_attribution`) |
| [Uptrace](/backends/uptrace) | ✅ | default on |
| [Parseable](/backends/parseable) | ✅ | default on; `logs_dataset` names the dataset |
| [Honeycomb](/backends/honeycomb) | ✅ | default on; one dataset per signal with Classic keys |
| [Phoenix](/backends/phoenix) | ❌ | traces only |
| [Langfuse](/backends/langfuse) | ❌ | traces only |
| [LangSmith](/backends/langsmith) | ❌ | not OTLP |
| [Jaeger](/backends/jaeger) | ❌ | traces only |
| [Grafana Tempo](/backends/tempo) | ❌ | traces only; use [LGTM](/backends/lgtm) for all three signals |
| [W&B Weave](/backends/weave) | ❌ | trace ingest only |

With `logs.capture` or `logs.events.enabled` on and **no** configured backend accepting logs, the records still reach the live store (the dashboard's Logs tab) and the startup banner says `Logs → live store only`; with the live store off as well, a single warning is printed and Python logging is left alone.

## The loop guard (`exclude_loggers`)

The OTel HTTP exporter POSTs log batches with `urllib3` / `requests`. With the root logger at DEBUG, each export would log a line like `"POST /v1/logs HTTP/1.1" 200`, which would be captured, batched and exported, producing the next line. The default `exclude_loggers` list drops `opentelemetry.*`, `urllib3.*`, `httpx`, `httpcore` and `requests` for that reason, plus the third-party loggers Hermes itself pins at `WARNING`. The loop is bounded by the batch queue either way; the guard keeps it from crowding real lines out. To debug the exporter itself, scope capture to one logger (`attach_logger: hermes_otel`) instead of removing the guard.

## Startup banner

When the pipeline is on, the plugin prints one line so it is never silent:

```text
[hermes-otel] ✓ Logs → 2 backend(s), live store (attached to root, level=INFO, events on)
[hermes-otel] ✓ Log events → 2 backend(s), live store (stdlib logs not captured)
[hermes-otel] Logs → live store only (no configured backend accepts OTLP logs: phoenix)
```

The first form is `logs.capture` (with `events on` when both switches are set), the second is events without the stdlib bridge, the third is either switch with no log-capable backend. If the banner says "attached to `hermes_otel`" you are in scoped mode and Hermes' own loggers do not flow. If no banner appears after turning a switch on, check that `opentelemetry-sdk` is at least 1.35 (the declared floor; the plugin warns when the logs module is missing) and that the config file is the one being read (the dashboard's Settings tab shows the file and every effective value).

## Privacy

[Privacy mode](/configuration/privacy) (`content_capture: off`) suppresses **span** content and, through `logs.events.content: inherit`, the content attributes on events. It does **not** filter forwarded log records: a record's body is whatever Hermes or a library passed to `logger.info(...)`. Secrets in it are redacted (see [What never leaves the machine](#what-never-leaves-the-machine)); other sensitive text flows unless the logging application redacts it first. If a chatty logger logs user text at INFO, exclude it with `exclude_loggers`, raise its floor with `logger_levels`, or ship only turn-attributed lines with `only_in_turn: true`.

## Interaction with other signals

- **Shared resource.** Logs, events, traces and metrics inherit the same `Resource` (`service.name`, `service.version`, `hermes.profile`, `global_tags`, `resource_attributes`, `project_name`).
- **Per-backend fan-out.** Each log-capable backend gets its own `BatchLogRecordProcessor` (queue and worker thread, tuned by `logs.batch.*`) behind its own rules filter; a slow Loki cannot block a fast SigNoz.
- **Flushed with the rest.** The logger provider's `force_flush` runs from the same turn-end and `atexit` paths as spans and metrics.
- **Events and metrics.** The counters stay the aggregate; an event is the per-occurrence record. Nothing is derived from one to the other.

## Verifying

Each of these is something the test suite or a script runs, not a promise:

1. **Local, no backend.** Turn on `logs.capture` (or `logs.events.enabled`), run a turn, then read the live store from the agent's own skill, `python3 ${HERMES_SKILL_DIR}/scripts/otel.py logs --since 1h` (events carry an `event_name` field), or directly:

   ```bash
   sqlite3 "$HERMES_HOME/hermes_otel_live.db" "select level, json_extract(data,'$.event_name'), trace_id, json_extract(data,'$.attributes.\"hermes.log.attribution\"'), substr(json_extract(data,'$.body'),1,60) from events where kind='log' order by ts desc limit 10"
   ```

   A line logged during a turn has a 32-hex `trace_id` and an attribution value; one logged between turns has neither.
2. **Against a backend.** `tests/smoke/test_hermes_openobserve_logs.py` runs a real turn and asserts that an attributed log record in OpenObserve carries a `trace_id` matching a span in the traces stream (skipped when the services are not running; see its docstring for the setup). In Grafana, Loki's derived field on `trace_id` opens the Tempo trace, and Tempo's "Logs for this span" runs `{trace_id="<id>"}` against Loki.
3. **Unit level.** `tests/unit/test_logs_correlation.py` drives `on_session_start → pre_api_request → logger.info` and asserts the ids, the session attributes, the attribution tier, the absence of `hermes_home` and a redacted token; `tests/unit/test_log_events.py` covers every event, the content modes and the per-backend narrowing.

## See also

- [Log events reference](/reference/log-events) — every event, generated from the code.
- [Config schema](/reference/config-schema) — every `logs.*` field with its default.
- [Privacy](/configuration/privacy), [Batch export](/configuration/batch-export), [Dashboard](/dashboard), [The observability skill](/skill).
- [Limitations](/reference/limitations#logs-are-attributed-exactly-or-not-at-all).
