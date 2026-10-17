"""The plugin's telemetry vocabulary, in one place (#404).

Every attribute name the plugin emits, the fallback chains readers use to
find a value under either convention (OpenInference ``llm.*`` / ``input.value``
for Phoenix and OTel GenAI ``gen_ai.*`` for everything else), the span-kind
classification and the event names. Pure data: no OpenTelemetry import, so
the dashboard package, the query CLI, the scripts and the generator can all
import it.

What derives from this module:

* The tables of ``website/docs/reference/span-attributes.md`` are rendered
  from :data:`ROWS` by ``scripts/gen_semconv.py`` (``--check`` fails when they
  drift, #523); the page keeps its prose.
* ``dashboard-ui/src/semconv.generated.ts`` carries the key chains and the
  span-kind table for ``lib.ts``, written by ``scripts/gen_semconv.py``.
* ``hermes_otel/dashboard/backends/_attrs.KNOWN_ATTRIBUTES`` is
  :data:`ATTRIBUTE_NAMES`.
* ``live_summary.span_kind`` / ``live_store._kind_sql`` and the query CLI (``query_render``,
  ``query_sources``) read the key chains and :data:`SPAN_KINDS` from here.

Adding an attribute: emit it in a hook, add it to a :class:`Row` here (its own,
or an existing row it shares a meaning with), run ``python scripts/gen_semconv.py``
and commit the regenerated TypeScript and reference page. The hook literal is
checked against the registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Row:
    """One row of the span-attributes reference page.

    ``names`` are the attributes the row lists together (one row documents
    a family that shares a meaning); ``where`` is the table the row sits in:
    a page table (see ``TABLES`` in ``scripts/gen_semconv.py``) or one of
    ``event`` / ``log record`` / ``backend`` for names that exist in the
    registry but are documented on the log-events page or only derived by a
    backend. ``group`` is the registry's own topic (session, usage, tool, ...),
    ``type`` and ``doc`` are the row's Type and Meaning cells. An attribute may
    appear in several rows (``gen_ai.operation.name`` is ``invoke_agent`` on a
    root and ``chat`` on an ``api.*`` span); the first row is its home.
    """

    names: Tuple[str, ...]
    where: str
    group: str
    type: str
    doc: str


ROWS: Tuple[Row, ...] = (
    # ── Resource ──
    Row(
        ("service.name",),
        "Resource",
        "resource",
        "string",
        "`hermes-agent`; the named profile (`coder`) with `service_name_from_profile: true`. `resource_attributes:` overrides either",
    ),
    Row(
        ("service.namespace",),
        "Resource",
        "resource",
        "string",
        "`hermes-agent`, only with `service_name_from_profile: true` and a named profile active (see [Profiles](/configuration/profiles#one-service-per-profile))",
    ),
    Row(
        ("service.instance.id",),
        "Resource",
        "resource",
        "string",
        "UUID generated once per process — keeps two Hermes processes on different metric series",
    ),
    Row(
        ("service.version",),
        "Resource",
        "resource",
        "string",
        "Plugin version, read from the shipped `plugin.yaml`",
    ),
    Row(("process.pid",), "Resource", "resource", "int", "Process id"),
    Row(
        ("os.type",),
        "Resource",
        "resource",
        "string",
        "Operating system in the OTel spelling (`darwin`, `linux`, `windows`), from `platform.system()`, so a fleet can be split by platform (#476)",
    ),
    Row(
        ("host.arch",),
        "Resource",
        "resource",
        "string",
        "CPU architecture in the OTel spelling (`arm64`, `amd64`, `x86` …), from `platform.machine()` (#476)",
    ),
    Row(
        ("openinference.project.name",),
        "Resource",
        "resource",
        "string",
        "`project_name` / `OTEL_PROJECT_NAME` (the Phoenix project); optional",
    ),
    Row(
        ("hermes.kanban.task_id", "hermes.kanban.board", "hermes.kanban.run_id"),
        "Resource",
        "kanban",
        "string",
        "The kanban card, its board and the attempt a dispatcher-spawned worker is running, from `HERMES_KANBAN_TASK` / `_BOARD` / `_RUN_ID`. Only when the task is set: Hermes pins the board into every chat's environment, so the board alone never marks a worker",
    ),
    Row(
        ("host.name",), "Resource", "resource", "string", "Hostname, only when `host_metrics: true`"
    ),
    Row(
        ("wandb.entity", "wandb.project"),
        "Resource",
        "resource",
        "string",
        "W&B Weave routing (when configured)",
    ),
    Row(
        ("telemetry.sdk.name", "telemetry.sdk.version", "telemetry.sdk.language"),
        "Resource",
        "resource",
        "string",
        "Set by the OTel SDK",
    ),
    # ── Every span ──
    Row(
        ("openinference.span.kind",),
        "Every span",
        "span_kind",
        "string",
        "`AGENT` (root, subagent) · `LLM` (`llm.*`, `api.*`) · `TOOL` (`tool.*`) · `GENERAL` (skill, approval, human_input, kanban)",
    ),
    Row(
        ("traceloop.span.kind",),
        "Every span",
        "span_kind",
        "string",
        "The same span kinds in Traceloop's lowercase vocabulary: `agent` (root, subagent) · `tool` (`tool.*`) · `task` (everything else; Traceloop has no LLM kind, model spans are identified by `gen_ai.*`) · `unknown`. Filter on this from openllmetry-aware tools such as `opentelemetry-mcp` (since 1.26.0; earlier releases wrote the OpenInference spelling here, #382)",
    ),
    Row(
        ("hermes.turn.number",),
        "Every span",
        "turn",
        "int",
        "1-based index of the user prompt within the session. A session resumed in a new process (`hermes -r`) continues from the user prompts in its conversation; after a context compaction the count can only stay or grow. On the root and on every `llm.*` / `api.*` / `tool.*` span of the turn",
    ),
    Row(
        ("session.id", "session_id"),
        "Every span",
        "session",
        "string",
        "Hermes session id, on the root and on `llm.*` / `api.*` / `tool.*` / `subagent.*`; `session_id` is the plugin's oldest spelling of the same value, kept for dashboards built on it",
    ),
    Row(
        ("gen_ai.conversation.id",),
        "Every span",
        "session",
        "string",
        "Same id, gen_ai spelling, wherever `gen_ai.operation.name` is set",
    ),
    Row(
        ("correlation.id",),
        "Every span",
        "session",
        "string",
        "A correlation id the host passed on the hook (`correlation_id` / `correlation.id` / `x-correlation-id` …), reused on every span of that session. Hermes 0.21 passes none, so the attribute is absent; group by `session.id` instead",
    ),
    Row(
        ("hermes.span.ended_by", "hermes.span.sweep_reason"),
        "Every span",
        "turn",
        "string",
        "On a child span (`tool.*` / `api.*` / `llm.*` / `approval.*` / `skill.*`) the plugin ended on the operation's behalf because the operation's own end hook never came: `ended_by` is `sweep` and `sweep_reason` says why, `turn_end` (still open at `on_session_end`) · `session_end` (at finalize / reset) · `turn_failed` (every API attempt of the turn failed and the next turn closed it) · `ttl` (the [orphan sweep](/architecture/orphan-sweep), `root_span_ttl_ms`) · `shutdown`. Absent on a normally ended span (#477)",
    ),
    Row(
        ("user.id", "hermes.sender.id"),
        "Every span",
        "session",
        "string",
        "Gateway sender identity — only with `capture_sender_id: true`. `user.id` is `platform:sender`",
    ),
    # ── agent / cron: start ──
    Row(
        ("hermes.session.kind",),
        "agent / cron: start",
        "session",
        "string",
        "`session` · `cron` (from Hermes' `session_type` / `origin` / `run_type` when a host passes one; Hermes 0.21 passes none, so `cron` comes from a cron platform and everything else is `session`)",
    ),
    Row(
        ("hermes.session_id",),
        "agent / cron: start",
        "session",
        "string",
        "Session id (pre-existing spelling; `session.id` is the standard one)",
    ),
    Row(
        ("llm.model_name",), "agent / cron: start", "model", "string", "Model the turn started with"
    ),
    Row(
        ("hermes.platform",),
        "agent / cron: start",
        "session",
        "string",
        "The Hermes surface the turn ran on: `cli` · `telegram` · `discord` · `cron` · … (never reported as a provider)",
    ),
    Row(
        ("hermes.profile",),
        "agent / cron: start",
        "session",
        "string",
        "The Hermes profile that ran the turn: `default`, the profile id under `~/.hermes/profiles/`, or `custom`. Also a resource attribute and the `profile` label on every metric ([profiles](/configuration/profiles))",
    ),
    Row(
        ("hermes.llm.output.chars",),
        "agent / cron: start",
        "turn",
        "int",
        "Size of the turn's final response text, observed on `transform_llm_output` ([hooks](/reference/hooks#transform_llm_output)); absent when the finalizer did not run (optional)",
    ),
    Row(
        ("llm.provider", "gen_ai.provider.name", "gen_ai.system"),
        "agent / cron: start",
        "model",
        "string",
        "The LLM provider the turn's API calls reported, set at **end**; absent when no API call reported one. Hermes 0.21 passes no provider on `on_session_start`. Each carries its convention's well-known spelling when there is one for the Hermes id (`gen_ai.*`: `x_ai`, `aws.bedrock`, `gcp.vertex_ai`, `gcp.gemini`, `azure.ai.inference`, `openai` for `openai-api` / `openai-codex`; `llm.provider`: `xai`, `aws`, `google`, `azure`, `moonshot`, `minimax`), else the raw id (`openrouter`, `nvidia`, `custom` …) (#475)",
    ),
    Row(
        ("hermes.provider",),
        "agent / cron: start",
        "model",
        "string",
        "The provider id exactly as Hermes names it (`xai`, `bedrock`, `openai-api`, `openrouter` …), beside the convention spellings in `gen_ai.provider.name` / `llm.provider`, wherever those are set (#475)",
    ),
    Row(("gen_ai.request.model",), "agent / cron: start", "model", "string", "Model name"),
    Row(
        ("hermes.turn.stop_reason",),
        "agent / cron: start",
        "session",
        "string",
        "Set when `agent_loop_stopped` fires for the turn (`/stop`, the `/new` fast path): the reason Hermes gives; a stopped turn whose end never arrives is swept as `interrupted`, not `timed_out`",
    ),
    Row(
        ("hermes.turn.verify_nudges",),
        "agent / cron: start",
        "session",
        "int",
        "How many times `pre_verify` fired this turn (the agent edited code and was about to finish); never the paths or the response",
    ),
    Row(
        ("hermes.turn.trigger",),
        "agent / cron: start",
        "session",
        "string",
        "What started the turn: `user` (CLI, TUI, desktop, a gateway platform, ACP), `cron`, `kanban` (a worker), `subagent` (a delegated child), `a2a`, `api` (the API server), `oneshot` (`hermes -z`, `chat -q`), `curator`, `batch` or `system`. `/loop`, `/heartbeat` and `/goal` re-entries and bot-to-bot DMs are not observable at turn start and classify as the turn they ride on (#444)",
    ),
    Row(
        ("hermes.agent.model_override",),
        "agent / cron: start",
        "model",
        "bool",
        "Whether the turn runs on a model other than the profile's `model.default` (a `delegation.model` child, a kanban `--model` worker, a cron job's own model, `-m`, `/model`); absent when either is unknown",
    ),
    Row(("gen_ai.operation.name",), "agent / cron: start", "model", "string", "`invoke_agent`"),
    Row(
        ("gen_ai.agent.name",),
        "agent / cron: start",
        "model",
        "string",
        "The host's `agent_name` when it passes one, else the active named profile (`coder`), else `hermes-agent`; Weave shows it as the agent",
    ),
    Row(
        ("wandb.is_turn", "wandb.thread_id"),
        "agent / cron: start",
        "model",
        "bool / string",
        "Marks a Weave conversation turn and groups turns by session",
    ),
    Row(
        ("weave.agent.version",),
        "agent / cron: start",
        "model",
        "string",
        "Plugin version (optional)",
    ),
    Row(
        ("hermes.trace.remote_parent",),
        "agent / cron: start",
        "session",
        "bool",
        "`true` when the root was parented under the span an outer process named in `HERMES_OTEL_TRACEPARENT` ([env vars](/reference/env-vars#join-an-outer-trace), #474); absent otherwise",
    ),
    Row(
        ("hermes.session.synthesized",),
        "agent / cron: start",
        "session",
        "bool",
        "`true` on continuation turns: Hermes fires `on_session_start` only for a session's first turn, so later turns open their root lazily at the first hook of the turn (optional)",
    ),
    Row(
        ("hermes.session.previous_id",),
        "agent / cron: start",
        "session",
        "string",
        "The session this one replaced (`/new`, `/reset`); the root also carries a span link (`hermes.link=previous_session`) to that session's last root (optional)",
    ),
    Row(
        ("hermes.session.finalize_reason", "hermes.session.reset_reason"),
        "agent / cron: start",
        "session",
        "string",
        "Set when a still-open root was closed by `on_session_finalize` / `on_session_reset`, with `hermes.turn.final_status` `finalized` / `reset` (optional)",
    ),
    Row(
        ("hermes.link",),
        "agent / cron: start",
        "session",
        "string",
        "On the span **link** from a replacing session's first root to the old session's last root: `previous_session`",
    ),
    Row(
        (
            "hermes.session_id",
            "hermes.session.turn_count",
            "hermes.session.duration_s",
            "hermes.session.finalize_reason",
            "hermes.session.reset_reason",
        ),
        "agent / cron: start",
        "session",
        "mixed",
        "On the **log record** `on_session_finalize` / `on_session_reset` emit (exported with `capture_logs`), not on a span",
    ),
    Row(
        ("hermes.cron.job_id",),
        "agent / cron: start",
        "session",
        "string",
        "Cron job id, `cron` roots only: from the hook payload, else parsed from the scheduler's `cron_<job id>_<timestamp>` session id (optional)",
    ),
    Row(
        ("hermes.session.is_subagent",),
        "agent / cron: start",
        "session",
        "bool",
        "`true` on a delegated child's own root (optional)",
    ),
    Row(
        ("hermes.subagent.role", "hermes.subagent.parent_session_id"),
        "agent / cron: start",
        "subagent",
        "string",
        "On a delegated child's root (optional)",
    ),
    # ── agent / cron: end ──
    Row(
        ("hermes.session.completed", "hermes.session.interrupted"),
        "agent / cron: end",
        "session",
        "bool",
        "Hook payload flags",
    ),
    Row(
        ("hermes.turn.final_status",),
        "agent / cron: end",
        "turn",
        "string",
        "`completed` · `interrupted` · `failed` · `incomplete` · `timed_out` · `finalized` · `reset` — from Hermes' `completed` / `interrupted` / `failed` flags (`timed_out` from the orphan sweep; `finalized` / `reset` when a session boundary closed the root; `failed` also when the turn's last API attempt failed and Hermes never ended it, #467)",
    ),
    Row(
        ("hermes.session.failed",),
        "agent / cron: end",
        "session",
        "bool",
        "Hermes' `failed` flag on `on_session_end`",
    ),
    Row(
        ("hermes.turn.exit_reason",),
        "agent / cron: end",
        "turn",
        "string",
        "Hermes' `turn_exit_reason` (`interrupted_by_user`, `guardrail_halt`, `context_compression_timeout`, `max_iterations_reached(n/m)` …), when reported",
    ),
    Row(
        ("hermes.turn.tool_count", "hermes.turn.tools"),
        "agent / cron: end",
        "turn",
        "int / string",
        "Distinct tool names (sorted CSV, ≤500 chars)",
    ),
    Row(
        ("hermes.turn.tool_targets", "hermes.turn.tool_commands"),
        "agent / cron: end",
        "turn",
        "string",
        "`|`-joined distinct paths/URLs and shell commands",
    ),
    Row(
        ("hermes.turn.tool_outcomes",),
        "agent / cron: end",
        "turn",
        "string",
        "Sorted CSV of distinct outcomes",
    ),
    Row(
        ("hermes.turn.skill_count", "hermes.turn.skills"),
        "agent / cron: end",
        "turn",
        "int / string",
        "Skills that loaded successfully this turn",
    ),
    Row(
        ("hermes.turn.api_call_count",),
        "agent / cron: end",
        "turn",
        "int",
        "`pre_api_request` hooks fired",
    ),
    Row(
        ("hermes.turn.auxiliary.api_call_count", "hermes.turn.auxiliary.error_count"),
        "agent / cron: end",
        "turn",
        "int",
        "Auxiliary LLM attempts that ran under this turn (titling, compression, MoA, vision, …) and how many failed (optional)",
    ),
    Row(
        ("hermes.turn.auxiliary.input_tokens", "hermes.turn.auxiliary.output_tokens"),
        "agent / cron: end",
        "turn",
        "int",
        "Their token sum, reported **beside** `gen_ai.usage.*`, never inside it: the `moa` provider's main-loop `api.*` span already carries what the MoA facade returns (optional)",
    ),
    Row(
        ("hermes.turn.auxiliary.cost.usage", "hermes.turn.auxiliary.cost.status"),
        "agent / cron: end",
        "turn",
        "float / string",
        "Their USD sum, with the same `actual` · `estimated` · `partial` · `included` · `unknown` rule as `hermes.cost.status` (optional)",
    ),
    Row(
        ("hermes.cost.usage",),
        "agent / cron: end",
        "cost",
        "float",
        "The turn's USD cost: the sum of its API calls' `hermes.cost.usage`, set **only when every call was priced** (optional)",
    ),
    Row(
        ("hermes.cost.status",),
        "agent / cron: end",
        "cost",
        "string",
        "`actual` (every call provider-reported) · `estimated` · `partial` (some calls priced, some not: no total) · `included` (subscription route) · `unknown` (no price for the model); absent when no call reported usage",
    ),
    Row(("gen_ai.response.model",), "agent / cron: end", "model", "string", "Model at turn end"),
    Row(
        ("error.type",),
        "agent / cron: end",
        "error",
        "string",
        "Most recent provider error class this turn (optional)",
    ),
    Row(
        ("input.value", "output.value"),
        "agent / cron: end",
        "content",
        "string",
        "First user message / final assistant response of the turn (previews; optional)",
    ),
    # ── llm.* ──
    Row(("llm.model_name",), "llm.*", "model", "string", "Model"),
    Row(
        ("llm.provider", "gen_ai.provider.name", "gen_ai.system"),
        "llm.*",
        "model",
        "string",
        "The LLM provider reported by this turn's API calls, in each convention's spelling (see the turn root); absent on the first call before any API request reported one",
    ),
    Row(("gen_ai.request.model",), "llm.*", "model", "string", "Model name"),
    Row(("gen_ai.operation.name",), "llm.*", "model", "string", "`chat`"),
    Row(
        ("input.value", "input.mime_type"),
        "llm.*",
        "content",
        "string",
        "User message (`text/plain`) or, with `capture_conversation_history`, the conversation JSON (`application/json`)",
    ),
    Row(
        ("gen_ai.input.messages",),
        "llm.*",
        "content",
        "string (JSON)",
        "Same content in the gen_ai message shape",
    ),
    Row(
        ("output.value", "output.mime_type"),
        "llm.*",
        "content",
        "string",
        "Final assistant response",
    ),
    Row(
        (
            "hermes.preview.input.truncated",
            "hermes.preview.input.original_chars",
            "hermes.preview.output.truncated",
            "hermes.preview.output.original_chars",
        ),
        "llm.*",
        "content",
        "bool, int",
        "Set only when the preview was clipped: `true` and the value's original length (optional)",
    ),
    Row(
        ("gen_ai.output.messages",), "llm.*", "content", "string (JSON)", "Same in the gen_ai shape"
    ),
    Row(
        ("gen_ai.response.model",),
        "llm.*",
        "model",
        "string",
        "The response model a provider reported on this turn's API calls; absent when none was reported (never the request model)",
    ),
    Row(
        ("hermes.conversation.message_count",),
        "llm.*",
        "turn",
        "int",
        "Message count when conversation capture is on (optional)",
    ),
    # ── api.*: start ──
    Row(("llm.model_name", "llm.provider"), "api.*: start", "model", "string", "Model / provider"),
    Row(
        ("gen_ai.request.model", "gen_ai.system", "gen_ai.provider.name"),
        "api.*: start",
        "model",
        "string",
        "Model / provider (`gen_ai.system` is the legacy spelling Langfuse reads); the provider in the GenAI well-known spelling when there is one, else the Hermes id, which `hermes.provider` always carries (#475)",
    ),
    Row(("gen_ai.operation.name",), "api.*: start", "model", "string", "`chat`"),
    Row(
        ("hermes.query_source",),
        "api.*: start",
        "model",
        "string",
        "Which loop the call belongs to: `main` (the agent loop), `subagent` (a delegated child's own loop), `auxiliary` (a call Hermes made on the agent's behalf: titling, compression, MoA reference models and aggregator, vision, approval, the kanban decomposer, the `/goal` judge, …; [hooks](/reference/hooks#pre_auxiliary_call--post_auxiliary_call))",
    ),
    Row(
        ("hermes.auxiliary.task",),
        "api.*: start",
        "model",
        "string",
        "The auxiliary task, as Hermes names it (`title_generation`, `compression`, `moa_reference`, `moa_aggregator`, `vision`, `approval`, `kanban_decomposer`, `triage_specifier`, `goal_judge`, …); `unknown` when it sent none. Auxiliary spans only",
    ),
    Row(
        ("llm.api_mode",),
        "api.*: start",
        "model",
        "string",
        "`chat_completions` · `anthropic_messages` · `codex_responses` · …",
    ),
    Row(
        ("llm.request.message_count", "llm.request.approx_input_tokens", "llm.request.max_tokens"),
        "api.*: start",
        "model",
        "int",
        "Request shape as Hermes reports it",
    ),
    Row(
        ("llm.request.tool_count",),
        "api.*: start",
        "model",
        "int",
        "Tools offered on an auxiliary call, when any (optional; auxiliary spans only)",
    ),
    Row(
        (
            "gen_ai.request.max_tokens",
            "gen_ai.request.temperature",
            "gen_ai.request.top_p",
            "gen_ai.request.top_k",
            "gen_ai.request.frequency_penalty",
            "gen_ai.request.presence_penalty",
            "gen_ai.request.stream",
            "gen_ai.request.reasoning.level",
            "gen_ai.request.stop_sequences",
            "gen_ai.request.choice.count",
        ),
        "api.*: start",
        "model",
        "mixed",
        "Request parameters, each only when Hermes passes it (optional)",
    ),
    Row(
        ("input.value", "input.mime_type", "gen_ai.input.messages"),
        "api.*: start",
        "content",
        "string (JSON)",
        "**Full** request messages, one copy per convention — `content_capture: full` (the default)",
    ),
    Row(
        ("gen_ai.system_instructions",),
        "api.*: start",
        "content",
        "string",
        "**Full** system prompt: the leading system message, or the Responses API `instructions` — `content_capture: full`",
    ),
    Row(
        ("hermes.content.input_chars",),
        "api.*: start",
        "content",
        "int",
        "Size of the captured request JSON — `content_capture: full`",
    ),
    # ── api.*: end ──
    Row(
        ("llm.token_count.prompt", "llm.token_count.completion", "llm.token_count.total"),
        "api.*: end",
        "usage",
        "int",
        "Whole prompt (incl. cache reads/writes), completion, sum",
    ),
    Row(
        ("llm.token_count.prompt_details.cache_read", "llm.token_count.prompt_details.cache_write"),
        "api.*: end",
        "usage",
        "int",
        "Cache buckets (optional)",
    ),
    Row(
        ("llm.token_count.completion_details.reasoning",),
        "api.*: end",
        "usage",
        "int",
        "Reasoning tokens, a subset of completion (optional)",
    ),
    Row(
        ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.total_tokens"),
        "api.*: end",
        "usage",
        "int",
        "Same three totals",
    ),
    Row(
        ("gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_creation.input_tokens"),
        "api.*: end",
        "usage",
        "int",
        "Cache buckets, current spelling (optional)",
    ),
    Row(
        ("gen_ai.usage.cache_read_input_tokens", "gen_ai.usage.cache_creation_input_tokens"),
        "api.*: end",
        "usage",
        "int",
        "Same values, pre-existing alias kept for older dashboards (optional)",
    ),
    Row(
        ("gen_ai.usage.reasoning.output_tokens",),
        "api.*: end",
        "usage",
        "int",
        "Reasoning tokens (optional)",
    ),
    Row(
        ("gen_ai.response.model", "gen_ai.response.id"),
        "api.*: end",
        "model",
        "string",
        "Response model / id, only when the provider reported them (optional)",
    ),
    Row(
        ("gen_ai.response.finish_reasons",),
        "api.*: end",
        "model",
        "string[]",
        '`["stop"]`, `["tool_use"]`, …',
    ),
    Row(("llm.response.finish_reason",), "api.*: end", "model", "string", "Same, scalar"),
    Row(("llm.response.duration_ms",), "api.*: end", "model", "float", "Wall-clock of the request"),
    Row(
        ("hermes.api.ttfc_ms",),
        "api.*: end",
        "api",
        "float",
        "Streaming calls: milliseconds from the call's start (retries included) to the first response chunk, from Hermes's `started_at` / `first_chunk_at`; absent for a non-streamed call",
    ),
    Row(
        ("llm.response.output_chars", "llm.response.tool_calls"),
        "api.*: end",
        "model",
        "int",
        "Assistant content length / tool-call count (optional)",
    ),
    Row(
        ("output.value", "output.mime_type", "gen_ai.output.messages"),
        "api.*: end",
        "content",
        "string (JSON)",
        "**Full** response: the text and, inside the one assistant message, its tool calls — `content_capture: full` (the default)",
    ),
    Row(
        ("hermes.content.output_chars",),
        "api.*: end",
        "content",
        "int",
        "Size of the captured response — `content_capture: full`",
    ),
    Row(
        ("hermes.cost.status",),
        "api.*: end",
        "cost",
        "string",
        "How Hermes priced this call (`estimate_usage_cost`, the function behind `state.db` and `/usage`): `actual` (provider-reported) · `estimated` (price table or models API) · `included` (subscription route: no charge to report) · `unknown` (no price for the model). Absent on Hermes builds without the pricing module",
    ),
    Row(
        ("hermes.cost.source",),
        "api.*: end",
        "cost",
        "string",
        "Where the price came from, as Hermes reports it (`official_docs_snapshot`, `provider_models_api`, `none`, …) (optional)",
    ),
    Row(
        ("hermes.cost.usage",),
        "api.*: end",
        "cost",
        "float",
        "USD for this call, **only** for `actual` / `estimated`; never `0` for an unknown price or an included route (optional)",
    ),
    # ── api.* on failure ──
    Row(
        ("exception.type", "exception.message", "exception.escaped"),
        "api.* on failure",
        "error",
        "string / string / bool",
        "On the `exception` span event the failed request records: the error class, its text and `true` (`exception.type` / `exception.message` also on a forwarded log record with `exc_info`)",
    ),
    Row(
        ("error.type",),
        "api.* on failure",
        "error",
        "string",
        "Error class reported by Hermes (e.g. `RateLimitError`)",
    ),
    Row(
        ("http.response.status_code", "gen_ai.response.status_code"),
        "api.* on failure",
        "error",
        "int",
        "HTTP status (omitted for network errors)",
    ),
    Row(
        ("hermes.retry.count", "hermes.max_retries", "hermes.retryable"),
        "api.* on failure",
        "error",
        "int / int / bool",
        "Retry state for this request",
    ),
    Row(
        ("llm.response.duration_ms",),
        "api.* on failure",
        "model",
        "float",
        "Wall-clock of the failed attempt",
    ),
    # ── tool.* ──
    Row(("tool.name", "gen_ai.tool.name"), "tool.*", "tool", "string", "Tool name"),
    Row(
        ("gen_ai.tool.call.id",),
        "tool.*",
        "tool",
        "string",
        "Hermes `tool_call_id` (falls back to `task_id` on older Hermes)",
    ),
    Row(("gen_ai.operation.name",), "tool.*", "model", "string", "`execute_tool`"),
    Row(
        ("input.value", "gen_ai.tool.call.arguments"),
        "tool.*",
        "tool",
        "string",
        "Tool args (JSON preview; **full** for `mcp_*` tools in `content_capture: full`)",
    ),
    Row(
        ("output.value", "gen_ai.tool.call.result"),
        "tool.*",
        "tool",
        "string",
        "Tool result preview",
    ),
    Row(
        (
            "hermes.preview.input.truncated",
            "hermes.preview.input.original_chars",
            "hermes.preview.output.truncated",
            "hermes.preview.output.original_chars",
        ),
        "tool.*",
        "content",
        "bool, int",
        "Set only when the args or result preview was clipped: `true` and the original length (optional)",
    ),
    Row(
        ("hermes.tool.result.chars",),
        "tool.*",
        "tool",
        "int",
        "Size of the result string the model receives, every tool",
    ),
    Row(
        ("hermes.terminal.output.chars", "hermes.terminal.returncode", "hermes.terminal.env_type"),
        "tool.*",
        "tool",
        "int, int, string",
        "Terminal tool only: the output size as the terminal tool handed it to Hermes' transform stage (after the capture window, before the head+tail limit, whitespace strip and redaction), the exit code and the environment type, observed on `transform_terminal_output` ([hooks](/reference/hooks#transform_terminal_output))",
    ),
    Row(
        ("hermes.terminal.output.truncated", "hermes.terminal.output.dropped_chars"),
        "tool.*",
        "tool",
        "bool, int",
        "Terminal tool only: whether Hermes' output limit cut the output before the model saw it, and by how many characters (`dropped_chars` only when `true`). Read from Hermes' own record of the cut (the `output_total_chars` field of a capture-window spill, or the `[OUTPUT TRUNCATED - N chars omitted …]` notice of the head+tail limit), never from a size comparison, so whitespace stripping, ANSI removal and redaction are not mistaken for truncation",
    ),
    Row(
        ("hermes.tool.target",),
        "tool.*",
        "tool",
        "string",
        "First non-empty `path` / `file_path` / `target` / `url` / `uri` arg (optional)",
    ),
    Row(
        ("hermes.tool.command",),
        "tool.*",
        "tool",
        "string",
        "First non-empty `command` / `cmd` arg (optional)",
    ),
    Row(
        ("hermes.tool.outcome",),
        "tool.*",
        "tool",
        "string",
        "`error` · `timeout` · `blocked` · `cancelled` from Hermes' hook status or the tool's own result status; `completed` means the tool returned and nothing reported a failure",
    ),
    Row(
        ("hermes.tool.blocked_by",),
        "tool.*",
        "tool",
        "string",
        "Which governance floor blocked the call — `deny_rule` · `hardline` · `stdin_password_guard` (optional; only when positively classified, `outcome=blocked`)",
    ),
    Row(
        ("hermes.tool.decided_by",),
        "tool.*",
        "tool",
        "string",
        "`hard_floor` on the tool span when a floor (not a human) blocked the call; see also `hermes.approval.decided_by` on `approval.*` spans (optional)",
    ),
    Row(
        ("error.message",),
        "tool.*",
        "error",
        "string",
        "Result `error` text when the outcome is `error` (optional)",
    ),
    Row(
        ("hermes.skill.name", "hermes.skill.source"),
        "tool.*",
        "skill",
        "string",
        "Bare skill name and `skill_view` / `path_match` when the call loaded a skill (optional)",
    ),
    Row(
        ("hermes.tool.cpu.utilization.avg", "hermes.tool.cpu.utilization.peak"),
        "tool.*",
        "tool",
        "float",
        "Process-tree CPU (0..1) during the call — `host_metrics` only",
    ),
    Row(
        ("hermes.tool.gpu.utilization.avg", "hermes.tool.gpu.utilization.peak"),
        "tool.*",
        "tool",
        "float",
        "Host GPU busy ratio (0..1) during the call — `host_metrics` + GPU only",
    ),
    # ── skill.* ──
    Row(
        ("hermes.skill.name", "gen_ai.skill.name"),
        "skill.*",
        "skill",
        "string",
        "Bare skill name as Hermes names it",
    ),
    Row(("hermes.skill.source",), "skill.*", "skill", "string", "`skill_view` · `path_match`"),
    Row(
        ("hermes.skill.path",),
        "skill.*",
        "skill",
        "string",
        "Directory holding the skill's `SKILL.md`, as reported by `skill_view` (`skill_dir`) or resolved from the file the tool read; omitted when unknown, never derived from the name (optional)",
    ),
    Row(("hermes.span_kind",), "skill.*", "span_kind", "string", "`skill`"),
    Row(("gen_ai.operation.name",), "skill.*", "model", "string", "`execute_skill`"),
    Row(
        ("hermes.skill.result_status",),
        "skill.*",
        "skill",
        "string",
        "Turn outcome at close (`completed` · `interrupted` · …)",
    ),
    # ── approval.* ──
    Row(
        ("hermes.approval.pattern_key", "hermes.approval.pattern_keys"),
        "approval.*",
        "approval",
        "string",
        "Hermes' approval pattern key, when reported (the span is then `approval.<key>`, else `approval`)",
    ),
    Row(
        ("hermes.approval.surface",),
        "approval.*",
        "approval",
        "string",
        "`cli` · `telegram` · … (optional)",
    ),
    Row(
        ("hermes.approval.command", "hermes.approval.description"),
        "approval.*",
        "approval",
        "string",
        "Gated command and Hermes' description (previews; optional)",
    ),
    Row(
        ("gen_ai.tool.call.id",),
        "approval.*",
        "tool",
        "string",
        "Correlates to the gated `tool.*` span (optional)",
    ),
    Row(("hermes.span_kind",), "approval.*", "span_kind", "string", "`approval`"),
    Row(
        ("hermes.approval.granted", "hermes.approval.timed_out"),
        "approval.*",
        "approval",
        "bool",
        "Outcome flags",
    ),
    Row(
        ("hermes.approval.choice",),
        "approval.*",
        "approval",
        "string",
        "`once` · `session` · `always` · `deny` · `timeout` · `smart_approve` · `smart_deny` · `notify_failed`",
    ),
    Row(
        ("hermes.approval.decided_by",),
        "approval.*",
        "approval",
        "string",
        "`aux_llm` for smart-guardian verdicts, empty for a human answer (optional)",
    ),
    Row(("hermes.approval.duration_ms",), "approval.*", "approval", "float", "Decision wait time"),
    # ── human_input.* ──
    Row(("hermes.span_kind",), "human_input.*", "span_kind", "string", "`human_input`"),
    Row(
        ("hermes.human_input.kind",),
        "human_input.*",
        "human_input",
        "string",
        "Hermes's kind of wait: `clarify`, a password prompt, … (an approval wait has its own `approval.*` span and is only counted)",
    ),
    Row(
        ("hermes.human_input.prompt",),
        "human_input.*",
        "human_input",
        "string",
        "What the person was asked, as Hermes redacted it; a preview under `content_capture` (optional)",
    ),
    Row(
        ("hermes.human_input.outcome",),
        "human_input.*",
        "human_input",
        "string",
        "How the wait ended, as Hermes reports it (`error` when it reported nothing)",
    ),
    Row(
        ("hermes.human_input.wait_s",),
        "human_input.*",
        "human_input",
        "float",
        "Seconds the agent waited on the person (optional)",
    ),
    # ── kanban.* ──
    Row(("hermes.span_kind",), "kanban.*", "span_kind", "string", "`kanban`"),
    Row(
        ("hermes.kanban.event",),
        "kanban.*",
        "kanban",
        "string",
        "`claimed` · `spawned` · `worker_exited` · `stale_claim` · `completed` · `blocked`",
    ),
    Row(
        (
            "hermes.kanban.task_id",
            "hermes.kanban.board",
            "hermes.kanban.assignee",
            "hermes.kanban.run_id",
        ),
        "kanban.*",
        "kanban",
        "string",
        "The card, its board, the profile assigned to it and the attempt (`run_id` as a string); the join across the dispatcher's and the worker's traces",
    ),
    Row(
        ("hermes.kanban.worker_pid",),
        "kanban.*",
        "kanban",
        "int",
        "The worker process (`kanban.spawn`, `kanban.worker_exit`, `kanban.stale_claim`)",
    ),
    Row(
        ("hermes.kanban.exit_kind", "hermes.kanban.exit_code", "hermes.kanban.retry_status"),
        "kanban.*",
        "kanban",
        "string, int, string",
        "How the worker process ended (`clean_exit` · `rate_limited` · `nonzero_exit` · `signaled` · `unknown`), its exit code, and what the dispatcher does next (`kanban.worker_exit`; `retry_status` also on `kanban.stale_claim`)",
    ),
    Row(
        ("hermes.kanban.heartbeat_stale",),
        "kanban.*",
        "kanban",
        "bool",
        "Whether the reclaimed claim had stopped heart-beating (`kanban.stale_claim`)",
    ),
    Row(
        ("hermes.kanban.outcome",),
        "kanban.*",
        "kanban",
        "string",
        "`completed` · `blocked` on the worker-side spans and on the worker's `agent` root; the dispatcher's own outcome word on `kanban.worker_exit` (`crashed`, `timed_out`, …)",
    ),
    Row(
        ("hermes.kanban.summary", "hermes.kanban.reason"),
        "kanban.*",
        "kanban",
        "string",
        "The worker's hand-off summary (`kanban.complete`) or block reason (`kanban.block`), preview-clipped; absent when `capture_previews=false`",
    ),
    # ── subagent.* ──
    Row(
        ("gen_ai.operation.name", "gen_ai.agent.name"),
        "subagent.*",
        "model",
        "string",
        "`invoke_agent` / child role (`gen_ai.agent.name` absent when no role was reported)",
    ),
    Row(
        ("hermes.subagent.role", "hermes.subagent.goal"),
        "subagent.*",
        "subagent",
        "string",
        "Child role (only when Hermes reports one; the span is then `subagent.<role>`, else `subagent`) and delegated goal (preview)",
    ),
    Row(("input.value",), "subagent.*", "content", "string", "The goal preview"),
    Row(
        ("hermes.subagent.child_session_id", "hermes.subagent.child_id"),
        "subagent.*",
        "subagent",
        "string",
        "Child session / sub-agent ids",
    ),
    Row(
        (
            "hermes.subagent.parent_session_id",
            "hermes.subagent.parent_turn_id",
            "hermes.subagent.parent_id",
        ),
        "subagent.*",
        "subagent",
        "string",
        "Parent identity",
    ),
    Row(
        (
            "hermes.subagent.status",
            "hermes.subagent.duration_ms",
            "hermes.subagent.summary",
            "output.value",
        ),
        "subagent.*",
        "subagent",
        "mixed",
        "On stop: reported `child_status`, wall-clock, result summary",
    ),
    Row(
        ("hermes.subagent.model_inherited",),
        "subagent.*",
        "subagent",
        "bool",
        "Set when the child's root starts, with `gen_ai.request.model` (the child's model): whether the child runs on its parent's model (`false` with `delegation.model` set)",
    ),
    Row(
        (
            "hermes.subagent.usage.input_tokens",
            "hermes.subagent.usage.output_tokens",
            "hermes.subagent.usage.total_tokens",
            "hermes.subagent.api_call_count",
        ),
        "subagent.*",
        "subagent",
        "int",
        "On stop: tokens and API calls of the child's whole subtree (its own turns plus every delegation it made); namespaced so the child root's `gen_ai.usage.*` is not counted twice by a reader that sums agent spans",
    ),
    Row(
        ("hermes.subagent.cost.usage", "hermes.subagent.cost.status"),
        "subagent.*",
        "subagent",
        "mixed",
        "On stop: the subtree's USD and pricing status, by the turn root's rule (an amount only when every call was priced)",
    ),
    Row(
        ("hermes.subagent.swept",),
        "subagent.*",
        "subagent",
        "bool",
        "`true` when the delegation was closed by its TTL (`subagent_span_ttl_ms`) because `subagent_stop` never arrived",
    ),
    # ── event ──
    Row(
        ("hermes.turn.duration_s",),
        "event",
        "turn",
        "",
        "Turn wall-clock in seconds on the `hermes.turn.end` event",
    ),
    Row(
        ("hermes.tool.duration_s",),
        "event",
        "tool",
        "",
        "Tool wall-clock in seconds on the `hermes.tool.call` event (spans carry ms)",
    ),
    Row(
        ("hermes.tool.name",),
        "event",
        "tool",
        "",
        "Tool name as the `hermes.tool.call` event carries it",
    ),
    Row(
        ("hermes.approval.decision",),
        "event",
        "approval",
        "",
        "Event name of an approval decision, as some backends store it as an attribute",
    ),
    Row(
        ("hermes.api.error",),
        "event",
        "error",
        "",
        "Event name of a failed API request, as some backends store it as an attribute",
    ),
    # ── log record ──
    Row(
        ("exception.stacktrace",),
        "log record",
        "error",
        "",
        "Stack trace of a forwarded log record with `exc_info`",
    ),
    Row(
        ("code.file.path",),
        "log record",
        "log",
        "",
        "Source location of a forwarded log record (OTel semconv)",
    ),
    Row(
        ("code.filepath",),
        "log record",
        "log",
        "",
        "Source location of a forwarded log record (pre-1.33 spelling)",
    ),
    Row(
        ("code.function",),
        "log record",
        "log",
        "",
        "Function of a forwarded log record (pre-1.33 spelling)",
    ),
    Row(
        ("code.function.name",),
        "log record",
        "log",
        "",
        "Function of a forwarded log record (OTel semconv)",
    ),
    Row(
        ("code.line.number",),
        "log record",
        "log",
        "",
        "Line of a forwarded log record (OTel semconv)",
    ),
    Row(
        ("code.lineno",),
        "log record",
        "log",
        "",
        "Line of a forwarded log record (pre-1.33 spelling)",
    ),
    Row(
        ("hermes.log.attribution",),
        "log record",
        "log",
        "",
        "How a forwarded log record was tied to a turn: `context` (an open span) or `session_tag`",
    ),
    # ── backend ──
    Row(
        ("llm.input_messages",),
        "backend",
        "content",
        "",
        "OpenInference message list some backends derive from `gen_ai.input.messages`",
    ),
    Row(
        ("llm.output.content",),
        "backend",
        "content",
        "",
        "OpenInference response text some backends derive from `output.value`",
    ),
    Row(
        ("llm.output.tool_calls",),
        "backend",
        "content",
        "",
        "OpenInference tool-call list some backends derive from `gen_ai.output.messages`",
    ),
    Row(
        ("llm.system_prompt",),
        "backend",
        "content",
        "",
        "OpenInference system prompt some backends derive from `gen_ai.system_instructions`",
    ),
    Row(
        ("gen_ai.tool.type",),
        "backend",
        "tool",
        "",
        "Tool kind (`function` / `extension`) when a backend reports it",
    ),
)


@dataclass(frozen=True)
class Attr:
    """One attribute the plugin may set: its home row's ``group``, ``doc`` and
    ``where`` (the first :class:`Row` that lists the name)."""

    name: str
    group: str
    doc: str = ""
    where: str = ""


def _attributes(rows: Sequence[Row]) -> Tuple[Attr, ...]:
    out: Dict[str, Attr] = {}
    for row in rows:
        for name in row.names:
            out.setdefault(name, Attr(name, row.group, row.doc, row.where))
    return tuple(out.values())


ATTRIBUTES: Tuple[Attr, ...] = _attributes(ROWS)

ATTRIBUTE_NAMES: Tuple[str, ...] = tuple(a.name for a in ATTRIBUTES)
_BY_NAME: Dict[str, Attr] = {a.name: a for a in ATTRIBUTES}


def attribute(name: str) -> Optional[Attr]:
    """The registry entry for ``name``, or ``None``."""
    return _BY_NAME.get(name)


def groups() -> Dict[str, List[Attr]]:
    """``{group: [Attr, ...]}`` in registry order."""
    out: Dict[str, List[Attr]] = {}
    for a in ATTRIBUTES:
        out.setdefault(a.group, []).append(a)
    return out


# ── Fallback chains: where a reader finds a value, first match wins ──────
#
# Both conventions are emitted side by side; a backend may keep one, both,
# or flatten the names. ``first(attrs, KEYS)`` is the one lookup rule.

SESSION_KEYS: Tuple[str, ...] = ("hermes.session_id", "session.id", "session_id")
# Backend search cards may carry a vendor's own session key too.
CARD_SESSION_KEYS: Tuple[str, ...] = (
    "hermes.session_id",
    "langfuse.sessionId",
    "session.id",
    "session_id",
)
MODEL_KEYS: Tuple[str, ...] = ("gen_ai.request.model", "llm.model_name", "gen_ai.response.model")
PROVIDER_KEYS: Tuple[str, ...] = ("gen_ai.provider.name", "llm.provider", "gen_ai.system")
INPUT_TOKEN_KEYS: Tuple[str, ...] = ("gen_ai.usage.input_tokens", "llm.token_count.prompt")
OUTPUT_TOKEN_KEYS: Tuple[str, ...] = ("gen_ai.usage.output_tokens", "llm.token_count.completion")
TOTAL_TOKEN_KEYS: Tuple[str, ...] = ("gen_ai.usage.total_tokens", "llm.token_count.total")
REASONING_TOKEN_KEYS: Tuple[str, ...] = (
    "gen_ai.usage.reasoning.output_tokens",
    "llm.token_count.completion_details.reasoning",
)
CACHE_READ_TOKEN_KEYS: Tuple[str, ...] = (
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_read_input_tokens",
    "llm.token_count.prompt_details.cache_read",
)
FINISH_REASON_KEYS: Tuple[str, ...] = (
    "gen_ai.response.finish_reasons",
    "llm.response.finish_reason",
)
INPUT_CONTENT_KEYS: Tuple[str, ...] = (
    "input.value",
    "gen_ai.input.messages",
    "gen_ai.tool.call.arguments",
)
OUTPUT_CONTENT_KEYS: Tuple[str, ...] = (
    "output.value",
    "gen_ai.output.messages",
    "gen_ai.tool.call.result",
)
COST_KEYS: Tuple[str, ...] = ("hermes.cost.usage",)
COST_KEY = COST_KEYS[0]
COST_STATUS_KEY = "hermes.cost.status"
# The turn's totals live on the root; a matched api span must not pass for them.
TURN_TOTAL_KEYS: Tuple[str, ...] = (
    "gen_ai.usage.total_tokens",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "llm.token_count.total",
    "llm.token_count.prompt",
    "llm.token_count.completion",
    "hermes.cost.usage",
)
TURN_NUMBER_KEY = "hermes.turn.number"
PLATFORM_KEY = "hermes.platform"
QUERY_SOURCE_KEY = "hermes.query_source"
# Its values: which loop an ``api.*`` span belongs to.
QUERY_SOURCE_MAIN = "main"
QUERY_SOURCE_SUBAGENT = "subagent"
QUERY_SOURCE_AUXILIARY = "auxiliary"
ERROR_TYPE_KEY = "error.type"
TOOL_NAME_KEY = "tool.name"


def first(attrs: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """The first non-empty value of ``keys`` in ``attrs`` (``None`` / ``""`` skip)."""
    for k in keys:
        v = attrs.get(k)
        if v is not None and v != "":
            return v
    return None


# ── Span kinds ────────────────────────────────────────────────────────────
#
# The dashboard's icon and colour per span, the live store's ``kind=`` filter
# and the query CLI agree on this table. A kind is recognised by the span
# name's prefix (``exact`` names match whole), except where the span carries
# ``hermes.span_kind`` (skill, approval and human_input spans, whose names
# vary). Order matters: the first rule that matches wins, and ``other`` is
# none of them.

KIND_OVERRIDE_ATTR = "hermes.span_kind"
KIND_OVERRIDES: Tuple[str, ...] = ("skill", "approval", "human_input")


@dataclass(frozen=True)
class KindRule:
    kind: str
    prefixes: Tuple[str, ...] = ()
    exact: Tuple[str, ...] = ()


SPAN_KINDS: Tuple[KindRule, ...] = (
    KindRule("agent", prefixes=("agent.",), exact=("agent",)),
    KindRule("cron", prefixes=("cron",)),
    KindRule("session", prefixes=("session",)),
    KindRule("skill", prefixes=("skill.",)),
    KindRule("approval", prefixes=("approval",)),
    KindRule("human_input", prefixes=("human_input.",)),
    KindRule("subagent", prefixes=("subagent",)),
    KindRule("llm", prefixes=("llm.",)),
    KindRule("api", prefixes=("api.",)),
    KindRule("tool", prefixes=("tool.",)),
)
KINDS: Tuple[str, ...] = tuple(r.kind for r in SPAN_KINDS) + ("other",)
# Name prefixes per kind, as ``LIKE`` patterns need them (an exact name is its own prefix).
KIND_PREFIXES: Dict[str, Tuple[str, ...]] = {
    r.kind: tuple(dict.fromkeys(r.exact + r.prefixes)) for r in SPAN_KINDS
}


def span_kind(name: str, attrs: Optional[Mapping[str, Any]] = None) -> str:
    """The kind of a span from its name and attributes (``kindOf`` in ``lib.ts``)."""
    a = attrs or {}
    override = a.get(KIND_OVERRIDE_ATTR)
    if override in KIND_OVERRIDES:
        return str(override)
    n = (name or "").lower()
    for rule in SPAN_KINDS:
        if n in rule.exact or any(n.startswith(p) for p in rule.prefixes):
            return rule.kind
    return "other"


# Names the generated TypeScript test replays through ``kindOf`` so the two
# classifiers cannot drift (``scripts/gen_semconv.py`` records the answers).
KIND_EXAMPLES: Tuple[Tuple[str, Dict[str, str]], ...] = (
    ("agent", {}),
    ("agent.main", {}),
    ("agentic-thing", {}),
    ("cron", {}),
    ("cron.nightly", {}),
    ("session", {}),
    ("session:abc", {}),
    ("skill.observability", {}),
    ("tool.skill_view", {KIND_OVERRIDE_ATTR: "skill"}),
    ("approval", {}),
    ("approval.rm_rf", {}),
    ("tool.terminal", {KIND_OVERRIDE_ATTR: "approval"}),
    ("human_input.clarify", {}),
    ("human_input.approval", {KIND_OVERRIDE_ATTR: "human_input"}),
    ("subagent", {}),
    ("subagent.researcher", {}),
    ("llm.gpt-4", {}),
    ("api.gpt-4", {}),
    ("api.error", {}),
    ("tool.terminal", {}),
    ("tool.read_file", {KIND_OVERRIDE_ATTR: "nonsense"}),
    ("kanban.claim", {}),
    ("MCP send ping", {}),
    ("", {}),
)


# ── Events (log records with an ``event_name``) ───────────────────────────

EVENT_TURN_START = "hermes.turn.start"
EVENT_TURN_END = "hermes.turn.end"
EVENT_TOOL_CALL = "hermes.tool.call"
EVENT_APPROVAL_DECISION = "hermes.approval.decision"
EVENT_API_ERROR = "hermes.api.error"
EVENT_SUBAGENT_START = "hermes.subagent.start"
EVENT_SUBAGENT_STOP = "hermes.subagent.stop"
EVENT_SESSION_FINALIZE = "hermes.session.finalize"
EVENT_KANBAN_TRANSITION = "hermes.kanban.transition"
EVENT_AUXILIARY_CALL = "hermes.auxiliary.call"
EVENT_INFERENCE_DETAILS = "gen_ai.client.inference.operation.details"
EVENT_NAMES: Tuple[str, ...] = (
    EVENT_TURN_START,
    EVENT_TURN_END,
    EVENT_TOOL_CALL,
    EVENT_APPROVAL_DECISION,
    EVENT_API_ERROR,
    EVENT_SUBAGENT_START,
    EVENT_SUBAGENT_STOP,
    EVENT_SESSION_FINALIZE,
    EVENT_KANBAN_TRANSITION,
    EVENT_AUXILIARY_CALL,
    EVENT_INFERENCE_DETAILS,
)


def unknown_names(names: Iterable[str]) -> List[str]:
    """Those of ``names`` that are not registered (for the drift tests)."""
    return sorted(n for n in set(names) if n not in _BY_NAME)
