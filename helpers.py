"""Pure-function helpers shared across hermes-otel modules.

Kept separate from hooks.py so unit tests can import without pulling in
the OpenTelemetry SDK dependency tree.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

# Matches:
#   ESC [ ... letter             → CSI sequences (colors, cursor)
#   ESC ] ... BEL | ESC \        → OSC sequences (titles)
#   ESC <single byte>            → 7-bit C1 escapes
_ANSI_RE = re.compile(r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-_])")

_WHITESPACE_RE = re.compile(r"\s+")


def truncate_string(value: Any, max_len: int = 1000) -> str:
    """Safely stringify ``value`` and truncate with ``"..."`` suffix.

    Unlike :func:`clip_preview` this does **not** strip ANSI escapes or
    collapse whitespace — use it for identifiers (session_id, model,
    provider), error messages, and other non-display string fields
    where the raw content matters.

    Returns ``"<unserializable>"`` if ``str(value)`` raises.
    """
    try:
        text = str(value)
    except Exception:
        text = "<unserializable>"
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def clip_preview(text: Any, max_chars: int) -> Optional[str]:
    """Strip ANSI, collapse whitespace, and truncate a preview string.

    Returns None for None / empty / whitespace-only / ANSI-only input so the
    caller can omit the attribute entirely rather than emitting "".
    """
    if text is None:
        return None
    try:
        s = text if isinstance(text, str) else str(text)
    except Exception:
        return None
    if not s:
        return None
    s = _ANSI_RE.sub("", s)
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    s = _WHITESPACE_RE.sub(" ", s).strip()
    if not s:
        return None
    if max_chars <= 0:
        return None
    if len(s) <= max_chars:
        return s
    if max_chars <= 3:
        return "." * max_chars
    return f"{s[: max_chars - 3]}..."


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


# ── Skill name inference ───────────────────────────────────────────────────

# Matches /skills/<name>/ and /skills/<name>/SKILL.md etc.
# Deliberately does NOT match /optional-skills/<name>/references/ or similar
# overlapping directory layouts.
_SKILL_PATH_RE = re.compile(r"(?:^|[/\\])skills[/\\]([A-Za-z0-9_\-]+)(?:[/\\]|$)")


def infer_skill_name(args: Optional[Dict[str, Any]]) -> Optional[str]:
    """Infer a skill name from tool arguments (path-based).

    Matches /skills/<name>/ anywhere in the `path` / `file_path` / `target`
    argument. Returns None if no path present or no match.
    """
    if not isinstance(args, dict):
        return None
    for key in ("path", "file_path", "target"):
        v = args.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        name = infer_skill_name_from_text(v)
        if name:
            return name
    return None


def infer_skill_name_from_text(text: str) -> Optional[str]:
    """Extract a skill name from a free-form string containing a /skills/ path."""
    if not isinstance(text, str):
        return None
    match = _SKILL_PATH_RE.search(text)
    if not match:
        return None
    return match.group(1)


# ── Sub-agent / delegation ───────────────────────────────────────────────────

# child_status values hermes-agent reports on subagent_stop that indicate a
# clean finish. Anything explicitly failure-like maps to an error span; unknown
# / empty values default to OK so a missing status never inflates error rates
# (mirrors the tool-outcome policy in on_post_tool_call).
_SUBAGENT_OK_STATUSES = frozenset({"ok", "completed", "complete", "success", "succeeded", "done"})
_SUBAGENT_ERROR_STATUSES = frozenset(
    {"error", "errored", "failed", "failure", "cancelled", "canceled", "timeout", "timed_out"}
)


def subagent_span_key(child_session_id: Any) -> Optional[str]:
    """Build the span-tracking key for a delegated child agent.

    Keyed on ``child_session_id`` because that is the value the child's own
    ``on_session_start`` carries, so the child root span can rejoin the
    delegation span started in the parent. Returns None when there is no
    usable id (handler then no-ops, fail-open).
    """
    if child_session_id is None:
        return None
    text = str(child_session_id).strip()
    if not text:
        return None
    return f"subagent:{text}"


def subagent_status_to_span_status(child_status: Any) -> str:
    """Map a hermes ``child_status`` to ``"ok"`` / ``"error"``.

    Explicit failure-like statuses become ``"error"``; everything else
    (including unknown / empty) is ``"ok"`` so an absent status never
    pollutes error rates.
    """
    if child_status is None:
        return "ok"
    text = str(child_status).strip().lower()
    if not text:
        return "ok"
    if text in _SUBAGENT_ERROR_STATUSES:
        return "error"
    return "ok"
