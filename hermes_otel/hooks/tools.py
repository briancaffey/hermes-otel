"""Tool hooks (``tool.*`` spans) and the skill execution-window spans they open."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Mapping, Optional, Tuple

from .. import log_events as EV
from ..debug_utils import debug_log
from ..helpers import serialize_full, truncate_string
from ..redaction import redact_text
from ._common import (
    _emit_event,
    _fail_open,
    _mark_truncated,
    _preview_marked,
    _resolve_session_id,
    get_tracer,
)
from .attributes import (
    _ID_MAX,
    _gen_ai_attributes,
    _session_context_attributes,
    _session_identity_attributes,
    _tool_host_utilization_attributes,
)
from .skills import detect_skill, resolve_skill_dir

# ── Tool identity ──────────────────────────────────────────────────────────

_TARGET_KEYS = ("path", "file_path", "target", "url", "uri")
_COMMAND_KEYS = ("command", "cmd")


def resolve_tool_identity(args: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """Extract (target, command) from tool arguments.

    target: first non-empty value under path/file_path/target/url/uri.
    command: first non-empty value under command/cmd.
    """
    if not isinstance(args, dict):
        return None, None

    target: Optional[str] = None
    for k in _TARGET_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            target = v.strip()
            break

    command: Optional[str] = None
    for k in _COMMAND_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            command = v.strip()
            break

    return target, command


# ── Commits (#478) ─────────────────────────────────────────────────────────

# Hermes's terminal tool, plus the names a shell tool goes by in other hosts
# that route through the same hooks. The command itself comes from the
# ``command`` / ``cmd`` argument (``resolve_tool_identity``).
_SHELL_TOOL_NAMES = frozenset({"terminal", "bash", "shell"})
# ``git commit`` as a word, however the command is prefixed (``cd x &&``,
# ``GIT_X=1``, a second space); ``git commit-tree`` and ``git committed`` are
# not it. The pattern opencode-plugin-otel uses, so both agents count alike.
_GIT_COMMIT = re.compile(r"\bgit\s+commit(?![-\w])")
# git's own confirmation line: ``[main abc1234] Subject`` or, for the first
# commit, ``[main (root-commit) abc1234] Subject``.
_COMMIT_SUMMARY = re.compile(r"^\[[^\]\n]+ [0-9a-f]{7,40}\] (?P<subject>[^\n]*)", re.MULTILINE)
_COMMIT_SUBJECT_MAX = 200


def is_git_commit(tool_name: str, command: Optional[str]) -> bool:
    """Whether a shell tool call ran ``git commit`` (by name, not by outcome)."""
    return (
        bool(command) and tool_name in _SHELL_TOOL_NAMES and _GIT_COMMIT.search(command) is not None
    )


def exit_code(result: Mapping[str, Any]) -> Optional[int]:
    """The command's exit code from the terminal result envelope (``exit_code``,
    or ``returncode`` as the environment layer spells it); None when absent."""
    for key in ("exit_code", "returncode"):
        value = result.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def commit_succeeded(outcome: str, result: Mapping[str, Any]) -> bool:
    """The commit ran and exited 0. Hermes reports the exit code in the
    terminal envelope; a result without one counts when nothing else reported
    a failure (``outcome`` not in ``FAILURE_OUTCOMES``)."""
    if outcome in FAILURE_OUTCOMES:
        return False
    code = exit_code(result)
    return code is None or code == 0


def commit_subject(output: Any) -> Optional[str]:
    """First line of the commit message, from git's ``[branch sha] subject``
    confirmation in the tool output; None when there is none. Redacted and
    clipped, so a secret pasted into a commit message never leaves as is."""
    if not isinstance(output, str) or not output:
        return None
    m = _COMMIT_SUMMARY.search(output)
    if m is None:
        return None
    subject = m.group("subject").strip()
    if not subject:
        return None
    return truncate_string(redact_text(subject), _COMMIT_SUBJECT_MAX)


# ── Tool outcome ───────────────────────────────────────────────────────────


def extract_tool_result_status(result: Any) -> Optional[str]:
    """Derive an outcome label from a tool-call result.

    Preference order, first match wins:
      1. result["status"] (string, lowercased)
      2. result["error"] is non-empty → "error"
      3. result["timeout"] truthy → "timeout"
      4. result["blocked"] truthy → "blocked"

    Returns None when nothing is derivable; caller then emits "completed".
    """
    if not isinstance(result, dict):
        return None

    status = result.get("status")
    if isinstance(status, str) and status.strip():
        return status.strip().lower()

    err = result.get("error")
    if err and str(err).strip():
        return "error"

    if result.get("timeout"):
        return "timeout"
    if result.get("blocked"):
        return "blocked"

    return None


# Hermes ``status`` kwarg value -> documented ``hermes.tool.outcome`` value.
# Success statuses are deliberately absent: they defer to the tool's own
# result-reported status (see ``outcome_from_hook_status``).
_HOOK_STATUS_SUCCESS = frozenset({"ok", "success", "completed", "complete", "done"})
HOOK_STATUS_TO_OUTCOME = {
    "error": "error",
    "failed": "error",
    "blocked": "blocked",
    "timeout": "timeout",
    "timed_out": "timeout",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}
# Every outcome that counts as "the tool did not complete"; one definition
# for the skill-load check and any future consumer.
FAILURE_OUTCOMES = frozenset(HOOK_STATUS_TO_OUTCOME.values())


def outcome_from_hook_status(status: Any) -> Optional[str]:
    """Translate Hermes' post_tool_call ``status`` into the outcome taxonomy.

    Non-success lifecycle statuses (``timeout``, ``blocked``, ``cancelled``,
    ``error``) are authoritative and win. Returns ``None`` for a success status
    or when the hook carried no usable status, so the caller falls back to the
    tool's own result-reported status (else ``completed``) — that keeps custom
    outcomes such as ``partial`` that only the tool knows about.
    """
    if not isinstance(status, str):
        return None
    key = status.strip().lower()
    if not key or key in _HOOK_STATUS_SUCCESS:
        return None
    return HOOK_STATUS_TO_OUTCOME.get(key, key)


# Governance floors (approvals.deny globs, the hardline list, the stdin
# password guard) never fire the approval hooks — Hermes' terminal tool
# collapses the block into a ``{"error": "BLOCKED: ...", "status":
# "blocked"}`` envelope before post_tool_call sees it, so the message text
# is the only provenance surface. Matching is deliberately conservative:
# only messages we can POSITIVELY classify yield a floor, everything else
# returns None (no attributes). Hermes prefixes many non-floor outcomes
# with ``BLOCKED:`` too (human denials, approval timeouts, notify
# failures, gateway refusals) — guessing those into floors would corrupt
# governance dashboards with false attribution.
_BLOCK_PROVENANCE_SIGNATURES = (
    ("user-defined deny rule", "deny_rule"),
    ("hardline", "hardline"),
    ("Do not pipe passwords", "stdin_password_guard"),
)


def classify_block_provenance(block_message: Any) -> Optional[str]:
    """Return which governance floor blocked a command, or None.

    ``block_message`` is the terminal error envelope's ``error``/``output``
    text. Only signatures we positively recognize match — deny-rule globs,
    the hardline list, and the stdin password guard. Unclassifiable
    ``BLOCKED`` texts (human denials, approval timeouts, plugin or
    guardrail vetoes — which surface as plain errors anyway) return None,
    so the caller emits no provenance rather than wrong provenance.
    """
    if not isinstance(block_message, str) or not block_message.strip():
        return None
    lowered = block_message.lower()
    if "blocked" not in lowered:
        return None
    for signature, blocked_by in _BLOCK_PROVENANCE_SIGNATURES:
        if signature.lower() in lowered:
            return blocked_by
    return None


def char_count(value: Any) -> Optional[int]:
    """Length in characters of a hook payload value; ``None`` for ``None``.

    Strings count as they are; anything else counts its ``str()`` form, so a
    structured result still yields a size instead of raising.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return len(value)
    try:
        return len(str(value))
    except Exception:  # pragma: no cover - defensive
        return None


