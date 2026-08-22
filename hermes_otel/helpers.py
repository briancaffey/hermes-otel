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


# Argument keys the ``skill_view`` tool may carry the skill name under.
_SKILL_VIEW_ARG_KEYS = ("name", "skill", "skill_name")


def detect_skill(tool_name: Optional[str], args: Optional[Dict[str, Any]]):
    """Detect an activated skill from a tool call.

    Two sources, in priority order:

    * ``skill_view`` — the canonical way Hermes loads a skill. The skill name
      arrives as an *argument* (e.g. ``{"name": "axolotl"}``), which the
      path-based :func:`infer_skill_name` heuristic does not catch. A
      plugin-namespaced name (``"plugin:axolotl"``) is reduced to the bare
      skill name.
    * path match — any tool whose ``path`` / ``file_path`` / ``target`` points
      into ``/skills/<name>/`` (e.g. ``read`` of a SKILL.md).

    Returns ``(skill_name, source)`` where ``source`` is ``"skill_view"`` or
    ``"path_match"``, or ``(None, None)`` when no skill is involved.
    """
    if not isinstance(args, dict):
        return None, None
    if tool_name == "skill_view":
        for key in _SKILL_VIEW_ARG_KEYS:
            v = args.get(key)
            if not isinstance(v, str) or not v.strip():
                continue
            # The value may be a bare name, a plugin-namespaced name, or (rarely)
            # a /skills/ path — handle all three.
            name = infer_skill_name_from_text(v) or v.split(":")[-1].split("/")[0].strip()
            if name:
                return name, "skill_view"
    name = infer_skill_name(args)
    if name:
        return name, "path_match"
    return None, None


def session_id_from_turn_id(turn_id: Any) -> str:
    """Recover the plugin ``session_id`` from a hook ``turn_id``.

    Hermes builds ``turn_id`` as ``"<session_id>:<task_id>:<hex>"``
    (``agent/turn_context.py``). Hooks that only carry ``turn_id`` (e.g. the
    approval hooks) can therefore recover the session the plugin keys its spans
    on by taking the first segment. Returns ``""`` when unavailable.
    """
    if not isinstance(turn_id, str) or ":" not in turn_id:
        return ""
    head = turn_id.split(":", 1)[0].strip()
    # ``agent/turn_context.py`` uses the literal "session" placeholder when the
    # agent has no session_id yet — that's not a real id to correlate on.
    return "" if head == "session" else head


_APPROVAL_GRANT_CHOICES = frozenset({"once", "session", "always"})


def classify_approval_choice(choice: Any) -> Dict[str, Any]:
    """Normalize an approval ``choice`` into telemetry fields.

    ``choice`` is one of ``once`` / ``session`` / ``always`` / ``deny`` /
    ``timeout``. The first three are grants; ``timeout`` is flagged distinctly.
    A denied or timed-out approval is a legitimate human outcome, not an error.
    """
    c = (choice or "").strip().lower() if isinstance(choice, str) else ""
    return {
        "choice": c,
        "granted": c in _APPROVAL_GRANT_CHOICES,
        "timed_out": c == "timeout",
    }


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


# ── API errors / retries ─────────────────────────────────────────────────────

_TRUE_STRINGS = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "f", "no", "n", "off", ""})


def to_optional_int(value: Any) -> Optional[int]:
    """Best-effort int conversion that returns None (not 0) when unparseable.

    Distinct from the hooks-module ``_to_int`` (which zero-fills) because for
    error telemetry we must tell "status 0" apart from "no status reported".
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def coerce_bool(value: Any) -> Optional[bool]:
    """Parse a loosely-typed truthy/falsey value, or None when undecidable.

    Accepts real bools, ints, and the usual string spellings. Returns None for
    ``None`` so callers can distinguish "not reported" from "reported false".
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_STRINGS:
            return True
        if text in _FALSE_STRINGS:
            return False
        return None
    return None


def http_status_class(status_code: Any) -> str:
    """Bucket an HTTP status code into a low-cardinality metric label.

    Returns ``"2xx"``/``"3xx"``/``"4xx"``/``"5xx"`` for a valid HTTP code,
    ``"network"`` when there is no code (None / 0 / unparseable — i.e. the
    request never got an HTTP response: timeout, connection error), and
    ``"other"`` for an out-of-range number. Keeps error-metric cardinality
    bounded regardless of provider quirks.
    """
    code = to_optional_int(status_code)
    if code is None or code <= 0:
        return "network"
    if 100 <= code < 600:
        return f"{code // 100}xx"
    return "other"
