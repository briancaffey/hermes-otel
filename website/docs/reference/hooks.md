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

### `pre_auxiliary_call` / `post_auxiliary_call`

Fire once per physical provider attempt of every **auxiliary** LLM call Hermes makes on the agent's behalf: session titling, context compression, the Mixture-of-Agents reference models and aggregator, vision, approval classification, the kanban decomposer and triage specifier, the `/goal` judge, … (`agent/auxiliary_hooks.py` upstream). They carry the `pre_api_request` / `post_api_request` payload shape plus `aux_task` and the identity of the main turn they run under (`session_id` is empty for turn-less callers such as the kanban dispatcher). Registered conditionally (newer Hermes). Deliberately distinct events upstream: the main-loop `*_api_request` hooks never see this traffic, so without these two hooks every auxiliary call is invisible and the turn's spend is under-reported.

- **Span op:** `api.{model}` (span kind `LLM`), nested under whatever the owning turn has in flight (`llm.*`, `api.*` or the root); its own root when there is no turn. Never pushed as a parent: nothing runs inside an auxiliary attempt (the MoA aggregator's tool calls belong to the main loop's `api.*`).
- **Attributes:** `hermes.query_source=auxiliary`, `hermes.auxiliary.task` (`title_generation`, `compression`, `moa_reference`, `moa_aggregator`, `vision`, `approval`, `kanban_decomposer`, `triage_specifier`, `goal_judge`, …), then the same model, provider, request, token, cost and duration attributes as a main-loop `api.*` span; `error.type` + an `exception` span event and status `ERROR` when the attempt failed; `gen_ai.request.stream=true` and no usage for a streamed attempt (Hermes hands the stream back unconsumed).
- **Turn roll-up:** counted on the owning turn's root as `hermes.turn.auxiliary.*` (call count, error count, tokens, cost), **beside** the turn totals, never inside them: the main loop's `api.*` span for the `moa` provider already carries what the MoA facade returns, so folding the attempts in would count them twice. The dashboard's trace totals skip auxiliary spans for the same reason.
- **Metrics:** `hermes.auxiliary.count{aux_task, status, model, provider}`; the usual `hermes.token.usage`, `hermes.cost.usage`, `hermes.model.usage` with `query_source=auxiliary` (main-loop calls carry `main` or `subagent`); `gen_ai.client.token.usage` and `gen_ai.client.operation.duration`.
- **Event:** `hermes.auxiliary.call` (`ERROR` on failure), see [log events](/reference/log-events).

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

Not a hook in any Hermes release (v0.21.3 has 39 hooks and no `mcp_request_headers`), so it is **not** declared in `plugin.yaml` and is not part of the "23 hooks" count. The plugin side is implemented: `register()` subscribes only when the running Hermes lists the hook in `hermes_cli.plugins.VALID_HOOKS`, and stays unregistered when that registry cannot be inspected, so the plugin catalog's declared-vs-registered check never sees an undeclared hook. The upstream proposal ([hermes-agent#52211](https://github.com/NousResearch/hermes-agent/issues/52211)) was closed because the MCP SDK 2.x propagates trace context in-protocol; the remaining gap is parenting on Hermes' MCP loop thread. See [MCP trace propagation](/configuration/mcp-trace-propagation) for the status.

## Observers on transform hooks

Hermes' `transform_*` hooks let a plugin *replace* content (the first non-empty string returned wins). hermes-otel subscribes to two of them as pure observers: every handler returns `None`, so content is never changed, and only sizes and metadata are recorded. Every callback of a transform hook receives the same original value, so a replacement made by another plugin is invisible to an observer; what these make visible is Hermes' own truncation.

### `transform_terminal_output`

Fires inside the terminal tool with the output as the tool collected it, before Hermes applies its head+tail output limit, whitespace strip and redaction. (A very large output has already been cut to the capture window by then; Hermes records that cut in the result, and `post_tool_call` reads it.)