# Hermes' head+tail limit leaves this notice inside the kept text
# (``tools/tool_output_truncate.truncation_notice``); the capture-window spill
# path instead adds ``output_total_chars`` next to ``output`` in the result.
_TRUNCATION_NOTICE = re.compile(
    r"\[[A-Z]+ TRUNCATED - ([\d,]+) [a-z]+ omitted out of ([\d,]+) total\]"
)


def terminal_truncation(result: Mapping[str, Any]) -> Tuple[bool, Optional[int]]:
    """``(truncated, dropped_chars)`` from a terminal tool's result envelope.

    Reads only what Hermes itself records about its output limits: the
    ``output_total_chars`` field the capture-window spill adds, or the
    ``[OUTPUT TRUNCATED - N chars omitted out of M total]`` notice the
    head+tail limit writes into the kept text. Whitespace stripping, ANSI
    removal and redaction also shrink the output, so a bare size comparison
    would call those truncation; this never does.
    """
    output = result.get("output")
    output_str = output if isinstance(output, str) else ("" if output is None else str(output))
    total = result.get("output_total_chars")
    if isinstance(total, int) and not isinstance(total, bool):
        return True, max(total - len(output_str), 0)
    m = _TRUNCATION_NOTICE.search(output_str)
    if m:
        try:
            return True, int(m.group(1).replace(",", ""))
        except ValueError:  # pragma: no cover - the regex only admits digits
            return True, None
    return False, None


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


