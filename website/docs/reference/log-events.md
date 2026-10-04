---
sidebar_position: 5
title: "Log events"
description: "Every structured event the plugin emits as an OTel log record — name, the hook that emits it, its severity rule and its attributes — generated from the code."
---

# Log events reference

Every structured event the plugin can emit, exactly as named in `hermes_otel.log_events.EVENTS`. An event is an OTel **log record with `event_name` set**, emitted through `Logger.emit` on the plugin's own instrumentation scope (`hermes_otel`, version = the plugin version), never through the stdlib `logging` bridge. Events are **off by default**; `logs.events.enabled: true` turns them on, and they reach every log-capable backend and the dashboard's live store as long as one exists, with or without `logs.capture`. See [Logs and events](/configuration/logs#events) for the switches and the content rules.

**What every event carries.** The trace and span ids of the turn it belongs to (the enrichment processor records the tier as `hermes.log.attribution: context`), `hermes.session_id` and `gen_ai.conversation.id`, `hermes.platform` from the session root, the resource shared with spans and metrics, and the attributes listed below using the same names as the span the event mirrors ([span attributes](/reference/span-attributes)). Bodies are one display line. Secrets are redacted before any sink sees the record, like every other log record.

**Content.** Attributes marked *content-gated* carry prompt, response or tool content and are the only ones `logs.events.content` governs: `full` keeps what the span has, `preview` clips to `preview_max_chars`, `off` drops them, `inherit` (the default) follows `content_capture`. A backend's own `logs: {events: {content: …}}` may narrow them further for that backend only. Metadata attributes always travel.

**Severity.** Severity numbers follow the OTel bands (INFO 9, WARN 13, ERROR 17); the rule per event is stated in its section. `hermes.tool.duration_s` and `hermes.turn.duration_s` exist only on events (the spans carry the `*_ms` form); everything else is shared with the spans.

This page is generated from the code by `scripts/gen_config_docs.py`; `tests/unit/test_config_surface.py` fails when it drifts, and `tests/unit/test_log_events_docs.py` fails when a hook emits an event the catalogue does not list.

## Events

[//]: # (generated:log-events:start)

### `hermes.turn.start`

Emitted from **`pre_llm_call`**. Severity: INFO. Body: `turn <n> started`.

| Attribute | Content-gated |
|---|---|
| `hermes.session_id` |  |
| `gen_ai.conversation.id` |  |
| `hermes.platform` |  |
| `gen_ai.request.model` |  |
| `gen_ai.provider.name` |  |
| `hermes.turn.number` |  |

### `hermes.turn.end`

Emitted from **`on_session_end`**. Severity: INFO; WARN when interrupted; ERROR when failed. Body: `turn <n> <final_status>`.

| Attribute | Content-gated |
|---|---|
| `hermes.turn.number` |  |
| `hermes.turn.final_status` |  |
| `hermes.turn.exit_reason` |  |
| `hermes.turn.api_call_count` |  |
| `hermes.turn.tool_count` |  |
| `hermes.turn.tools` |  |
| `gen_ai.usage.input_tokens` |  |
| `gen_ai.usage.output_tokens` |  |
| `gen_ai.usage.cache_read.input_tokens` |  |
| `gen_ai.usage.reasoning.output_tokens` |  |
| `hermes.cost.usage` |  |
| `hermes.turn.duration_s` |  |
| `error.type` |  |

### `hermes.tool.call`

Emitted from **`post_tool_call`**. Severity: INFO; WARN when blocked or timed out; ERROR on error. Body: `tool <name> <outcome>`.

| Attribute | Content-gated |
|---|---|
| `gen_ai.tool.name` |  |
| `gen_ai.tool.call.id` |  |
| `gen_ai.tool.type` |  |
| `hermes.tool.outcome` |  |
| `hermes.tool.decided_by` |  |
| `hermes.tool.duration_s` |  |
| `gen_ai.tool.call.arguments` | yes |
| `gen_ai.tool.call.result` | yes |

### `hermes.approval.decision`

Emitted from **`post_approval_response`**. Severity: INFO; WARN when denied or timed out. Body: `approval <choice> for <tool>`.

| Attribute | Content-gated |
|---|---|
| `hermes.approval.choice` |  |
| `hermes.approval.granted` |  |
| `hermes.approval.decided_by` |  |
| `hermes.approval.surface` |  |
| `hermes.approval.timed_out` |  |
| `hermes.approval.duration_ms` |  |
| `gen_ai.tool.name` |  |

### `hermes.api.error`

Emitted from **`api_request_error`**. Severity: ERROR; WARN when retryable. Body: `api error <error.type> from <provider>`.

| Attribute | Content-gated |
|---|---|
| `error.type` |  |
| `http.response.status_code` |  |
| `hermes.retryable` |  |
| `hermes.retry.count` |  |
| `gen_ai.request.model` |  |
| `gen_ai.provider.name` |  |
| `exception.type` |  |
| `exception.message` |  |

### `hermes.subagent.start`

Emitted from **`subagent_start`**. Severity: INFO. Body: `sub-agent <role> started`.

| Attribute | Content-gated |
|---|---|
| `hermes.subagent.role` |  |
| `hermes.subagent.child_session_id` |  |
| `hermes.subagent.parent_session_id` |  |
| `hermes.subagent.goal` | yes |

### `hermes.subagent.stop`

Emitted from **`subagent_stop`**. Severity: INFO; ERROR when the sub-agent reports failure. Body: `sub-agent <role> <status>`.

| Attribute | Content-gated |
|---|---|
| `hermes.subagent.role` |  |
| `hermes.subagent.status` |  |
| `hermes.subagent.duration_ms` |  |
| `hermes.subagent.child_session_id` |  |
| `hermes.subagent.parent_session_id` |  |
| `hermes.subagent.summary` | yes |

### `hermes.session.finalize`

Emitted from **`on_session_finalize / on_session_reset`**. Severity: INFO. Body: `session <id> <finalized|reset> (<reason>): <n> turn(s)`.

| Attribute | Content-gated |
|---|---|
| `hermes.session.turn_count` |  |
| `hermes.session.duration_s` |  |
| `hermes.session.finalize_reason / hermes.session.reset_reason` |  |

### `gen_ai.client.inference.operation.details`

Emitted from **`post_api_request`**. Severity: INFO. Body: `<operation> <model> (<input>/<output> tokens)`.

| Attribute | Content-gated |
|---|---|
| `gen_ai.operation.name` |  |
| `gen_ai.provider.name` |  |
| `gen_ai.request.model` |  |
| `gen_ai.response.model` |  |
| `gen_ai.response.id` |  |
| `gen_ai.response.finish_reasons` |  |
| `gen_ai.usage.input_tokens` |  |
| `gen_ai.usage.output_tokens` |  |
| `gen_ai.conversation.id` |  |
| `gen_ai.input.messages` | yes |
| `gen_ai.output.messages` | yes |
| `gen_ai.system_instructions` | yes |

[//]: # (generated:log-events:end)

## See also

- [Logs and events](/configuration/logs) — the pipeline, the `logs:` block, attribution and redaction.
- [Span attributes](/reference/span-attributes) — the names the events reuse.
- [Metrics](/reference/metrics) — the aggregates; an event is the per-occurrence record.
