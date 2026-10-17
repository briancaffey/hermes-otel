"""Text helpers shared across hermes-otel modules: clipping, serialising,
the plugin's version, the fleet profile name and W3C ``traceparent``.

Kept separate from the hooks package so unit tests can import without pulling in
the OpenTelemetry SDK dependency tree. Anything one module owns lives with that
module: skill inference in ``hooks/skills.py``, the approval / tool / sub-agent /
auxiliary classifiers in their hook module, scalar coercion in ``coerce.py``,
the exporter-environment and host facts in ``pipeline_env.py`` (#522).
"""

from __future__ import annotations

import functools
import json
import os
import re
from typing import Any, List, Optional, Tuple

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


def clip_joined(items: List[str], sep: str, limit: int = 500) -> str:
    """Join ``items`` with ``sep``, capped to ``limit`` chars with a ``...`` tail.

    Unlike :func:`clip_preview` this keeps whitespace as-is (turn summaries
    list raw commands) and returns ``""`` rather than None for no items.
    """
    if not items:
        return ""
    joined = sep.join(items)
    if len(joined) <= limit:
        return joined
    if limit <= 3:
        return "." * limit
    return joined[: limit - 3] + "..."


def _json_default(obj: Any) -> Any:
    """Fallback for :func:`json.dumps` on objects handed through the api hook.

    Hermes-agent emits ``tool_calls`` as ``SimpleNamespace`` (nested, with a
    ``.function`` sub-namespace). json.dumps calls this recursively for any
    non-serialisable object, so returning ``__dict__`` flattens each layer.
    """
    if hasattr(obj, "__dict__") and obj.__dict__:
        return obj.__dict__
    return str(obj)


def serialize_full(value: Any) -> Optional[str]:
    """JSON-serialise ``value`` in full (no truncation), or None when empty.

    Used for ``capture_full_prompts`` / ``capture_full_responses``: the whole
    point is fidelity, so no ``preview_max_chars``. None on empty or
    unserialisable input so the caller can skip the attribute.
    """
    if value is None or value == "" or value == [] or value == {}:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    except Exception:
        try:
            return str(value)
        except Exception:
            return None


@functools.lru_cache(maxsize=1)
def package_version() -> Optional[str]:
    """The plugin's version string, or None if it cannot be determined.

    ``hermes plugins install`` copies the ``hermes_otel/`` directory into
    ``$HERMES_HOME/plugins`` — it is *not* a pip-installed distribution, so
    ``importlib.metadata`` knows nothing about it. The shipped ``plugin.yaml``
    (bumped by release-please) is therefore the primary source; the
    distribution metadata is the fallback for wheel / editable installs.
    Cached: this is read on every session start/end and at Resource build.
    """
    try:
        manifest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin.yaml")
        with open(manifest, encoding="utf-8") as fh:
            for line in fh:
                found = re.match(r'^version:\s*"?([0-9][^"\s]*)"?\s*$', line)
                if found:
                    return found.group(1)
    except OSError:
        pass
    try:
        from importlib.metadata import version

        return version("hermes-otel")
    except Exception:  # PackageNotFoundError or a broken metadata install
        return None


DEFAULT_SERVICE_NAME = "hermes-agent"

# Profile names that do not identify one agent: the pre-profile home and an
# arbitrary ``HERMES_HOME`` outside the profiles root (``hermes_home``).
_UNNAMED_PROFILES = frozenset({"", "default", "custom"})


def named_profile(profile: Any) -> str:
    """The profile name when it identifies one agent of a fleet, else ``""``.

    ``default`` and ``custom`` are not agent names: every single-profile
    install is ``default``, and ``custom`` is any home outside the profiles
    root (#395).
    """
    if not isinstance(profile, str):
        return ""
    name = profile.strip()
    return "" if name in _UNNAMED_PROFILES else name


def parse_traceparent(value: Any) -> Optional[Tuple[int, int, int]]:
    """``(trace_id, span_id, flags)`` from a W3C ``traceparent`` (version 00), or
    None when it is malformed or names the all-zero (invalid) ids."""
    parts = str(value or "").strip().lower().split("-")
    if len(parts) != 4 or parts[0] != "00":
        return None
    trace_hex, span_hex, flags_hex = parts[1], parts[2], parts[3]
    if len(trace_hex) != 32 or len(span_hex) != 16 or len(flags_hex) != 2:
        return None
    try:
        trace_id, span_id, flags = int(trace_hex, 16), int(span_hex, 16), int(flags_hex, 16)
    except ValueError:
        return None
    if not trace_id or not span_id:
        return None
    return trace_id, span_id, flags


def format_traceparent(trace_id: int, span_id: int, flags: int) -> str:
    """The W3C ``traceparent`` for a span context (version 00)."""
    return f"00-{trace_id:032x}-{span_id:016x}-{'01' if flags & 1 else '00'}"
