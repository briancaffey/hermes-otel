"""Shared plumbing for the hook callbacks: tracer lookup, fail-open, previews.

Nothing here holds state; the tracer singleton owns all of it. Every
submodule imports :func:`get_tracer` from here so a test can swap the
tracer in one place (``hermes_otel.tracer.get_tracer``).
"""

from __future__ import annotations

import functools
from typing import Any, Dict, Optional, Tuple

from .. import tracer as _tracer_mod
from ..debug_utils import debug_log, logger
from ..helpers import clip_preview, session_id_from_turn_id


def get_tracer():
    """The plugin singleton, looked up at call time (patchable in tests)."""
    return _tracer_mod.get_tracer()


_FAIL_OPEN_WARNED: set = set()


def _fail_open(fn):
    """Never let a telemetry handler raise into the agent loop.

    Hermes catches handler exceptions, but a raise still aborts the handler
    mid-way (spans left open, parent stacks unbalanced) and logs a warning per
    call. Catch everything here, log once per (hook, exception type) at
    WARNING and every occurrence to the debug log, and return None.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — fail open by design
            key = (fn.__name__, type(exc).__name__)
            debug_log(f"{fn.__name__} failed open: {exc!r}")
            if key not in _FAIL_OPEN_WARNED:
                _FAIL_OPEN_WARNED.add(key)
                logger.warning(
                    "[hermes-otel] %s raised %s: %s — telemetry for this call was dropped "
                    "(further occurrences logged at DEBUG only)",
                    fn.__name__,
                    type(exc).__name__,
                    exc,
                )
            return None

    return wrapper


def _as_dict(value: Any) -> Dict[str, Any]:
    """Coerce a hook payload field to a dict, never raising.

    Hermes documents ``usage`` / ``error`` as dicts, but SDK objects
    (pydantic models, dataclasses, SimpleNamespace) and bare strings have been
    seen. A dict passes through; objects expose ``model_dump`` / ``_asdict`` /
    ``__dict__``; a non-empty string becomes ``{"message": value}``; anything
    else is ``{}``.
    """
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    for attr in ("model_dump", "_asdict"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                out = method()
                if isinstance(out, dict):
                    return out
            except Exception:
                pass
    if isinstance(value, str):
        return {"message": value} if value.strip() else {}
    d = getattr(value, "__dict__", None)
    if isinstance(d, dict):
        return dict(d)
    return {}


# Preview kind -> the per-kind config override. Each falls back to the global
# ``preview_max_chars`` when unset (None / 0).
_PREVIEW_LIMITS = {
    "tool_input": "tool_input_preview_max_chars",
    "tool_output": "tool_output_preview_max_chars",
    "llm_input": "llm_input_preview_max_chars",
    "llm_output": "llm_output_preview_max_chars",
}


def _preview(value: Any, max_chars: int) -> Optional[str]:
    """Apply the configured preview policy: capture toggle + clip_preview."""
    tracer = get_tracer()
    if not tracer.config.capture_previews:
        return None
    return clip_preview(value, max_chars)


def _preview_for(tracer, kind: Optional[str], value: Any) -> Optional[str]:
    """Clip ``value`` with the limit configured for ``kind`` (see ``_PREVIEW_LIMITS``).

    ``kind=None`` uses the global ``preview_max_chars``. Honors
    ``capture_previews`` (None when off) so callers omit the attribute.
    """
    cfg = tracer.config
    if not cfg.capture_previews:
        return None
    limit = None
    if kind is not None:
        limit = getattr(cfg, _PREVIEW_LIMITS[kind], None)
    return clip_preview(value, limit or cfg.preview_max_chars)


def _preview_marked(
    tracer, kind: Optional[str], value: Any
) -> "Tuple[Optional[str], Optional[int]]":
    """``_preview_for`` plus the original length when the preview was clipped.

    Returns ``(preview, original_chars)``; ``original_chars`` is None when
    nothing was cut, so a short-looking value can be told from a clipped one.
    """
    preview = _preview_for(tracer, kind, value)
    if preview is None or not preview.endswith("..."):
        return preview, None
    try:
        original = len(value if isinstance(value, str) else str(value))
    except Exception:
        return preview, None
    return preview, original if original > len(preview) else None


def _mark_truncated(
    attributes: Dict[str, Any], direction: str, original_chars: Optional[int]
) -> None:
    """``hermes.preview.<direction>.truncated`` / ``.original_chars`` when clipped."""
    if original_chars is None:
        return
    truncated_key, chars_key = _TRUNCATION_KEYS[direction]
    attributes[truncated_key] = True
    attributes[chars_key] = original_chars


_TRUNCATION_KEYS = {
    "input": ("hermes.preview.input.truncated", "hermes.preview.input.original_chars"),
    "output": ("hermes.preview.output.truncated", "hermes.preview.output.original_chars"),
}


def _resolve_session_id(
    kwargs: Dict[str, Any],
    session_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> str:
    """One precedence for the session a hook belongs to.

    1. An explicit ``session_id`` argument (hooks whose signature carries one).
    2. ``kwargs["session_id"]`` (tool / api hooks receive it as an extra).
    3. The first segment of ``turn_id`` (``<session_id>:<task_id>:<hex>``) —
       only when the caller passes ``turn_id`` explicitly: the approval hooks
       carry no session id at all, so this is their only source. Tool / api
       hooks never offer it (no Hermes dispatch path sends a real turn_id
       without a session_id, #68). Hermes' ``"session"`` placeholder (no
       session yet) resolves to ``""``.

    Returns ``""`` when nothing applies; callers treat that as "no session".
    """
    if session_id:
        return str(session_id)
    from_kwargs = kwargs.get("session_id")
    if from_kwargs:
        return str(from_kwargs)
    return session_id_from_turn_id(turn_id) if turn_id else ""
