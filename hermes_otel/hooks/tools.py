"""Tool hooks (``tool.*`` spans) and the skill execution-window spans they open."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from ..debug_utils import debug_log
from ..helpers import (
    FAILURE_OUTCOMES,
    classify_block_provenance,
    detect_skill,
    extract_tool_result_status,
    outcome_from_hook_status,
    resolve_skill_dir,
    resolve_tool_identity,
    serialize_full,
    truncate_string,
)
from ._common import (
    _fail_open,
    _mark_truncated,
    _preview_marked,
    _resolve_session_id,
    get_tracer,
)
from .attributes import (
    _gen_ai_attributes,
    _session_context_attributes,
    _session_identity_attributes,
    _tool_host_utilization_attributes,
)


def _open_skill_span(
    tracer, session_id: str, skill: str, source: str, path: Optional[str] = None
) -> None:
    """Open an overlapping skill execution-window span (idempotent per turn).

    A skill is loaded once (via ``skill_view`` or a ``/skills/`` read) and then
    guides the rest of the turn, so the span opens here and is closed at the
    turn boundary in :func:`on_session_end`. Skills overlap freely — each gets
    its own span keyed by name, nested under the turn root rather than the
    in-flight tool/LLM span. ``path`` is the skill directory when Hermes or the
    referenced file told us where it is; the attribute is omitted otherwise
    rather than guessed from the name (#147).
    """
    if tracer.spans.has_skill_span(session_id, skill):
        return  # already active this turn — keep the first window open
    key = f"skill:{session_id}:{skill}"
    attributes: Dict[str, Any] = {
        "hermes.skill.name": skill,
        "hermes.skill.source": source,
        "hermes.span_kind": "skill",
        "gen_ai.skill.name": skill,
    }
    if path:
        attributes["hermes.skill.path"] = truncate_string(path, 500)
    attributes.update(_gen_ai_attributes(session_id, "execute_skill"))
    tracer.start_span(
        name=f"skill.{skill}",
        key=key,
        kind="general",
        attributes=attributes,
        session_id=session_id,
        parent=tracer.spans.get_session_root(session_id),
    )
    tracer.spans.register_skill_span(session_id, skill, key)
    debug_log(f"  skill span opened: {skill} (source={source})")


# Result-reported statuses that are more specific than Hermes' coarse
# ``error`` hook status. Hermes' terminal tool returns every governance block
# (hard floors, human denials, approval timeouts) as
# ``{"error": "BLOCKED: ...", "status": "blocked"}`` and then derives hook
# ``status="error"`` from the ``error`` key; the explicit ``status`` field is
# the more precise signal and must win (#106).
_SPECIFIC_NON_SUCCESS_OUTCOMES = frozenset({"blocked", "timeout", "cancelled"})


def _resolve_tool_outcome(hook_status: Any, result_json: Any) -> str:
    """Combine Hermes' lifecycle ``status`` with the tool's own result status.

    Precedence:

    1. A *specific* non-success hook status (``timeout`` / ``blocked`` /
       ``cancelled``) is authoritative — the result may be plain text there
       (#72).
    2. A coarse hook ``error`` yields to an explicit, more specific status the
       tool reported in its result (``blocked`` / ``timeout`` / ``cancelled``),
       so governance blocks are not counted as errors (#106); otherwise
       ``error``.
    3. A success status (or no status, on older Hermes) defers to the result's
       own status, else ``completed``.
    """
    hook_outcome = outcome_from_hook_status(hook_status)
    result_outcome = extract_tool_result_status(result_json)
    if hook_outcome == "error" and result_outcome in _SPECIFIC_NON_SUCCESS_OUTCOMES:
        return result_outcome
    if hook_outcome:
        return hook_outcome
    return result_outcome or "completed"


def _skill_dir_for(source: str, args: Any, result_json: Dict[str, Any]) -> Optional[str]:
    """Where the loaded skill lives, from evidence only.

    ``skill_view`` reports the resolved ``skill_dir`` in its result (Hermes
    ``tools/skills_tool.py``); a path-match load resolves the directory from
    the file the tool actually read. None when neither says.
    """
    if source == "skill_view":
        reported = result_json.get("skill_dir")
        return reported if isinstance(reported, str) and reported.strip() else None
    return resolve_skill_dir(args)


def _tool_call_id(task_id: str, kwargs: dict) -> str:
    """Use Hermes's tool-call id when available, with old-core fallback."""
    return str(kwargs.get("tool_call_id") or task_id)


def _parse_result(result: Any) -> Dict[str, Any]:
    """The tool result as a dict (``{}`` for scalars, lists and unparsable text)."""
    if isinstance(result, dict):
        return result
    try:
        parsed = json.loads(result) if isinstance(result, str) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    # A tool may legitimately return a JSON scalar or list ("42", [...]).
    return parsed if isinstance(parsed, dict) else {}


@_fail_open
def on_pre_tool_call(tool_name: str, args: dict, task_id: str, **kwargs):
    """Start a tool span before the tool executes."""
    tool_name = str(tool_name or "")
    debug_log(f"pre_tool_call fired: tool={tool_name}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    tracer.sweep_expired_turns()

    tool_call_id = _tool_call_id(task_id, kwargs)
    key = f"{tool_name}:{tool_call_id}"
    tracer.sessions.record_tool_start(key, time.perf_counter())

    # OpenInference attributes — Phoenix Info panel
    attributes: Dict[str, Any] = {
        "tool.name": tool_name,
        "gen_ai.tool.name": tool_name,
        "gen_ai.tool.call.id": truncate_string(tool_call_id, 200),
    }
    preview, original = _preview_marked(tracer, "tool_input", serialize_full(args) or "{}")
    if preview is not None:
        attributes["input.value"] = preview
        attributes["gen_ai.tool.call.arguments"] = preview
        _mark_truncated(attributes, "input", original)
    if (
        tracer.config.capture_previews
        and tool_name.startswith("mcp_")
        and tracer.config.capture_full_prompts
    ):
        serialized_args = serialize_full(args)
        if serialized_args is not None:
            attributes["gen_ai.tool.call.arguments"] = serialized_args

    # Richer identity — hermes.tool.* (opt-in namespace)
    target, command = resolve_tool_identity(args)
    if target:
        attributes["hermes.tool.target"] = truncate_string(target, 500)
    if command:
        attributes["hermes.tool.command"] = truncate_string(command, 500)
    skill, skill_source = detect_skill(tool_name, args)
    if skill:
        attributes["hermes.skill.name"] = skill
        attributes["hermes.skill.source"] = skill_source
        tracer.record_metric("skill_inferred", 1, {"skill_name": skill, "source": skill_source})

    # Summary roll-up (requires session_id to bucket into the right turn).
    session_id = _resolve_session_id(kwargs)
    if session_id:
        attributes.update(_session_identity_attributes(session_id))
        attributes.update(_gen_ai_attributes(session_id, "execute_tool"))
        attributes.update(_session_context_attributes(tracer, session_id, kwargs))
        summary = tracer.sessions.get_or_create(session_id).turn_summary
        summary.add_tool(tool_name)
        summary.add_target(target)
        summary.add_command(command)
        # ``summary.add_skill`` happens in on_post_tool_call, once the load is
        # known to have succeeded, so hermes.turn.skills agrees with the
        # skill.<name> spans.

    tracer.start_span(
        name=f"tool.{tool_name}",
        key=key,
        kind="tool",
        attributes=attributes,
        session_id=session_id or None,
    )
    debug_log(f"  span created: key={key}")


@_fail_open
def on_post_tool_call(tool_name: str, args: dict, result: str, task_id: str, **kwargs):
    """End the tool span and record the result."""
    tool_name = str(tool_name or "")
    debug_log(f"post_tool_call fired: tool={tool_name}")
    tracer = get_tracer()
    debug_log(f"  tracer.is_enabled={tracer.is_enabled}")
    if not tracer.is_enabled:
        return

    tool_call_id = _tool_call_id(task_id, kwargs)
    key = f"{tool_name}:{tool_call_id}"
    debug_log(f"  ending span: key={key}")

    start_time = tracer.sessions.pop_tool_start(key)
    ended_at = time.perf_counter()
    if start_time:
        duration_ms = (ended_at - start_time) * 1000
        tracer.record_metric(
            "tool_duration", duration_ms, {"tool_name": tool_name, "gen_ai.tool.name": tool_name}
        )

    # Build final attributes — OpenInference conventions for Phoenix Info
    attributes: Dict[str, Any] = {
        "gen_ai.tool.name": tool_name,
        "gen_ai.tool.call.id": truncate_string(tool_call_id, 200),
    }
    if start_time:
        attributes.update(_tool_host_utilization_attributes(tracer, start_time, ended_at))

    result_json = _parse_result(result)

    # Determine outcome taxonomy (see _resolve_tool_outcome for the rules).
    outcome = _resolve_tool_outcome(kwargs.get("status"), result_json)
    attributes["hermes.tool.outcome"] = outcome

    # Governance provenance: floor blocks (approvals.deny globs, hardline
    # list, stdin password guard) never fire the approval hooks — the
    # block is collapsed into this error envelope before post_tool_call
    # sees it. Positively classified floors carry who/what blocked on the
    # tool span; anything unclassifiable stays attribute-free rather than
    # being guessed into a floor (human denials, timeouts, plugin vetoes
    # are NOT floors). Correlate to the approval span via
    # gen_ai.tool.call.id; decided_by on approval spans keeps the
    # human-vs-smart-guardian distinction (#107).
    if outcome == "blocked":
        blocked_by = classify_block_provenance(
            result_json.get("error") or result_json.get("output") or ""
        )
        if blocked_by:
            attributes["hermes.tool.blocked_by"] = blocked_by
            attributes["hermes.tool.decided_by"] = "hard_floor"

    # Preserve existing error.message attribute when outcome == error
    has_error = outcome == "error"
    error_msg = ""
    if has_error:
        err_val = result_json.get("error")
        if err_val:
            error_msg = truncate_string(err_val, 500)
            attributes["error.message"] = error_msg

    # OpenInference output value — Phoenix shows this in Info
    preview, original = _preview_marked(tracer, "tool_output", result)
    if preview is not None:
        attributes["output.value"] = preview
        attributes["gen_ai.tool.call.result"] = preview
        _mark_truncated(attributes, "output", original)
    if (
        tracer.config.capture_previews
        and tool_name.startswith("mcp_")
        and tracer.config.capture_full_responses
    ):
        serialized_result = serialize_full(result_json if result_json else result)
        if serialized_result is not None:
            attributes["gen_ai.tool.call.result"] = serialized_result

    # Summary roll-up
    session_id = _resolve_session_id(kwargs)
    if session_id:
        attributes.update(_session_identity_attributes(session_id))
        attributes.update(_gen_ai_attributes(session_id, "execute_tool"))
        attributes.update(_session_context_attributes(tracer, session_id, kwargs))
        summary = tracer.sessions.get_or_create(session_id).turn_summary
        summary.add_outcome(outcome)
        skill, skill_source = detect_skill(tool_name, args)
        if skill and skill_source:
            # skill_view reports success explicitly; file reads instead succeed
            # unless Hermes reports a terminal failure through the hook/result.
            succeeded = (
                result_json.get("success") is True
                if skill_source == "skill_view"
                else outcome not in FAILURE_OUTCOMES
            )
            if succeeded:
                summary.add_skill(skill)
                if tracer.config.skill_spans:
                    _open_skill_span(
                        tracer,
                        session_id,
                        skill,
                        skill_source,
                        _skill_dir_for(skill_source, args, result_json),
                    )

    # Map outcome to span status. Only "error" is ERROR; other non-ok outcomes
    # (timeout, blocked, ...) are OK to avoid polluting error rates.
    status = "error" if has_error else "ok"
    tracer.end_span(
        key, attributes=attributes, status=status, error_message=error_msg if has_error else None
    )
    debug_log(f"  span ended: status={status}, outcome={outcome}")