def _tool_session_attributes(tracer, session_id: str, kwargs: dict) -> Dict[str, Any]:
    """What both tool spans carry for their session: its identity, the
    ``execute_tool`` GenAI operation and the session context."""
    return {
        **_session_identity_attributes(session_id),
        **_gen_ai_attributes(session_id, "execute_tool"),
        **_session_context_attributes(tracer, session_id, kwargs),
    }


def _record_skill_load(
    tracer, session_id: str, summary, tool_name: str, args: Any, outcome: str, result_json: dict
) -> None:
    """A skill loaded by this tool call joins the turn summary and (opt-in)
    opens its execution-window span, once the load is known to have succeeded.

    ``skill_view`` reports success explicitly; a file read instead succeeds
    unless Hermes reports a terminal failure through the hook/result.
    """
    skill, skill_source = detect_skill(tool_name, args)
    if not skill or not skill_source:
        return
    succeeded = (
        result_json.get("success") is True
        if skill_source == "skill_view"
        else outcome not in FAILURE_OUTCOMES
    )
    if not succeeded:
        return
    summary.add_skill(skill)
    if tracer.config.skill_spans:
        _open_skill_span(
            tracer, session_id, skill, skill_source, _skill_dir_for(skill_source, args, result_json)
        )


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
    tracer.spans.mark_started(key)

    # OpenInference attributes — Phoenix Info panel
    attributes: Dict[str, Any] = {
        "tool.name": tool_name,
        "gen_ai.tool.name": tool_name,
        "gen_ai.tool.call.id": truncate_string(tool_call_id, _ID_MAX),
    }
    preview, original = _preview_marked(tracer, "tool_input", serialize_full(args) or "{}")
    if preview is not None:
        attributes["input.value"] = preview
        attributes["gen_ai.tool.call.arguments"] = preview
        _mark_truncated(attributes, "input", original)
    if tool_name.startswith("mcp_") and tracer.config.captures_full_prompts():
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
        attributes.update(_tool_session_attributes(tracer, session_id, kwargs))
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

    start_time = tracer.spans.pop_started(key)
    ended_at = time.perf_counter()
    duration_ms: Optional[float] = None
    if start_time:
        duration_ms = (ended_at - start_time) * 1000
        tracer.record_metric(
            "tool_duration", duration_ms, {"tool_name": tool_name, "gen_ai.tool.name": tool_name}
        )
        tracer.record_metric(
            "tool_duration_s", duration_ms / 1000.0, {"gen_ai.tool.name": tool_name}
        )

    # Build final attributes — OpenInference conventions for Phoenix Info
    attributes: Dict[str, Any] = {
        "gen_ai.tool.name": tool_name,
        "gen_ai.tool.call.id": truncate_string(tool_call_id, _ID_MAX),
    }
    if start_time:
        attributes.update(_tool_host_utilization_attributes(tracer, start_time, ended_at))

    result_json = _parse_result(result)

    # Size of the result the model will see (#33). For the terminal tool the
    # transform_terminal_output observer recorded the raw output size; whether
    # Hermes' output limit then cut it is read from Hermes' own markers.
    result_chars = char_count(result)
    if result_chars is not None:
        attributes["hermes.tool.result.chars"] = result_chars
    raw_chars = tracer.spans.pop_tool_output_chars(key)
    truncated, dropped = terminal_truncation(result_json)
    if raw_chars is not None or truncated:
        # Only Hermes' own record of its limits counts: whitespace stripping,
        # ANSI removal and redaction also shrink the output, so a bare
        # raw-vs-kept comparison would report those as truncation.
        attributes["hermes.terminal.output.truncated"] = truncated
        if truncated and dropped:
            attributes["hermes.terminal.output.dropped_chars"] = dropped

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
    if tool_name.startswith("mcp_") and tracer.config.captures_full_responses():
        serialized_result = serialize_full(result_json if result_json else result)
        if serialized_result is not None:
            attributes["gen_ai.tool.call.result"] = serialized_result

    # Summary roll-up
    session_id = _resolve_session_id(kwargs)
    if session_id:
        attributes.update(_tool_session_attributes(tracer, session_id, kwargs))
        summary = tracer.sessions.get_or_create(session_id).turn_summary
        summary.add_outcome(outcome)
        _record_skill_load(tracer, session_id, summary, tool_name, args, outcome, result_json)

    # Map outcome to span status. Only "error" is ERROR; other non-ok outcomes
    # (timeout, blocked, ...) are OK to avoid polluting error rates.
    status = "error" if has_error else "ok"
    tool_span = tracer.spans.get_span(key)
    _emit_event(
        tracer,
        EV.TOOL_CALL,
        f"tool {tool_name} {outcome}",
        {
            **{k: v for k, v in attributes.items() if k.startswith(EV.TOOL_PREFIXES)},
            EV.ATTR_TOOL_DURATION_S: (
                round(duration_ms / 1000.0, 3) if duration_ms is not None else None
            ),
        },
        severity=(
            "ERROR"
            if outcome == "error"
            else ("WARN" if outcome in ("blocked", "timeout") else "INFO")
        ),
        session_id=session_id,
        span=tool_span,
    )
    _record_commit(tracer, tool_name, args, outcome, result_json, attributes, session_id, tool_span)
    tracer.end_span(
        key, attributes=attributes, status=status, error_message=error_msg if has_error else None
    )
    debug_log(f"  span ended: status={status}, outcome={outcome}")


