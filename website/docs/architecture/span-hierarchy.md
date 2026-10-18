---
sidebar_position: 2
title: "Span hierarchy"
description: "What each span type represents, how they nest, and the attributes each one carries."
---

# Span hierarchy

Every Hermes turn produces a nested tree of spans. This page documents what each one is for and the data it carries.

## The tree

```text
agent / cron                                  [AGENT]
├── skill.{name}                              [SKILL] (load → turn end; overlaps OK)
└── llm.{model}                               [LLM]
    ├── api.{model}                           [LLM]
    │   ├── tool.{name}                       [TOOL]
    │   ├── tool.skill_view                   [TOOL] (the call that *loads* skill.{name})
    │   ├── tool.{name}                       [TOOL] (parallel tool calls — siblings)
    │   ├── approval.{pattern_key}            [APPROVAL] (human-in-the-loop wait; gen_ai.tool.call.id → the gated tool)
    │   ├── tool.delegate_task                [TOOL] (the tool call that delegates)
    │   └── subagent.{role}                   [AGENT] (delegation span)
    │       └── agent (child session root)    [AGENT] (the child rejoins this trace)
    │           └── llm.{model} → api.{model} → tool.{name} ...   (child's own work)
    └── api.{model}                           [LLM]  (second round-trip after tool results)
```

