---
sidebar_position: 3
title: "Span attribute reference"
description: "Every attribute the plugin sets, by span type."
---

# Span attribute reference

Every attribute the plugin may set, grouped by span type. See [Attribute conventions](/architecture/attributes) for the narrative version of the dual-convention mapping (OpenInference `llm.*` / `input.value` for Phoenix **and** OTel GenAI `gen_ai.*` for Langfuse, Weave and generic dashboards).

Attributes marked **optional** are only set when the underlying data is available. Content is governed by `content_capture`: `full` (the default) puts the complete prompt and response on `api.*` spans; previews elsewhere (`input.value`, `output.value`, `gen_ai.*.messages`, tool args/results) are clipped to `preview_max_chars` (or the per-category cap) and carry a `hermes.preview.*` marker when clipped; `off` records no content at all.

The tables are generated from the registry in `hermes_otel/semconv.py` by `scripts/gen_semconv.py` (`make ci-semconv` fails when they drift); the **Convention** column says whose vocabulary a name belongs to, `both` when one value is spelled for OpenInference and for the GenAI readers. A test (`tests/unit/test_span_attributes_docs.py`) fails if the code sets an attribute this page does not list, or this page lists one the code never sets.

## Resource (on every span)

[//]: # (generated:resource:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `service.name` | OTel | string | `hermes-agent`; the named profile (`coder`) with `service_name_from_profile: true`. `resource_attributes:` overrides either |
| `service.namespace` | OTel | string | `hermes-agent`, only with `service_name_from_profile: true` and a named profile active (see [Profiles](/configuration/profiles#one-service-per-profile)) |
| `service.instance.id` | OTel | string | UUID generated once per process — keeps two Hermes processes on different metric series |
| `service.version` | OTel | string | Plugin version, read from the shipped `plugin.yaml` |
| `process.pid` | OTel | int | Process id |
| `os.type` | OTel | string | Operating system in the OTel spelling (`darwin`, `linux`, `windows`), from `platform.system()`, so a fleet can be split by platform (#476) |
| `host.arch` | OTel | string | CPU architecture in the OTel spelling (`arm64`, `amd64`, `x86` …), from `platform.machine()` (#476) |
| `openinference.project.name` | OpenInference | string | `project_name` / `OTEL_PROJECT_NAME` (the Phoenix project); optional |
| `hermes.kanban.task_id`, `hermes.kanban.board`, `hermes.kanban.run_id` | hermes | string | The kanban card, its board and the attempt a dispatcher-spawned worker is running, from `HERMES_KANBAN_TASK` / `_BOARD` / `_RUN_ID`. Only when the task is set: Hermes pins the board into every chat's environment, so the board alone never marks a worker |
| `host.name` | OTel | string | Hostname, only when `host_metrics: true` |
| `wandb.entity`, `wandb.project` | Weave | string | W&B Weave routing (when configured) |
| `telemetry.sdk.name`, `telemetry.sdk.version`, `telemetry.sdk.language` | OTel | string | Set by the OTel SDK |

[//]: # (generated:resource:end)

Any `resource_attributes.*` / `global_tags.*` key from the config file is added too; `resource_attributes` wins on key conflict.

Every `common_attributes.*` key is set on every span as a plain span attribute (not on the Resource) by a `SpanProcessor` at span start, under keys the span does not already carry; see [Which attributes reach which signal](/configuration/yaml#which-attributes-reach-which-signal).

## Every span

[//]: # (generated:every-span:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `openinference.span.kind` | OpenInference | string | `AGENT` (root, subagent) · `LLM` (`llm.*`, `api.*`) · `TOOL` (`tool.*`) · `GENERAL` (skill, approval, human_input, kanban) |
| `traceloop.span.kind` | Traceloop | string | The same span kinds in Traceloop's lowercase vocabulary: `agent` (root, subagent) · `tool` (`tool.*`) · `task` (everything else; Traceloop has no LLM kind, model spans are identified by `gen_ai.*`) · `unknown`. Filter on this from openllmetry-aware tools such as `opentelemetry-mcp` (since 1.26.0; earlier releases wrote the OpenInference spelling here, #382) |
| `hermes.turn.number` | hermes | int | 1-based index of the user prompt within the session. A session resumed in a new process (`hermes -r`) continues from the user prompts in its conversation; after a context compaction the count can only stay or grow. On the root and on every `llm.*` / `api.*` / `tool.*` span of the turn |
| `session.id`, `session_id` | OTel / hermes | string | Hermes session id, on the root and on `llm.*` / `api.*` / `tool.*` / `subagent.*`; `session_id` is the plugin's oldest spelling of the same value, kept for dashboards built on it |
| `gen_ai.conversation.id` | gen_ai | string | Same id, gen_ai spelling, wherever `gen_ai.operation.name` is set |
| `correlation.id` | hermes | string | A correlation id the host passed on the hook (`correlation_id` / `correlation.id` / `x-correlation-id` …), reused on every span of that session. Hermes 0.21 passes none, so the attribute is absent; group by `session.id` instead |
| `hermes.span.ended_by`, `hermes.span.sweep_reason` | hermes | string | On a child span (`tool.*` / `api.*` / `llm.*` / `approval.*` / `skill.*`) the plugin ended on the operation's behalf because the operation's own end hook never came: `ended_by` is `sweep` and `sweep_reason` says why, `turn_end` (still open at `on_session_end`) · `session_end` (at finalize / reset) · `turn_failed` (every API attempt of the turn failed and the next turn closed it) · `ttl` (the [orphan sweep](/architecture/orphan-sweep), `root_span_ttl_ms`) · `shutdown`. Absent on a normally ended span (#477) |
| `user.id`, `hermes.sender.id` | OTel / hermes | string | Gateway sender identity — only with `capture_sender_id: true`. `user.id` is `platform:sender` |

[//]: # (generated:every-span:end)

## `agent` / `cron` (turn root)

The root span is named `agent`, or `cron` when the session kind is a cron job. Set at **start**:

[//]: # (generated:root-start:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `hermes.session.kind` | hermes | string | `session` · `cron` (from Hermes' `session_type` / `origin` / `run_type` when a host passes one; Hermes 0.21 passes none, so `cron` comes from a cron platform and everything else is `session`) |
| `hermes.session_id` | hermes | string | Session id (pre-existing spelling; `session.id` is the standard one) |
| `llm.model_name` | OpenInference | string | Model the turn started with |
| `hermes.platform` | hermes | string | The Hermes surface the turn ran on: `cli` · `telegram` · `discord` · `cron` · … (never reported as a provider) |
| `hermes.profile` | hermes | string | The Hermes profile that ran the turn: `default`, the profile id under `~/.hermes/profiles/`, or `custom`. Also a resource attribute and the `profile` label on every metric ([profiles](/configuration/profiles)) |
| `hermes.llm.output.chars` | hermes | int | Size of the turn's final response text, observed on `transform_llm_output` ([hooks](/reference/hooks#transform_llm_output)); absent when the finalizer did not run (optional) |
| `llm.provider`, `gen_ai.provider.name`, `gen_ai.system` | both | string | The LLM provider the turn's API calls reported, set at **end**; absent when no API call reported one. Hermes 0.21 passes no provider on `on_session_start`. Each carries its convention's well-known spelling when there is one for the Hermes id (`gen_ai.*`: `x_ai`, `aws.bedrock`, `gcp.vertex_ai`, `gcp.gemini`, `azure.ai.inference`, `openai` for `openai-api` / `openai-codex`; `llm.provider`: `xai`, `aws`, `google`, `azure`, `moonshot`, `minimax`), else the raw id (`openrouter`, `nvidia`, `custom` …) (#475) |
| `hermes.provider` | hermes | string | The provider id exactly as Hermes names it (`xai`, `bedrock`, `openai-api`, `openrouter` …), beside the convention spellings in `gen_ai.provider.name` / `llm.provider`, wherever those are set (#475) |
| `gen_ai.request.model` | gen_ai | string | Model name |
| `hermes.turn.stop_reason` | hermes | string | Set when `agent_loop_stopped` fires for the turn (`/stop`, the `/new` fast path): the reason Hermes gives; a stopped turn whose end never arrives is swept as `interrupted`, not `timed_out` |
| `hermes.turn.verify_nudges` | hermes | int | How many times `pre_verify` fired this turn (the agent edited code and was about to finish); never the paths or the response |
| `hermes.turn.trigger` | hermes | string | What started the turn: `user` (CLI, TUI, desktop, a gateway platform, ACP), `cron`, `kanban` (a worker), `subagent` (a delegated child), `a2a`, `api` (the API server), `oneshot` (`hermes -z`, `chat -q`), `curator`, `batch` or `system`. `/loop`, `/heartbeat` and `/goal` re-entries and bot-to-bot DMs are not observable at turn start and classify as the turn they ride on (#444) |
| `hermes.agent.model_override` | hermes | bool | Whether the turn runs on a model other than the profile's `model.default` (a `delegation.model` child, a kanban `--model` worker, a cron job's own model, `-m`, `/model`); absent when either is unknown |
| `gen_ai.operation.name` | gen_ai | string | `invoke_agent` |
| `gen_ai.agent.name` | gen_ai | string | The host's `agent_name` when it passes one, else the active named profile (`coder`), else `hermes-agent`; Weave shows it as the agent |
| `wandb.is_turn`, `wandb.thread_id` | Weave | bool / string | Marks a Weave conversation turn and groups turns by session |
| `weave.agent.version` | Weave | string | Plugin version (optional) |
| `hermes.trace.remote_parent` | hermes | bool | `true` when the root was parented under the span an outer process named in `HERMES_OTEL_TRACEPARENT` ([env vars](/reference/env-vars#join-an-outer-trace), #474); absent otherwise |
| `hermes.session.synthesized` | hermes | bool | `true` on continuation turns: Hermes fires `on_session_start` only for a session's first turn, so later turns open their root lazily at the first hook of the turn (optional) |
| `hermes.session.previous_id` | hermes | string | The session this one replaced (`/new`, `/reset`); the root also carries a span link (`hermes.link=previous_session`) to that session's last root (optional) |
| `hermes.session.finalize_reason`, `hermes.session.reset_reason` | hermes | string | Set when a still-open root was closed by `on_session_finalize` / `on_session_reset`, with `hermes.turn.final_status` `finalized` / `reset` (optional) |
| `hermes.link` | hermes | string | On the span **link** from a replacing session's first root to the old session's last root: `previous_session` |
| `hermes.session_id`, `hermes.session.turn_count`, `hermes.session.duration_s`, `hermes.session.finalize_reason`, `hermes.session.reset_reason` | hermes | mixed | On the **log record** `on_session_finalize` / `on_session_reset` emit (exported with `capture_logs`), not on a span |
| `hermes.cron.job_id` | hermes | string | Cron job id, `cron` roots only: from the hook payload, else parsed from the scheduler's `cron_<job id>_<timestamp>` session id (optional) |
| `hermes.session.is_subagent` | hermes | bool | `true` on a delegated child's own root (optional) |
| `hermes.subagent.role`, `hermes.subagent.parent_session_id` | hermes | string | On a delegated child's root (optional) |

[//]: # (generated:root-start:end)

Set at **end** (turn summary; empty/zero aggregators are omitted):

[//]: # (generated:root-end:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `hermes.session.completed`, `hermes.session.interrupted` | hermes | bool | Hook payload flags |
| `hermes.turn.final_status` | hermes | string | `completed` · `interrupted` · `failed` · `incomplete` · `timed_out` · `finalized` · `reset` — from Hermes' `completed` / `interrupted` / `failed` flags (`timed_out` from the orphan sweep; `finalized` / `reset` when a session boundary closed the root; `failed` also when the turn's last API attempt failed and Hermes never ended it, #467) |
| `hermes.session.failed` | hermes | bool | Hermes' `failed` flag on `on_session_end` |
| `hermes.turn.exit_reason` | hermes | string | Hermes' `turn_exit_reason` (`interrupted_by_user`, `guardrail_halt`, `context_compression_timeout`, `max_iterations_reached(n/m)` …), when reported |
| `hermes.turn.tool_count`, `hermes.turn.tools` | hermes | int / string | Distinct tool names (sorted CSV, ≤500 chars) |
| `hermes.turn.tool_targets`, `hermes.turn.tool_commands` | hermes | string | `\|`-joined distinct paths/URLs and shell commands |
| `hermes.turn.tool_outcomes` | hermes | string | Sorted CSV of distinct outcomes |
| `hermes.turn.skill_count`, `hermes.turn.skills` | hermes | int / string | Skills that loaded successfully this turn |
| `hermes.turn.api_call_count` | hermes | int | `pre_api_request` hooks fired |
| `hermes.turn.auxiliary.api_call_count`, `hermes.turn.auxiliary.error_count` | hermes | int | Auxiliary LLM attempts that ran under this turn (titling, compression, MoA, vision, …) and how many failed (optional) |
| `hermes.turn.auxiliary.input_tokens`, `hermes.turn.auxiliary.output_tokens` | hermes | int | Their token sum, reported **beside** `gen_ai.usage.*`, never inside it: the `moa` provider's main-loop `api.*` span already carries what the MoA facade returns (optional) |
| `hermes.turn.auxiliary.cost.usage`, `hermes.turn.auxiliary.cost.status` | hermes | float / string | Their USD sum, with the same `actual` · `estimated` · `partial` · `included` · `unknown` rule as `hermes.cost.status` (optional) |
| `hermes.cost.usage` | hermes | float | The turn's USD cost: the sum of its API calls' `hermes.cost.usage`, set **only when every call was priced** (optional) |
| `hermes.cost.status` | hermes | string | `actual` (every call provider-reported) · `estimated` · `partial` (some calls priced, some not: no total) · `included` (subscription route) · `unknown` (no price for the model); absent when no call reported usage |
| `gen_ai.response.model` | gen_ai | string | Model at turn end |
| `error.type` | OTel | string | Most recent provider error class this turn (optional) |
| `input.value`, `output.value` | OpenInference | string | First user message / final assistant response of the turn (previews; optional) |

[//]: # (generated:root-end:end)

The root also carries the turn's token roll-up under the `api.*` spans' keys (`gen_ai.usage.*`, `llm.token_count.*`): the sum of its `api.*` spans (optional). It is withheld from a backend whose entry has `root_usage: false`, the preset for `opik` and `langwatch`, which sum usage over every span and would double the trace total ([#327](https://github.com/briancaffey/hermes-otel/issues/327)); the `api.*` spans and `hermes.cost.*` are never touched.

## `llm.*`

One per `run_conversation` call (span kind `LLM`).

[//]: # (generated:llm:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `llm.model_name` | OpenInference | string | Model |
| `llm.provider`, `gen_ai.provider.name`, `gen_ai.system` | both | string | The LLM provider reported by this turn's API calls, in each convention's spelling (see the turn root); absent on the first call before any API request reported one |
| `gen_ai.request.model` | gen_ai | string | Model name |
| `gen_ai.operation.name` | gen_ai | string | `chat` |
| `input.value`, `input.mime_type` | OpenInference | string | User message (`text/plain`) or, with `capture_conversation_history`, the conversation JSON (`application/json`) |
| `gen_ai.input.messages` | gen_ai | string (JSON) | Same content in the gen_ai message shape |
| `output.value`, `output.mime_type` | OpenInference | string | Final assistant response |
| `hermes.preview.input.truncated`, `hermes.preview.input.original_chars`, `hermes.preview.output.truncated`, `hermes.preview.output.original_chars` | hermes | bool, int | Set only when the preview was clipped: `true` and the value's original length (optional) |
| `gen_ai.output.messages` | gen_ai | string (JSON) | Same in the gen_ai shape |
| `gen_ai.response.model` | gen_ai | string | The response model a provider reported on this turn's API calls; absent when none was reported (never the request model) |
| `hermes.conversation.message_count` | hermes | int | Message count when conversation capture is on (optional) |

[//]: # (generated:llm:end)

## `api.*`

One per HTTP round-trip to the provider (span kind `LLM`). Set at **start**:

[//]: # (generated:api-start:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `llm.model_name`, `llm.provider` | OpenInference | string | Model / provider |
| `gen_ai.request.model`, `gen_ai.system`, `gen_ai.provider.name` | gen_ai | string | Model / provider (`gen_ai.system` is the legacy spelling Langfuse reads); the provider in the GenAI well-known spelling when there is one, else the Hermes id, which `hermes.provider` always carries (#475) |
| `gen_ai.operation.name` | gen_ai | string | `chat` |
| `hermes.query_source` | hermes | string | Which loop the call belongs to: `main` (the agent loop), `subagent` (a delegated child's own loop), `auxiliary` (a call Hermes made on the agent's behalf: titling, compression, MoA reference models and aggregator, vision, approval, the kanban decomposer, the `/goal` judge, …; [hooks](/reference/hooks#pre_auxiliary_call--post_auxiliary_call)) |
| `hermes.auxiliary.task` | hermes | string | The auxiliary task, as Hermes names it (`title_generation`, `compression`, `moa_reference`, `moa_aggregator`, `vision`, `approval`, `kanban_decomposer`, `triage_specifier`, `goal_judge`, …); `unknown` when it sent none. Auxiliary spans only |
| `llm.api_mode` | hermes | string | `chat_completions` · `anthropic_messages` · `codex_responses` · … |
| `llm.request.message_count`, `llm.request.approx_input_tokens`, `llm.request.max_tokens` | hermes | int | Request shape as Hermes reports it |
| `llm.request.tool_count` | hermes | int | Tools offered on an auxiliary call, when any (optional; auxiliary spans only) |
| `gen_ai.request.max_tokens`, `gen_ai.request.temperature`, `gen_ai.request.top_p`, `gen_ai.request.top_k`, `gen_ai.request.frequency_penalty`, `gen_ai.request.presence_penalty`, `gen_ai.request.stream`, `gen_ai.request.reasoning.level`, `gen_ai.request.stop_sequences`, `gen_ai.request.choice.count` | gen_ai | mixed | Request parameters, each only when Hermes passes it (optional) |
| `input.value`, `input.mime_type`, `gen_ai.input.messages` | both | string (JSON) | **Full** request messages, one copy per convention — `content_capture: full` (the default) |
| `gen_ai.system_instructions` | gen_ai | string | **Full** system prompt: the leading system message, or the Responses API `instructions` — `content_capture: full` |
| `hermes.content.input_chars` | hermes | int | Size of the captured request JSON — `content_capture: full` |

[//]: # (generated:api-start:end)

Set at **end** (success):

[//]: # (generated:api-end:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total` | OpenInference | int | Whole prompt (incl. cache reads/writes), completion, sum |
| `llm.token_count.prompt_details.cache_read`, `llm.token_count.prompt_details.cache_write` | OpenInference | int | Cache buckets (optional) |
| `llm.token_count.completion_details.reasoning` | OpenInference | int | Reasoning tokens, a subset of completion (optional) |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.total_tokens` | gen_ai | int | Same three totals |
| `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens` | gen_ai | int | Cache buckets, current spelling (optional) |
| `gen_ai.usage.cache_read_input_tokens`, `gen_ai.usage.cache_creation_input_tokens` | gen_ai | int | Same values, pre-existing alias kept for older dashboards (optional) |
| `gen_ai.usage.reasoning.output_tokens` | gen_ai | int | Reasoning tokens (optional) |
| `gen_ai.response.model`, `gen_ai.response.id` | gen_ai | string | Response model / id, only when the provider reported them (optional) |
| `gen_ai.response.finish_reasons` | gen_ai | string[] | `["stop"]`, `["tool_use"]`, … |
| `llm.response.finish_reason` | hermes | string | Same, scalar |
| `llm.response.duration_ms` | hermes | float | Wall-clock of the request |
| `hermes.api.ttfc_ms` | hermes | float | Streaming calls: milliseconds from the call's start (retries included) to the first response chunk, from Hermes's `started_at` / `first_chunk_at`; absent for a non-streamed call |
| `llm.response.output_chars`, `llm.response.tool_calls` | hermes | int | Assistant content length / tool-call count (optional) |
| `output.value`, `output.mime_type`, `gen_ai.output.messages` | both | string (JSON) | **Full** response: the text and, inside the one assistant message, its tool calls — `content_capture: full` (the default) |
| `hermes.content.output_chars` | hermes | int | Size of the captured response — `content_capture: full` |
| `hermes.cost.status` | hermes | string | How Hermes priced this call (`estimate_usage_cost`, the function behind `state.db` and `/usage`): `actual` (provider-reported) · `estimated` (price table or models API) · `included` (subscription route: no charge to report) · `unknown` (no price for the model). Absent on Hermes builds without the pricing module |
| `hermes.cost.source` | hermes | string | Where the price came from, as Hermes reports it (`official_docs_snapshot`, `provider_models_api`, `none`, …) (optional) |
| `hermes.cost.usage` | hermes | float | USD for this call, **only** for `actual` / `estimated`; never `0` for an unknown price or an included route (optional) |

[//]: # (generated:api-end:end)

### `api.*` on failure (`api_request_error`)

When the request fails, the span ends with `StatusCode.ERROR`, an `exception` event, and:

[//]: # (generated:api-failure:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `exception.type`, `exception.message`, `exception.escaped` | OTel | string / string / bool | On the `exception` span event the failed request records: the error class, its text and `true` (`exception.type` / `exception.message` also on a forwarded log record with `exc_info`) |
| `error.type` | OTel | string | Error class reported by Hermes (e.g. `RateLimitError`) |
| `http.response.status_code`, `gen_ai.response.status_code` | OTel / gen_ai | int | HTTP status (omitted for network errors) |
| `hermes.retry.count`, `hermes.max_retries`, `hermes.retryable` | hermes | int / int / bool | Retry state for this request |
| `llm.response.duration_ms` | hermes | float | Wall-clock of the failed attempt |

[//]: # (generated:api-failure:end)

The most recent `error.type` is also stamped on the turn's root span at `on_session_end`.

## `tool.*`

One per tool call (span kind `TOOL`), keyed by Hermes' `tool_call_id` so parallel calls never collide.

[//]: # (generated:tool:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `tool.name`, `gen_ai.tool.name` | both | string | Tool name |
| `gen_ai.tool.call.id` | gen_ai | string | Hermes `tool_call_id` (falls back to `task_id` on older Hermes) |
| `gen_ai.operation.name` | gen_ai | string | `execute_tool` |
| `input.value`, `gen_ai.tool.call.arguments` | both | string | Tool args (JSON preview; **full** for `mcp_*` tools in `content_capture: full`) |
| `output.value`, `gen_ai.tool.call.result` | both | string | Tool result preview |
| `hermes.preview.input.truncated`, `hermes.preview.input.original_chars`, `hermes.preview.output.truncated`, `hermes.preview.output.original_chars` | hermes | bool, int | Set only when the args or result preview was clipped: `true` and the original length (optional) |
| `hermes.tool.result.chars` | hermes | int | Size of the result string the model receives, every tool |
| `hermes.terminal.output.chars`, `hermes.terminal.returncode`, `hermes.terminal.env_type` | hermes | int, int, string | Terminal tool only: the output size as the terminal tool handed it to Hermes' transform stage (after the capture window, before the head+tail limit, whitespace strip and redaction), the exit code and the environment type, observed on `transform_terminal_output` ([hooks](/reference/hooks#transform_terminal_output)) |
| `hermes.terminal.output.truncated`, `hermes.terminal.output.dropped_chars` | hermes | bool, int | Terminal tool only: whether Hermes' output limit cut the output before the model saw it, and by how many characters (`dropped_chars` only when `true`). Read from Hermes' own record of the cut (the `output_total_chars` field of a capture-window spill, or the `[OUTPUT TRUNCATED - N chars omitted …]` notice of the head+tail limit), never from a size comparison, so whitespace stripping, ANSI removal and redaction are not mistaken for truncation |
| `hermes.tool.target` | hermes | string | First non-empty `path` / `file_path` / `target` / `url` / `uri` arg (optional) |
| `hermes.tool.command` | hermes | string | First non-empty `command` / `cmd` arg (optional) |
| `hermes.tool.outcome` | hermes | string | `error` · `timeout` · `blocked` · `cancelled` from Hermes' hook status or the tool's own result status; `completed` means the tool returned and nothing reported a failure |
| `hermes.tool.blocked_by` | hermes | string | Which governance floor blocked the call — `deny_rule` · `hardline` · `stdin_password_guard` (optional; only when positively classified, `outcome=blocked`) |
| `hermes.tool.decided_by` | hermes | string | `hard_floor` on the tool span when a floor (not a human) blocked the call; see also `hermes.approval.decided_by` on `approval.*` spans (optional) |
| `hermes.tool.git_commit` | hermes | bool | `true` when a terminal command was a `git commit` (matched as a word, so `git commit-tree` is not one) that exited 0: the call the commit counter ([metrics](/reference/metrics)) and the `commit` [event](/reference/log-events#commit) came from (optional) |
| `error.message` | OTel | string | Result `error` text when the outcome is `error` (optional) |
| `hermes.skill.name`, `hermes.skill.source` | hermes | string | Bare skill name and `skill_view` / `path_match` when the call loaded a skill (optional) |
| `hermes.tool.cpu.utilization.avg`, `hermes.tool.cpu.utilization.peak` | hermes | float | Process-tree CPU (0..1) during the call — `host_metrics` only |
| `hermes.tool.gpu.utilization.avg`, `hermes.tool.gpu.utilization.peak` | hermes | float | Host GPU busy ratio (0..1) during the call — `host_metrics` + GPU only |

[//]: # (generated:tool:end)

The utilization attributes are absent when host metrics are off or the tool finished between two samples. See [Host & GPU metrics](/configuration/host-metrics).

## `skill.*`

One per skill that loaded successfully this turn (`skill_spans: true`), nested under the root; opens when the loading tool call ends and closes at turn end.

[//]: # (generated:skill:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `hermes.skill.name`, `gen_ai.skill.name` | hermes / gen_ai | string | Bare skill name as Hermes names it |
| `hermes.skill.source` | hermes | string | `skill_view` · `path_match` |
| `hermes.skill.path` | hermes | string | Directory holding the skill's `SKILL.md`, as reported by `skill_view` (`skill_dir`) or resolved from the file the tool read; omitted when unknown, never derived from the name (optional) |
| `hermes.span_kind` | hermes | string | `skill` |
| `gen_ai.operation.name` | gen_ai | string | `execute_skill` |
| `hermes.skill.result_status` | hermes | string | Turn outcome at close (`completed` · `interrupted` · …) |

[//]: # (generated:skill:end)

## `approval.*`

One per human-in-the-loop (or smart-guardian) approval prompt, named `approval.<pattern_key>`.

[//]: # (generated:approval:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `hermes.approval.pattern_key`, `hermes.approval.pattern_keys` | hermes | string | Hermes' approval pattern key, when reported (the span is then `approval.<key>`, else `approval`) |
| `hermes.approval.surface` | hermes | string | `cli` · `telegram` · … (optional) |
| `hermes.approval.command`, `hermes.approval.description` | hermes | string | Gated command and Hermes' description (previews; optional) |
| `gen_ai.tool.call.id` | gen_ai | string | Correlates to the gated `tool.*` span (optional) |
| `hermes.span_kind` | hermes | string | `approval` |
| `hermes.approval.granted`, `hermes.approval.timed_out` | hermes | bool | Outcome flags |
| `hermes.approval.choice` | hermes | string | `once` · `session` · `always` · `deny` · `timeout` · `smart_approve` · `smart_deny` · `notify_failed` |
| `hermes.approval.decided_by` | hermes | string | `aux_llm` for smart-guardian verdicts, empty for a human answer (optional) |
| `hermes.approval.duration_ms` | hermes | float | Decision wait time |

[//]: # (generated:approval:end)

## `human_input.*`

One per wait on the person (a `clarify` question, a password prompt), named `human_input.<kind>` and nested under the turn's in-flight span; an approval wait is only counted, since it has its own `approval.*` span. The prompt Hermes passes is already redacted.

[//]: # (generated:human-input:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `hermes.span_kind` | hermes | string | `human_input` |
| `hermes.human_input.kind` | hermes | string | Hermes's kind of wait: `clarify`, a password prompt, … (an approval wait has its own `approval.*` span and is only counted) |
| `hermes.human_input.prompt` | hermes | string | What the person was asked, as Hermes redacted it; a preview under `content_capture` (optional) |
| `hermes.human_input.outcome` | hermes | string | How the wait ended, as Hermes reports it (`error` when it reported nothing) |
| `hermes.human_input.wait_s` | hermes | float | Seconds the agent waited on the person (optional) |

[//]: # (generated:human-input:end)

## `kanban.*`

One per kanban board transition ([hooks](/reference/hooks#kanban-board-transitions)): `kanban.claim`, `kanban.spawn`, `kanban.worker_exit`, `kanban.stale_claim` in the dispatcher process, `kanban.complete` and `kanban.block` in the worker; one run of a card is one trace rooted at `kanban.claim` ([span hierarchy](/architecture/span-hierarchy#kanban)). Opened and closed at once.

[//]: # (generated:kanban:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `hermes.span_kind` | hermes | string | `kanban` |
| `hermes.kanban.event` | hermes | string | `claimed` · `spawned` · `worker_exited` · `stale_claim` · `completed` · `blocked` |
| `hermes.kanban.task_id`, `hermes.kanban.board`, `hermes.kanban.assignee`, `hermes.kanban.run_id` | hermes | string | The card, its board, the profile assigned to it and the attempt (`run_id` as a string); the join across the dispatcher's and the worker's traces |
| `hermes.kanban.worker_pid` | hermes | int | The worker process (`kanban.spawn`, `kanban.worker_exit`, `kanban.stale_claim`) |
| `hermes.kanban.exit_kind`, `hermes.kanban.exit_code`, `hermes.kanban.retry_status` | hermes | string, int, string | How the worker process ended (`clean_exit` · `rate_limited` · `nonzero_exit` · `signaled` · `unknown`), its exit code, and what the dispatcher does next (`kanban.worker_exit`; `retry_status` also on `kanban.stale_claim`) |
| `hermes.kanban.heartbeat_stale` | hermes | bool | Whether the reclaimed claim had stopped heart-beating (`kanban.stale_claim`) |
| `hermes.kanban.outcome` | hermes | string | `completed` · `blocked` on the worker-side spans and on the worker's `agent` root; the dispatcher's own outcome word on `kanban.worker_exit` (`crashed`, `timed_out`, …) |
| `hermes.kanban.summary`, `hermes.kanban.reason` | hermes | string | The worker's hand-off summary (`kanban.complete`) or block reason (`kanban.block`), preview-clipped; absent when `capture_previews=false` |

[//]: # (generated:kanban:end)

The worker's `agent` root additionally carries `hermes.kanban.task_id`, `hermes.kanban.board`, `hermes.kanban.run_id` and `hermes.kanban.outcome` once the card is completed or blocked.

## `subagent.*`

One per delegated child agent (span kind `AGENT`); the child's own root nests beneath it (or is linked, cross-process).

[//]: # (generated:subagent:start)

| Attribute | Convention | Type | Meaning |
|---|---|---|---|
| `gen_ai.operation.name`, `gen_ai.agent.name` | gen_ai | string | `invoke_agent` / child role (`gen_ai.agent.name` absent when no role was reported) |
| `hermes.subagent.role`, `hermes.subagent.goal` | hermes | string | Child role (only when Hermes reports one; the span is then `subagent.<role>`, else `subagent`) and delegated goal (preview) |
| `input.value` | OpenInference | string | The goal preview |
| `hermes.subagent.child_session_id`, `hermes.subagent.child_id` | hermes | string | Child session / sub-agent ids |
| `hermes.subagent.parent_session_id`, `hermes.subagent.parent_turn_id`, `hermes.subagent.parent_id` | hermes | string | Parent identity |
| `hermes.subagent.status`, `hermes.subagent.duration_ms`, `hermes.subagent.summary`, `output.value` | hermes / OpenInference | mixed | On stop: reported `child_status`, wall-clock, result summary |
| `hermes.subagent.model_inherited` | hermes | bool | Set when the child's root starts, with `gen_ai.request.model` (the child's model): whether the child runs on its parent's model (`false` with `delegation.model` set) |
| `hermes.subagent.usage.input_tokens`, `hermes.subagent.usage.output_tokens`, `hermes.subagent.usage.total_tokens`, `hermes.subagent.api_call_count` | hermes | int | On stop: tokens and API calls of the child's whole subtree (its own turns plus every delegation it made); namespaced so the child root's `gen_ai.usage.*` is not counted twice by a reader that sums agent spans |
| `hermes.subagent.cost.usage`, `hermes.subagent.cost.status` | hermes | mixed | On stop: the subtree's USD and pricing status, by the turn root's rule (an amount only when every call was priced) |
| `hermes.subagent.swept` | hermes | bool | `true` when the delegation was closed by its TTL (`subagent_span_ttl_ms`) because `subagent_stop` never arrived |

[//]: # (generated:subagent:end)

## Metrics

Metrics are documented on their own page: [Metrics reference](/reference/metrics).