def _record_commit(
    tracer,
    tool_name: str,
    args: Any,
    outcome: str,
    result_json: Dict[str, Any],
    attributes: Dict[str, Any],
    session_id: Optional[str],
    tool_span: Any,
) -> None:
    """A successful ``git commit`` through a shell tool marks the tool span
    (``hermes.tool.git_commit``), counts ``hermes.commit.count`` by the
    session's platform and model, and emits the ``commit`` event with the
    message's first line when git printed it (#478). Failed commits, other
    tools and other commands leave nothing behind.
    """
    _, command = resolve_tool_identity(args)
    if not is_git_commit(tool_name, command) or not commit_succeeded(outcome, result_json):
        return
    attributes["hermes.tool.git_commit"] = True
    ps = tracer.sessions.peek(session_id) if session_id else None
    labels: Dict[str, Any] = {"platform": (ps.platform if ps else "") or "unknown"}
    if ps is not None and ps.model:
        labels["model"] = ps.model
    tracer.record_metric("commit_count", 1, labels)
    _emit_event(
        tracer,
        EV.COMMIT,
        "commit",
        {
            "gen_ai.tool.name": tool_name,
            "gen_ai.tool.call.id": attributes.get("gen_ai.tool.call.id"),
            EV.ATTR_COMMIT_SUBJECT: commit_subject(result_json.get("output")),
        },
        session_id=session_id,
        span=tool_span,
    )
    debug_log(f"  commit recorded: tool={tool_name}")