- **`agent` / `cron`** — the root for each turn. Present when session hooks are available in the Hermes build; absent on older versions (the `llm.*` span becomes the root).
- **`skill.*`** — one per skill loaded during the turn. Spans from the load (a `skill_view` call or a `/skills/` read) to the turn boundary, nested under the turn root. Skills overlap freely — two loaded in one turn are two concurrent siblings. See [`skill.*`](#skill) below.
- **`llm.*`** — one per logical model turn. Wraps one or more HTTP round-trips to the provider.
- **`api.*`** — one per HTTP round-trip. Tools run during a round-trip, so their parent is `api.*`, not `llm.*`.
- **`tool.*`** — one per tool invocation. Parallel tool calls are siblings under the same `api.*`.
- **`approval.*`** — one per dangerous-command approval prompt. Spans the time the agent blocks waiting for a human to approve/deny, correlated to the gated tool via `gen_ai.tool.call.id`. See [`approval.*`](#approval) below.
- **`subagent.*`** — one per delegated child agent. The child's own root span nests under it so a multi-agent run is one connected trace. See [`subagent.*`](#subagent) below.

Every family but the root can be switched off with [`disable_spans`](/configuration/yaml#disable) (#479). A disabled span is a non-recording stand-in carrying its parent's context, so the tree closes over it: with `llm` disabled the `api.*` spans parent to `agent`, with `api` disabled the `tool.*` spans parent to `llm.*`. The hooks still run and still record the family's metrics.

## `agent` / `cron`

The root span of every turn. It is named `agent`, or `cron` when the Hermes session kind is a cron job; the kind itself (`cli`, `telegram`, `discord`, …) is on `hermes.session.kind`.

**Span kind:** `AGENT` (OpenInference), `gen_ai.operation.name=invoke_agent`.

Key attributes, set at start:

| Attribute | Source |
|---|---|
| `openinference.project.name` | `OTEL_PROJECT_NAME` / `HERMES_OTEL_PROJECT_NAME` |
| `hermes.session.kind` | From Hermes (`cli`, `telegram`, `cron`, etc.) |
| `hermes.session.id` | Hermes session ID |
| `session.id` | Same as above, standard OTel naming |
| `user.id` | Hermes user ID (when available) |

Key attributes, set at end (the **turn summary**):

| Attribute | Type | Meaning |
|---|---|---|
| `hermes.turn.tool_count` | int | Distinct tool names invoked |
| `hermes.turn.tools` | string | Sorted CSV of distinct tool names (≤500 chars) |
| `hermes.turn.tool_targets` | string | `\|`-joined distinct file paths / URLs |
| `hermes.turn.tool_commands` | string | `\|`-joined distinct shell commands |
| `hermes.turn.tool_outcomes` | string | Sorted CSV of distinct outcome statuses |
| `hermes.turn.skill_count` | int | Distinct skills inferred |
| `hermes.turn.skills` | string | Sorted CSV of distinct skill names |
| `hermes.turn.api_call_count` | int | Number of `pre_api_request` hooks fired |
| `hermes.turn.final_status` | string | `completed` · `interrupted` · `incomplete` · `timed_out` |

See [Turn summary](/architecture/turn-summary) for why these exist.

## `llm.*`

One per logical model turn. Name is `llm.{model}` (e.g. `llm.claude-sonnet-4-6`).

**Span kind:** `LLM` (OpenInference).

| Attribute | Convention | Meaning |
|---|---|---|
| `llm.model_name` | OpenInference | Model name |
| `llm.provider` | OpenInference | The LLM provider reported by the turn's API calls (`openrouter`, `anthropic`); absent until one reports it |
| `input.value` | OpenInference | User message *or* full conversation history (see below) |
| `input.mime_type` | OpenInference | `text/plain` or `application/json` |
| `output.value` | OpenInference | Final assistant response |
| `output.mime_type` | OpenInference | `text/plain` |
| `gen_ai.request.model` | gen_ai | Model name (for Langfuse / SigNoz) |
| `gen_ai.content.prompt` | gen_ai | User message (same content as `input.value` when both are strings) |
| `gen_ai.content.completion` | gen_ai | Assistant response |
| `hermes.conversation.message_count` | hermes-specific | When `capture_conversation_history: true` |

By default `input.value` is the latest user turn only. To see the full message list the model saw, enable [conversation capture](/configuration/conversation-capture).

## `api.*`

One per HTTP round-trip to the provider. Name is `api.{model}`.

**Span kind:** `LLM` (OpenInference).

| Attribute | Convention | Meaning |
|---|---|---|
| `llm.token_count.prompt` | OpenInference | Prompt tokens |
| `llm.token_count.completion` | OpenInference | Completion tokens |
| `llm.token_count.total` | OpenInference | Sum of the above |
| `llm.token_count.cache_read` | OpenInference | Prompt tokens read from cache (if provider reports) |
| `llm.token_count.cache_write` | OpenInference | Prompt tokens written to cache (if provider reports) |
| `gen_ai.usage.input_tokens` | gen_ai | Prompt tokens (for Langfuse) |
| `gen_ai.usage.output_tokens` | gen_ai | Completion tokens (for Langfuse) |
| `gen_ai.usage.cache_read_input_tokens` | gen_ai | Cache read (if provider reports) |
| `gen_ai.usage.cache_creation_input_tokens` | gen_ai | Cache write (if provider reports) |
| `llm.invocation_parameters` | OpenInference | JSON of temperature, max_tokens, etc. |
| `gen_ai.response.finish_reason` | gen_ai | `stop`, `tool_use`, `length`, etc. |
| `http.duration_ms` | hermes-specific | Wall-clock duration of the HTTP call |

The `api.*` span is the right place to look for token counts — not the parent `llm.*` (which doesn't carry per-call counts, because a turn can have multiple `api.*` calls).

### Auxiliary calls {#auxiliary}

Hermes also calls models **on the agent's behalf**: session titling, context compression, the Mixture-of-Agents reference models and aggregator, vision, approval classification, the kanban decomposer, the `/goal` judge. Each physical attempt is an `api.{model}` span too, tagged `hermes.query_source=auxiliary` and `hermes.auxiliary.task=<task>`, nested under whatever the owning turn has in flight (so a MoA turn shows its fan-out: N `api.<reference>` spans and one `api.<aggregator>` under the turn's `llm.*`), or standing as its own root when no turn owns it (the kanban dispatcher decomposing a card). They are never pushed as parents. An attempt that starts after its turn's root already ended (Hermes's title generation can fire after `on_session_end`) stays its own root too, tagged `hermes.span.after_turn=true`, with a span link (`hermes.link=turn_root`) to the root it followed: parenting it under the ended root would show a child outliving its parent. Its usage is counted on no turn's roll-up.

```text
agent
└── llm.{model}
    ├── api.ref-a            hermes.query_source=auxiliary  hermes.auxiliary.task=moa_reference
    ├── api.ref-b            hermes.query_source=auxiliary  hermes.auxiliary.task=moa_reference
    ├── api.aggregator       hermes.query_source=auxiliary  hermes.auxiliary.task=moa_aggregator
    └── api.moa/preset       hermes.query_source=main       (what the MoA facade returned to the loop)
```

The turn root counts them as `hermes.turn.auxiliary.*` **beside** its `gen_ai.usage.*` totals, never inside them, because the main loop's `api.*` span for the `moa` provider already carries what the facade returned. Main-loop spans carry `hermes.query_source=main`, or `subagent` inside a delegated child's session.

## `tool.*`

One per tool invocation. Name is `tool.{name}` (e.g. `tool.bash`, `tool.read_file`).

**Span kind:** `TOOL` (OpenInference).

| Attribute | Convention | Meaning |
|---|---|---|
| `tool.name` | OpenInference | Tool name |
| `input.value` | OpenInference | Tool args (JSON) |
| `output.value` | OpenInference | Tool result |
| `hermes.tool.target` | hermes-specific | Inferred file path / URL (see [Tool identity](/architecture/tool-identity)) |
| `hermes.tool.command` | hermes-specific | Inferred shell command |
| `hermes.tool.outcome` | hermes-specific | `completed` · `error` · `timeout` · `blocked` |
| `hermes.skill.name` | hermes-specific | Skill inferred from args paths (optional) |

Errors: `hermes.tool.outcome=error` also maps the span's `StatusCode` to `ERROR`. Timeouts and blocked tools stay `OK` so dashboards don't count them as failures.

## `skill.*` {#skill}

One per skill the agent loads during a turn. A skill isn't a tool call — it's
loaded once (the agent calls `skill_view`, or reads a `/skills/<name>/` file)
and then *guides the rest of the turn*. So the span opens at the load and
closes at the **turn boundary** (`on_session_end`), nested under the turn's
`agent` root rather than the in-flight tool/LLM span. That makes "which skills
were active, and for how long" a visible part of the timeline.

Skills **overlap**: load two in one turn and you get two concurrent `skill.*`
siblings — loading a skill again in the same turn keeps the first window (no
duplicate span). Controlled by `skill_spans` (default on); the
`hermes.skill.name` attribute on the tool span and the `skill_inferred` counter
are emitted regardless.

| Attribute | Convention | Meaning |
|---|---|---|
| `hermes.skill.name` | hermes-specific | Skill name |
| `gen_ai.skill.name` | gen_ai (ext.) | Skill name (GenAI-convention alias) |
| `hermes.skill.source` | hermes-specific | `skill_view` (canonical) or `path_match` |
| `hermes.skill.path` | hermes-specific | The skill directory Hermes reported or the read file resolved to (optional) |
| `hermes.skill.result_status` | hermes-specific | Turn outcome: `completed` · `interrupted` · `incomplete` |
| `hermes.span_kind` | hermes-specific | `skill` (for UI grouping) |

Status is always `OK` — a skill being active is never itself an error; the
turn's outcome rides on `hermes.skill.result_status`.

## `approval.*` {#approval}

One per dangerous-command approval prompt. When a tool trips an approval rule,
the agent **blocks waiting for a human** to approve or deny — often the single
biggest chunk of a turn's wall-clock, and previously invisible. This span makes
that human-decision latency a first-class part of the trace, and records what
was decided (an audit signal for allowlist tuning).

The span opens on `pre_approval_request` and closes on `post_approval_response`,
nested within the turn (under the active `api.*`/`agent` span) and correlated to
the tool it gates via `gen_ai.tool.call.id`. It's keyed off the `turn_id` the
hook carries (which embeds the session id), so it lands in the right trace even
though the approval system runs on its own executor thread. Works on both the
CLI (`surface=cli`) and gateway surfaces (`surface=gateway` — Telegram, Discord,
Slack, …).

| Attribute | Convention | Meaning |
|---|---|---|
| `hermes.approval.pattern_key` | hermes-specific | The matched approval rule (e.g. `rm_rf`) — also the span name |
| `hermes.approval.choice` | hermes-specific | `once` · `session` · `always` · `deny` · `timeout` |
| `hermes.approval.granted` | hermes-specific | `true` for once/session/always |
| `hermes.approval.timed_out` | hermes-specific | `true` when the human never answered |
| `hermes.approval.duration_ms` | hermes-specific | Human-decision wait time (ms) |
| `hermes.approval.surface` | hermes-specific | `cli` or `gateway` |
| `hermes.approval.command` | hermes-specific | The command awaiting approval (preview-clipped; suppressed when `capture_previews=false`) |
| `hermes.approval.description` | hermes-specific | Why approval is required (preview-clipped) |
| `gen_ai.tool.call.id` | gen_ai | The gated tool call — correlates this approval to its `tool.*` span |

Status is always `OK` — a denial or timeout is a legitimate human outcome, not
an error. Metrics: `hermes.approval.count` (by `choice` / `pattern_key`) and
`hermes.approval.duration` (the wait histogram). These spans are observer-only —
the plugin never vetoes or alters an approval.

## `kanban.*` {#kanban}

Hermes' kanban board runs work in two processes: a **dispatcher** (inside the
gateway, or `hermes kanban dispatch`) claims a card and spawns a **worker**,
a fresh `hermes -p <assignee> chat` process that finishes the card with
`kanban_complete` or `kanban_block`. Every board transition is a short span,
and **one run of a card is one trace** across the two processes (#388):

```text
kanban.claim                       dispatcher process: the root of the run's trace
├── kanban.spawn                   dispatcher: the worker's pid
├── agent                          worker process (Resource + root: hermes.kanban.task_id / board / run_id)
│   └── llm.<model>
│       ├── api.<model>
│       ├── tool.kanban_show
│       ├── tool.kanban_complete
│       └── kanban.complete        outcome, summary
└── kanban.worker_exit             dispatcher, ERROR on crash / kill (or kanban.stale_claim)
```

**How the worker joins.** `kanban_task_claimed` fires in the dispatcher right
before it builds the worker's environment from the process environment, so the plugin
sets a W3C `TRACEPARENT` there (pointing at the `kanban.claim` span) and
removes it in `on_kanban_worker_spawned`. A worker (a process with
`HERMES_KANBAN_TASK`) whose environment carries a valid `TRACEPARENT`
parents its turn root under the claim. A `TRACEPARENT` anywhere else is left
alone, and the plugin never removes one it did not set.

Any other launcher can do the same with `HERMES_OTEL_TRACEPARENT` (and
`HERMES_OTEL_TRACESTATE`): a CI job, a shell script or another agent that
exports its own span's context before running `hermes` gets the turn root as
a child of that span, marked `hermes.trace.remote_parent=true` (#474). The
plugin-prefixed name never collides with a `TRACEPARENT` some unrelated tool
left in the shell; `remote_parent: false` switches it off.

The claim itself is a root: the gateway serves every session, so nesting it
under whatever turn happens to be in flight would mis-parent it. The later
dispatcher transitions (`kanban.spawn`, `kanban.worker_exit`,
`kanban.stale_claim`) join the run's trace while the same dispatcher process
remembers the claim; a one-shot `hermes kanban dispatch` pass that exited in
between starts a trace of its own for them. `hermes.kanban.task_id` and
`hermes.kanban.run_id` stay on every span (and on the worker's Resource) as
the join for those; one card attempted three times is three runs and three
traces. In the dashboard a worker's turn now shows as a `kanban.claim` card
holding the whole run, its totals summed from the run's `api.*` spans. Metrics:
`hermes.kanban.task.count{outcome, board}`. Attributes:
[span attributes](/reference/span-attributes#kanban).

## `subagent.*` {#subagent}

One per delegated child agent (when the parent calls the `delegate_task` tool). Name is `subagent.{role}` (e.g. `subagent.leaf`, `subagent.researcher`).

**Span kind:** `AGENT` (OpenInference).

The delegation span opens on `subagent_start` and nests under the parent turn's in-flight `api.*`/`llm.*` span. Crucially, the delegated child's **own root span rejoins this trace**: when the child runs in the same process (the default), its `agent` root nests directly under the `subagent.*` span, so a multi-agent run is a single connected tree instead of many disconnected traces.

**It outlives the turn that opened it.** Hermes runs a top-level `delegate_task` in the background: the tool returns a handle at once, the parent's turn ends, and `subagent_stop` fires later, often during a following turn. The delegation span stays open across those turn boundaries and ends on `subagent_stop` with the reported status, so in a trace view it extends past the end of the turn root it hangs from. The parent's orphan sweep does not close it. A delegation whose `subagent_stop` never arrives is closed after `subagent_span_ttl_ms` (default 24 hours) with `hermes.subagent.swept=true`, and at process exit.

**What the child cost.** On `subagent_stop` the delegation span gets the child's whole subtree: `hermes.subagent.usage.*`, `hermes.subagent.api_call_count` and `hermes.subagent.cost.*`, summed over the child's own turns and, for an orchestrator, every delegation it made in turn (each grandchild is counted once at every level above it). The parent's root keeps only the parent's own calls, so "what did the whole run cost" is the parent root plus its top-level `subagent.*` spans, never the parent root plus every span. `hermes.token.usage` / `hermes.cost.usage` carry `query_source=subagent` for a child's calls.

**Which model the child ran on.** `subagent_start` does not carry the child's model, but the child's own root does; when it rejoins, the delegation span gets `gen_ai.request.model` (the child's model) and `hermes.subagent.model_inherited`: `true` when the child runs on its parent's model, `false` when `delegation.model` pins it to another. Every turn root also carries `hermes.agent.model_override` when Hermes's config is readable: whether the turn's model differs from the profile's `model.default`, which is how a mixed-model fleet (a cheap worker model under a frontier orchestrator, kanban `--model`, a cron job's own model) shows up at the agent level. Group by `gen_ai.request.model` on `subagent.*` or on the roots to split spend per model.

Nested orchestrators (`delegation.max_spawn_depth` > 1) nest the same way one level down: a grandchild's `subagent.*` span hangs from the child's in-flight span, inside the child's root. Batch delegations (`independent_completions`, `group`) get one span per child, ended in whatever order their results arrive.

| Attribute | Convention | Meaning |
|---|---|---|
| `gen_ai.operation.name` | gen_ai | `invoke_agent` |
| `gen_ai.agent.name` | gen_ai | The child's role |
| `hermes.subagent.role` | hermes-specific | Child role (`leaf`, `orchestrator`, …) |
| `hermes.subagent.goal` | hermes-specific | What the child was asked to do (preview) |
| `hermes.subagent.child_session_id` | hermes-specific | The child's session ID (join key) |
| `hermes.subagent.parent_session_id` | hermes-specific | The delegating parent's session ID |
| `hermes.subagent.parent_turn_id` | hermes-specific | The parent turn that delegated |
| `hermes.subagent.child_id` | hermes-specific | The child's sub-agent ID |
| `hermes.subagent.status` | hermes-specific | Reported `child_status` (set on stop) |
| `hermes.subagent.duration_ms` | hermes-specific | Child wall-clock duration (set on stop) |
| `hermes.subagent.summary` | hermes-specific | The child's result summary (set on stop) |
| `hermes.subagent.swept` | hermes-specific | `true` when closed by `subagent_span_ttl_ms` instead of `subagent_stop` |

The child's `agent` root carries `hermes.session.is_subagent=true` plus `hermes.subagent.parent_session_id` / `hermes.subagent.role` so you can filter child runs even when looking at a single span.

**Status:** failure-like `child_status` values (`error`, `failed`, `cancelled`, `timeout`, `stalled`) map the span to `ERROR`. `interrupted` (the user stopped the parent) and `unknown` (the parent process restarted, so nothing is known) stay `OK`, as does a missing status. A `subagent_stop` that arrives with no open span (a restart) records the metrics and opens nothing.

**Metrics:** `hermes.subagent.count{role, status}` and `hermes.subagent.duration{role}`.

See [Hooks reference](/reference/hooks#subagent_start) for the rejoin mechanism and the cross-process span-link fallback.

## Sessions

A session is not a span. Every turn is its own trace, and the session is `session.id` / `gen_ai.conversation.id` on each span plus `hermes.turn.number` on the root. `on_session_finalize` closes anything still open, records `hermes.session.turns` and `hermes.session.duration`, and drops the session's state; `on_session_reset` gives the replacing session's first root a `hermes.session.previous_id` attribute and a span link to the old session's last root. See [Limitations](/reference/limitations#a-session-is-a-group-of-traces-not-one-trace) for why.

## Why this shape?

The tree mirrors the agent's execution structure:

- **One root per turn** so you can filter "one user question worth of work" in the backend UI.
- **`llm.*` as a logical parent of all `api.*`** because the conversation-with-the-model is one coherent thing even when it takes multiple HTTP calls.
- **`tool.*` under `api.*`** because tools run *between* rounds of model inference, within a specific HTTP response's tool_calls. The `api.*` parent makes that explicit.

See [Attribute conventions](/architecture/attributes) for the dual-convention mapping side-by-side.
