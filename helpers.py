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

_KNOWN_OUTCOMES = {"completed", "error", "timeout", "blocked", "cancelled", "skipped"}


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
