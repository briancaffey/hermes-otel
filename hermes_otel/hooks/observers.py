"""Small observer hooks (#400): slash commands, human-input waits, skill
lifecycle facts and voice transcriptions.

Every handler is an observer and returns None. ``pre_transcription`` is a
decision hook in Hermes (a dict return rewrites the transcription request),
so returning None matters there. No handler records a free-text value Hermes
passes along: never a command's arguments, a transcription's file path or
prompt; a human-input prompt only as a preview, under ``content_capture``.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from ..debug_utils import debug_log
from ..helpers import truncate_string
from ._common import _fail_open, _preview_for, get_tracer

_LABEL_MAX = 64
# Approval waits already have their own approval.* spans (pre/post approval
# hooks); a human_input span would duplicate them, so they are only counted.
# Every other kind (a clarify question, a password prompt) gets a span.
_COUNT_ONLY_KINDS = frozenset({"approval"})
_waits: Dict[str, float] = {}


def _label(value: Any, default: str = "unknown") -> str:
    text = str(value or "").strip()
    return truncate_string(text, _LABEL_MAX) if text else default


@_fail_open
def on_pre_command(command: Any = None, surface: Any = None, **kwargs: Any) -> None:
    """A recognised slash command is about to run: one point per command.
    ``command`` is Hermes's canonical name (bounded); ``args_raw`` is never read."""
    tracer = get_tracer()
    if not tracer.is_enabled or not command:
        return None
    tracer.record_metric(
        "command_count", 1, {"command": _label(command), "surface": _label(surface)}
    )
    return None


@_fail_open
def on_human_input_request(
    kind: Any = None,
    request_id: Any = None,
    session_id: Any = None,
    prompt: Any = None,
    **kwargs: Any,
) -> None:
    """The agent is waiting on a person (a clarify question, a password prompt).

    Opens ``human_input.{kind}`` under the session's in-flight span; an
    ``approval`` wait is only counted on resolve.
    The prompt Hermes passes is already redacted; it is kept as a preview.
    """
    tracer = get_tracer()
    if not tracer.is_enabled or not request_id:
        return None
    k = _label(kind)
    _waits[str(request_id)] = time.perf_counter()
    if k in _COUNT_ONLY_KINDS:
        return None
    sid = str(session_id or "") or None
    attributes: Dict[str, Any] = {"hermes.span_kind": "human_input", "hermes.human_input.kind": k}
    preview = _preview_for(tracer, None, prompt)
    if preview is not None:
        attributes["hermes.human_input.prompt"] = preview
    tracer.start_span(
        name=f"human_input.{k}",
        key=f"human_input:{request_id}",
        kind="general",
        attributes=attributes,
        session_id=sid,
        parent=tracer.spans.get_current_parent(sid),
    )
    return None


@_fail_open
def on_human_input_resolved(
    kind: Any = None, request_id: Any = None, outcome: Any = None, **kwargs: Any
) -> None:
    """The person answered (or the wait ended): close the span and count it."""
    tracer = get_tracer()
    if not tracer.is_enabled or not request_id:
        return None
    k, result = _label(kind), _label(outcome, "error")
    started = _waits.pop(str(request_id), None)
    wait_s = round(time.perf_counter() - started, 3) if started is not None else None
    if k not in _COUNT_ONLY_KINDS:
        attrs: Dict[str, Any] = {"hermes.human_input.outcome": result}
        if wait_s is not None:
            attrs["hermes.human_input.wait_s"] = wait_s
        # Waiting on a person is never itself an error; the outcome says how it ended.
        tracer.end_span(f"human_input:{request_id}", attributes=attrs, status="ok")
    tracer.record_metric("human_input_count", 1, {"kind": k, "outcome": result})
    return None


@_fail_open
def on_skill_lifecycle(action: Any = None, provenance: Any = None, **kwargs: Any) -> None:
    """A skill changed state (created, patched, used, ...): one point per fact.
    The skill's name is not a label: a profile can hold any number of skills."""
    tracer = get_tracer()
    if not tracer.is_enabled or not action:
        return None
    tracer.record_metric(
        "skill_lifecycle_count", 1, {"action": _label(action), "provenance": _label(provenance)}
    )
    return None


@_fail_open
def on_pre_transcription(
    provider: Any = None, model: Any = None, source: Any = None, **kwargs: Any
) -> None:
    """A voice message is about to be transcribed: one point per attempt.
    A decision hook in Hermes; this observer always returns None and never
    reads ``file_path`` or ``prompt``."""
    debug_log(f"pre_transcription fired: provider={provider} model={model}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return None
    tracer.record_metric(
        "transcription_count",
        1,
        {"provider": _label(provider), "model": _label(model), "source": _label(source)},
    )
    return None
