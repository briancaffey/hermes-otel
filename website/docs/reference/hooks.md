---
sidebar_position: 4
title: "Hooks reference"
description: "The Hermes lifecycle hooks this plugin subscribes to, and the span operation each performs."
---

# Hooks reference

hermes-otel subscribes to a set of Hermes lifecycle hooks. Six (`pre_tool_call` … `post_api_request`) are "always available" on any Hermes version with plugin support. The rest — `api_request_error`, the session hooks, the sub-agent delegation hooks, the approval hooks — are registered conditionally, so older Hermes builds simply register fewer; the startup banner reports the count (13 on Hermes 0.21). `mcp_request_headers` is implemented but waits on an upstream hook that no Hermes release fires (see below).

## Always available

### `pre_tool_call`

Fires just before Hermes runs a tool.

- **Span op:** `tracker.start("tool.{name}", parent=current_api or current_llm)`
- **Attributes set on start:** `tool.name`, `gen_ai.tool.call.id` (Hermes' `tool_call_id`, falling back to `task_id`), `input.value` (args JSON), `hermes.tool.target`, `hermes.tool.command`, `hermes.skill.name`
- **Side effects:** increments the session aggregator (`session_state`) for turn summary; orphan sweep runs first

### `post_tool_call`

Fires when the tool returns (success, error, timeout, block, or cancel).

- **Span op:** closes the `tool.*` span
- **Attributes set on end:** `output.value` (result), `hermes.tool.outcome` (a specific non-success hook `status` — `timeout` / `blocked` / `cancelled` — is authoritative; a coarse hook `error` yields to an explicit `blocked` / `timeout` / `cancelled` in the result's `status` field, so governance blocks are not counted as errors; otherwise the result's own `status`, else `completed`)
- **Span status:** `ERROR` if outcome is `error`, else `OK`
- **Skill spans:** when the call loaded a skill and succeeded (`skill_view` result `success: true`, or a `/skills/` file read that did not error), opens the `skill.<name>` span and adds the skill to `hermes.turn.skills`; failed loads do neither
- **Metrics:** `hermes.tool.duration{tool_name}` histogram (on end); `hermes.skill.inferred{skill_name, source}` counter (on start) — see [Metrics](/reference/metrics)

### `pre_llm_call`

Fires before the logical LLM turn starts (before any HTTP round-trips).

- **Span op:** `tracker.start("llm.{model}", parent=session_root)`
- **Attributes set on start:** `llm.model_name`, `llm.provider`, `gen_ai.request.model`, and (when `capture_conversation_history: true`) `input.value` JSON + `input.mime_type=application/json` + `hermes.conversation.message_count`
- **Side effects:** stores the current `llm.*` as the parent for subsequent `api.*` and `tool.*`

### `post_llm_call`

Fires after the logical LLM turn finishes (all round-trips done, final response received).

- **Span op:** closes the `llm.*` span
- **Attributes set on end:** `output.value` (final assistant response), `gen_ai.content.completion`

### `pre_api_request`

Fires before each HTTP request to the LLM provider. Can fire multiple times per `llm.*` turn.

- **Span op:** `tracker.start("api.{model}", parent=current_llm)`
- **Attributes set on start:** `llm.model_name`, `llm.provider`, `llm.invocation_parameters`
- **Side effects:** increments `hermes.turn.api_call_count`

### `post_api_request`

Fires when the HTTP response is parsed.

- **Span op:** closes the `api.*` span
- **Attributes set on end:** token counts (both conventions), `gen_ai.response.finish_reason`, `http.duration_ms`
- **Metrics:** `hermes.token.usage{token_type}`, `hermes.prompt_cache.tokens{cache_result}`, `hermes.prompt_cache.observations{cache_result}` (see [Metrics](/reference/metrics)), `hermes.cost.usage`, `hermes.model.usage` counters; `gen_ai.client.token.usage` and `gen_ai.client.operation.duration` histograms

### `api_request_error`

Fires when a provider API request **fails** (rate limit, timeout, 5xx, network error) instead of returning a response. Registered conditionally (newer Hermes).

- **Span op:** closes the in-flight `api.{model}` span (key `api:{task_id}`) as **ERROR** with a recorded `exception` event. Without this hook that span would be left to the orphan sweep and end `OK` — hiding the failure. If no in-flight span exists (error before `pre_api_request`, or already swept) a short-lived `api.error` span is created so the failure is still visible.
- **Attributes set on end:** `error.type`, `http.response.status_code` + `gen_ai.response.status_code`, `hermes.retry.count`, `hermes.max_retries`, `hermes.retryable`, `llm.response.duration_ms`
- **Span event:** `exception` (`exception.type`, `exception.message`, `exception.escaped`)
- **Metrics:** `hermes.api.error.count{error_type, status_class, retryable, model, provider}` counter; `hermes.retry.count{model, provider}` counter (once per *retryable* failure); `gen_ai.client.operation.duration` with `error.type`
- **Side effect:** records the `error.type` on the session aggregator so `on_session_end` can stamp it on the turn's root span

Only API-level failures become `ERROR`. Tool timeout/blocked outcomes deliberately stay `OK` (see [Limitations](/reference/limitations)) so they don't inflate error rates.

## Newer (session + sub-agent hooks)

Registered inside a `try:/except:` because older Hermes versions don't expose them. If any is missing, the plugin logs a debug message and degrades gracefully (older Hermes simply registers fewer hooks).

### `on_session_start`

Fires at the start of a user turn (CLI input, inbound message, cron wake-up).

- **Span op:** `tracker.start("agent")` (or `"cron"` for cron sessions), `parent=None` — this becomes the root of the trace
- **Attributes set on start:** `hermes.session.kind`, `hermes.session_id`, `session.id`, `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name`, the Weave turn markers; `user.id` / `hermes.sender.id` with `capture_sender_id`. See [Span attributes](/reference/span-attributes#agent--cron-turn-root)
- **Fallback if not available:** the `llm.*` span becomes the root; turn summary is attached there instead of on a dedicated session root

### `on_session_end`

Fires when the turn is fully complete (assistant has returned its final response, interrupted, or timed out). Despite the name it fires **once per turn**, not once per session; the session's true end is `on_session_finalize` below.

- **Span op:** closes the `agent` / `cron` root span
- **Attributes set on end:** the full [turn summary](/architecture/turn-summary) — `hermes.turn.tool_count`, `hermes.turn.tools`, `hermes.turn.tool_targets`, `hermes.turn.tool_commands`, `hermes.turn.tool_outcomes`, `hermes.turn.skill_count`, `hermes.turn.skills`, `hermes.turn.api_call_count`, `hermes.turn.final_status`
- **Metrics:** `hermes.session.count{platform}` counter (on start); `gen_ai.agent.token.usage` per-turn rollup (on end)
- **Side effects:** if `force_flush_on_session_end: true` (default), force-flushes every `BatchSpanProcessor` from a background thread (500 ms per backend, coalesced) so the trace appears in the backend UI promptly without the agent loop waiting on a collector

### `on_session_finalize`

Fires once, at the session's true end: CLI exit, `/new` or `/reset`, a gateway session expiring, a TUI session closing. Payload: `session_id`, `platform`, `reason` (`session_boundary`, `shutdown`, `new_session`, …).

- **Span op:** none of its own. A session is not a span: backends export a span only when it ends, and a gateway session can live for hours, so each turn stays its own trace and the session is the `session.id` / `gen_ai.conversation.id` attribute on every span. If a turn's `agent` / `cron` root is still open (a turn in flight, or an orphan), it is closed with `hermes.session.finalize_reason` and `hermes.turn.final_status=finalized`.
- **Metrics:** `hermes.session.turns{platform, reason}` histogram (turns the session had) and `hermes.session.duration{platform, reason}` histogram (seconds from its first turn); nothing is recorded for a session this process never saw a turn of, which happens when a gateway restarts before expiry fires.
- **Log:** one `INFO` line, `session <id> finalized (<reason>): N turn(s) over Ns`, with `hermes.session_id`, `hermes.session.turn_count`, `hermes.session.duration_s` and `hermes.session.finalize_reason` as attributes, exported when `capture_logs` is on.
- **Side effects:** every per-session record (aggregator, turn counter, parent stack, open skill spans, delegation record) is dropped, so a session id that comes back starts at turn 1; the span context of the session's last root is kept so a replacing session can link to it; every backend is force-flushed regardless of `force_flush_on_session_end`.

### `on_session_reset`

Fires when a new session replaces an old one (`/new`, `/reset`). The gateway passes `session_id` (the new one), `old_session_id`, `new_session_id`, `platform` and `reason`; the CLI passes only the new `session_id` after having fired `on_session_finalize` for the old one.

- **Span op:** the old session, when still known, is finalized as above with `hermes.session.reset_reason` on any open root. The new session's first root carries `hermes.session.previous_id` and an OTel span **link** (attribute `hermes.link=previous_session`) to the old session's last root, so `/new` chains are discoverable from either end in backends that render links.
- **Fallback:** with no old id and no previously finalized session in this process, only the new session is noted; nothing is emitted.

### `subagent_start`

Fires when a parent agent delegates work to a child agent (the `delegate_task` tool). Dispatched in the parent's process, on the parent thread.

- **Span op:** `tracker.start("subagent.{role}", parent=current_api or current_llm)` — a delegation span in the **parent's** trace
- **Attributes set on start:** `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name`, `hermes.subagent.role`, `hermes.subagent.child_session_id`, `hermes.subagent.parent_session_id`, `hermes.subagent.parent_turn_id`, `hermes.subagent.child_id`, `hermes.subagent.goal`, `input.value`
- **Side effects:** stashes the delegation span (and its `SpanContext`) keyed by `child_session_id` in the tracer's sub-agent registry so the child's own root span can rejoin this trace; registers the span with the orphan sweep under the parent session

### `subagent_stop`

Fires when a delegated child agent returns or fails.

- **Span op:** closes the `subagent.{role}` span
- **Attributes set on end:** `hermes.subagent.status`, `hermes.subagent.duration_ms`, `hermes.subagent.summary`, `output.value`
- **Span status:** `ERROR` for failure-like statuses (`error`, `failed`, `cancelled`, `timeout`); `OK` otherwise (an unknown/missing status never inflates error rates)
- **Metrics:** `hermes.subagent.count{role, status}` counter, `hermes.subagent.duration{role}` histogram
- **Side effects:** removes the child from the sub-agent registry

#### How the child rejoins the parent trace

When the delegated child runs **in the same process** (the default for `delegate_task`), `on_session_start` for the child finds the stashed delegation span and nests the child's root span directly under it — so the whole multi-agent run is **one connected trace**. When only a `SpanContext` is available (cross-process delegation), the child root attaches a span **link** to the delegation span instead and is tagged with `hermes.subagent.parent_session_id` for correlation. See [Limitations](/reference/limitations).

### `pre_approval_request`

Fires when a tool trips a dangerous-command approval rule and the agent blocks waiting on a human.

- **Span op:** opens an `approval.{pattern_key}` span (within the turn; correlated to the gated tool via `gen_ai.tool.call.id`)
- **Attributes set on start:** `hermes.approval.pattern_key`, `hermes.approval.surface`, `hermes.approval.command` / `hermes.approval.description` (preview-clipped), `gen_ai.tool.call.id`
- **Correlation:** keyed off `turn_id` (which embeds the session id), so it lands in the right trace even though approvals run on a separate executor thread
- **Observer-only:** the return value is ignored — the plugin **cannot veto or pre-answer** an approval (use `pre_tool_call` blocking for that)

### `post_approval_response`

Fires when the human answers (or the prompt times out).

- **Span op:** closes the `approval.{pattern_key}` span
- **Attributes set on end:** `hermes.approval.choice` (`once`/`session`/`always`/`deny`/`timeout`), `hermes.approval.granted`, `hermes.approval.timed_out`, `hermes.approval.duration_ms`
- **Span status:** always `OK` — a denial or timeout is a legitimate human decision, not an error
- **Metrics:** `hermes.approval.count{choice}` counter, `hermes.approval.duration{choice}` histogram

### `mcp_request_headers` (pending upstream)

Not a hook in any Hermes release (v0.21.3 has 39 hooks and no `mcp_request_headers`), so it is **not** declared in `plugin.yaml` and is not part of the "15 hooks" count. The plugin side is implemented: `register()` subscribes only when the running Hermes lists the hook in `hermes_cli.plugins.VALID_HOOKS`, and stays unregistered when that registry cannot be inspected, so the plugin catalog's declared-vs-registered check never sees an undeclared hook. The upstream proposal ([hermes-agent#52211](https://github.com/NousResearch/hermes-agent/issues/52211)) was closed because the MCP SDK 2.x propagates trace context in-protocol; the remaining gap is parenting on Hermes' MCP loop thread. See [MCP trace propagation](/configuration/mcp-trace-propagation) for the status.

## Hook → span mapping

```text
Hermes hook              OTel operation
──────────────────────   ─────────────────────────────────────
on_session_start         open  agent/cron        (or rejoin a delegation span as a child)
pre_llm_call             open  llm.{model}       (child of session)
pre_api_request          open  api.{model}       (child of llm)
pre_tool_call            open  tool.{name}       (child of api or llm)
subagent_start           open  subagent.{role}   (child of api or llm)
post_tool_call           close tool.{name}
post_api_request         close api.{model}        (OK)
api_request_error        close api.{model}        (ERROR + exception + retry attrs/metrics)
post_llm_call            close llm.{model}
subagent_stop            close subagent.{role}   + status + duration + metrics
pre_approval_request     open  approval.{pattern} (child of api/turn; → gated tool)
post_approval_response   close approval.{pattern} + choice + wait duration + metrics
on_session_end           close agent/cron        + turn summary + force-flush   (per turn)
on_session_finalize      close agent/cron if open + session turns/duration metrics + drop state + background flush
on_session_reset         finalize old session    + previous_id and link on the new session's first root
mcp_request_headers      (pending upstream; no span) would return traceparent/tracestate for the outbound MCP call
```

## Parallel tool calls

When the model emits multiple tool calls in a single response, Hermes fires `pre_tool_call` / `post_tool_call` for each in sequence (or in parallel, depending on the Hermes version). The plugin handles both: each tool span gets its own `span.start` / `span.end`, and they're all children of the same `api.*` parent.

See the `hermes_otel/hooks/` package for the callback implementations (one module per hook family) and the `SpanTracker` class for the parent-stack management.