- **Span op:** sets attributes on the open `tool.terminal` span
- **Attributes:** `hermes.terminal.output.chars` (raw size), `hermes.terminal.returncode`, `hermes.terminal.env_type`
- **Then, on `post_tool_call`:** `hermes.terminal.output.truncated` and `hermes.terminal.output.dropped_chars` report whether Hermes' output limit cut the output and by how much, read from Hermes' own truncation markers in the result; `hermes.tool.result.chars` records the result size for every tool
- **Returns:** always `None`

### `transform_llm_output`

Fires in the turn finalizer with the final response text.

- **Span op:** sets `hermes.llm.output.chars` on the turn root (`agent` / `cron`); the `llm.*` span is already closed by then
- **Returns:** always `None`

`transform_tool_result` is not subscribed: it fires after `post_tool_call` with the same result the plugin already saw there, so there is nothing new to observe and no reason to register a third behaviour-changing hook.

## Kanban board transitions

Hermes' kanban is a durable task board: a dispatcher (inside the gateway, or `hermes kanban dispatch`) claims a ready card and spawns a worker, a fresh `hermes -p <assignee> chat` process that finishes the card with `kanban_complete` or `kanban_block`. Six observer hooks fire around that lifecycle, in two processes, and the plugin turns each into one short `kanban.*` span, a `hermes.kanban.transition` event and a point on `hermes.kanban.task.count`. No span is shared across the processes; `hermes.kanban.task_id` and `hermes.kanban.run_id` on every span (and on the worker's Resource, see [resource attributes](/reference/span-attributes#resource-on-every-span)) are the join. All six return `None`; Hermes ignores their return value and every fire site is best-effort.

### `kanban_task_claimed`

Fires in the **dispatcher** process right before the worker is spawned, inside the board's dispatch lock.

- **Span op:** `kanban.claim`, opened and closed at once, the root of the run's trace (the dispatcher serves every session, so it never nests under whatever turn is in flight). The plugin sets `TRACEPARENT` to this span in the dispatcher's environment so the worker's turn joins the trace, and removes it on `on_kanban_worker_spawned` (#388)
- **Attributes:** `hermes.kanban.task_id`, `hermes.kanban.board`, `hermes.kanban.assignee`, `hermes.kanban.run_id`, `hermes.kanban.event=claimed`
- **Metrics:** `hermes.kanban.task.count{outcome=claimed, board}`

### `on_kanban_worker_spawned`

Fires in the **dispatcher** after the worker's PID is persisted.

- **Span op:** `kanban.spawn`, a root of its own
- **Attributes:** the card identity plus `hermes.kanban.worker_pid`; `workspace_path` is deliberately not recorded (a filesystem path)

### `on_kanban_worker_exited`

Fires in the **dispatcher** when a tick finds a worker's process gone and reclaims its run.

- **Span op:** `kanban.worker_exit`, a root of its own
- **Attributes:** the card identity plus `hermes.kanban.exit_kind` (`clean_exit` · `rate_limited` · `nonzero_exit` · `signaled` · `unknown`), `hermes.kanban.exit_code`, `hermes.kanban.outcome`, `hermes.kanban.retry_status`
- **Span status:** `ERROR` for `nonzero_exit` and `signaled` (a crash, or a kill such as the dispatcher's own runtime-cap SIGTERM); `OK` otherwise

### `on_kanban_worker_stale_claim`

Fires in the **dispatcher** when a claim's TTL expired with no heartbeat.

- **Span op:** `kanban.stale_claim`, a root of its own, status `OK`
- **Attributes:** the card identity plus `hermes.kanban.worker_pid`, `hermes.kanban.heartbeat_stale`, `hermes.kanban.retry_status`

### `on_kanban_dispatch_tick` / `on_kanban_task_updated`

Board health (#397), metrics only.

- **`on_kanban_dispatch_tick`:** fires once per dispatcher pass, after the dispatch lock is released. It records `hermes.kanban.dispatch.count{outcome, board}`, plus `hermes.kanban.dispatch.result{kind, board}` for each non-zero `DispatchResult` field (spawned, reclaimed, crashed, auto_blocked, …). The task ids, assignees and workspace paths in the result are never exported, only how many there were. Dry runs are not counted. There is no span: an idle board ticks every minute, and what a busy pass did is already on the run's `kanban.*` spans.
- **`on_kanban_task_updated`:** a task-row edit outside claim / complete / block. It records `hermes.kanban.task.updated{field, board}` once per changed field *name*. Hermes never passes the values.

### `kanban_task_completed`

Fires in the **worker** from inside the `kanban_complete` tool (or in a CLI process when a human runs `hermes kanban complete`).

- **Span op:** `kanban.complete`, in the worker's own trace under its in-flight `llm` span (or turn root); a root of its own when no turn is in flight
- **Attributes:** the card identity, `hermes.kanban.outcome=completed`, `hermes.kanban.summary` (preview-clipped; suppressed when `capture_previews=false`)
- **Also:** the worker's `agent` root is stamped with `hermes.kanban.task_id`, `board`, `run_id` and `outcome`, so span-only readers (the bundled dashboard's live store) can find the card's turn

### `kanban_task_blocked`

Fires in the **worker** from inside `kanban_block` (or in the dispatcher when its breaker blocks a card, or on the CLI).

- **Span op:** `kanban.block`, placed like `kanban.complete`
- **Attributes:** the card identity, `hermes.kanban.outcome=blocked`, `hermes.kanban.reason` (preview-clipped)
- **Span status:** always `OK`; a blocked card is waiting for a human, not an error. The event is emitted at WARN

Two dispatcher outcomes fire no hook at all in Hermes: a worker killed by the card's runtime cap closes its run as `timed_out`, and the breaker's auto-block writes the card's status directly, so neither produces a `kanban.*` span (the worker's own trace ends interrupted, and the card's `task_events` carry the record). `on_kanban_worker_exited` covers a worker that died on its own.

`on_kanban_task_updated` and `on_kanban_dispatch_tick` are not subscribed: field-name-only updates and one event per 60-second tick would add noise and nothing a trace needs.

## Hook → span mapping

```text
Hermes hook              OTel operation
──────────────────────   ─────────────────────────────────────
on_session_start         open  agent/cron        (or rejoin a delegation span as a child)
pre_llm_call             open  llm.{model}       (child of session)
pre_api_request          open  api.{model}       (child of llm)
pre_tool_call            open  tool.{name}       (child of api or llm)
subagent_start           open  subagent.{role}   (child of api or llm)
transform_terminal_output  attrs tool.terminal   (raw output size, return code, env type; returns None)
post_tool_call           close tool.{name}       + result size, terminal truncation
post_api_request         close api.{model}        (OK)
api_request_error        close api.{model}        (ERROR + exception + retry attrs/metrics)
pre_auxiliary_call       open  api.{model}       (auxiliary: child of the turn's llm/api/root, or own root; not a parent)
post_auxiliary_call      close api.{model}       (auxiliary: usage, cost, ERROR on failure; turn roll-up hermes.turn.auxiliary.*)
post_llm_call            close llm.{model}
transform_llm_output     attr  agent/cron        (final response size; returns None)
kanban_task_claimed      open+close kanban.claim        (dispatcher; the run's root; sets TRACEPARENT for the worker)
on_kanban_worker_spawned open+close kanban.spawn        (dispatcher; under the claim; clears TRACEPARENT)
on_kanban_worker_exited  open+close kanban.worker_exit  (dispatcher; ERROR on crash/kill)
on_kanban_worker_stale_claim open+close kanban.stale_claim (dispatcher)
on_kanban_dispatch_tick  metrics only: hermes.kanban.dispatch.count / .result (dispatcher, after the lock)
on_kanban_task_updated   metrics only: hermes.kanban.task.updated{field}
kanban_task_completed    open+close kanban.complete     (worker; in the turn's trace + root stamped)
kanban_task_blocked      open+close kanban.block        (worker; OK + outcome=blocked)
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
