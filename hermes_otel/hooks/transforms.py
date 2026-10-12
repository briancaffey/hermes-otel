"""Observers on Hermes' ``transform_*`` hooks (#33).

Hermes offers ``transform_terminal_output`` and ``transform_llm_output`` so a
plugin can *replace* content before the model or the user sees it. This plugin
subscribes as a pure observer: every handler returns ``None`` (the hooks are
first-non-empty-string-wins, so ``None`` leaves the content untouched) and
records sizes and metadata on spans that already exist.

What the observers see, and what they cannot see:

* ``transform_terminal_output`` fires inside the terminal tool with the **raw**
  output, before Hermes applies its own output limit. The raw size, return
  code and environment type go on the open ``tool.terminal`` span;
  ``on_post_tool_call`` later compares the raw size with the output that
  actually entered the result, so Hermes' truncation becomes visible as
  ``hermes.terminal.output.truncated`` / ``dropped_chars``.
* ``transform_llm_output`` fires in the turn finalizer with the final text.
  Its size goes on the turn root (the ``llm`` span is already closed).
* Every callback of a transform hook receives the **same original** value and
  the first string wins, so a replacement made by another plugin is invisible
  to an observer. Only the framework's own truncation can be measured.

``transform_tool_result`` is deliberately not subscribed: it fires after
``post_tool_call`` with the same ``result`` the plugin already saw there, so
the result size is recorded on the tool span in ``on_post_tool_call`` instead
of registering a third behaviour-changing hook for no new information.
"""

from __future__ import annotations

from typing import Any, Optional

from ..debug_utils import debug_log
from ..helpers import char_count, truncate_string
from ._common import _fail_open, get_tracer
from .tools import _tool_call_id

# Hermes' environment-type strings are short identifiers (``local``,
# ``docker``, ``ssh``, ...); the clip only guards against a surprise.
_ENV_TYPE_MAX = 64


def _set_attributes(span: Any, attributes: dict) -> None:
    """Set attributes on a live span; a no-op for span objects without the
    OTel API (``INVALID_SPAN`` stand-ins) and never raises."""
    if span is None or not hasattr(span, "set_attribute"):
        return
    for key, value in attributes.items():
        if value is None:
            continue
        try:
            span.set_attribute(key, value)
        except Exception:  # pragma: no cover — never let telemetry raise
            pass


@_fail_open
def on_transform_terminal_output(
    command: Any = None,
    output: Any = None,
    returncode: Any = None,
    task_id: Any = None,
    env_type: Any = None,
    **kwargs: Any,
) -> None:
    """Observe the raw terminal output before Hermes' output limit.

    Always returns ``None`` so the output is never replaced.
    """
    tracer = get_tracer()
    if not tracer.is_enabled:
        return None

    tool_call_id = _tool_call_id(str(task_id or ""), kwargs)
    key = f"terminal:{tool_call_id}"
    raw_chars = char_count(output)
    debug_log(f"transform_terminal_output observed: key={key} chars={raw_chars}")

    attributes: dict = {"hermes.terminal.output.chars": raw_chars}
    if isinstance(returncode, bool):
        attributes["hermes.terminal.returncode"] = int(returncode)
    elif isinstance(returncode, int):
        attributes["hermes.terminal.returncode"] = returncode
    if env_type:
        attributes["hermes.terminal.env_type"] = truncate_string(str(env_type), _ENV_TYPE_MAX)

    span = tracer.spans.get_span(key)
    _set_attributes(span, attributes)
    if raw_chars is not None:
        # on_post_tool_call reads this back to measure the output limit.
        tracer.sessions.record_tool_output_chars(key, raw_chars)
    return None


@_fail_open
def on_transform_llm_output(
    response_text: Any = None,
    session_id: Optional[str] = None,
    model: Any = None,
    platform: Any = None,
    **kwargs: Any,
) -> None:
    """Observe the final response text's size on the turn root.

    Always returns ``None`` so the response is never replaced.
    """
    tracer = get_tracer()
    if not tracer.is_enabled:
        return None

    chars = char_count(response_text)
    if chars is None:
        return None
    sid = str(session_id or "")
    debug_log(f"transform_llm_output observed: session={sid} chars={chars}")
    root = tracer.spans.get_session_root(sid) if sid else None
    if root is None:
        root = tracer.spans.get_current_parent(sid or None)
    _set_attributes(root, {"hermes.llm.output.chars": chars})
    return None
