"""Hermes OTel plugin — hook callbacks.

Each hook starts or ends a span, passing data through to OTel attributes.
The package is split by hook family; this module re-exports every callback
(``on_*``), :func:`get_tracer` and the public :func:`get_current_traceparent`.
Helpers live in their family module and are imported from there (#411).

Per-session buffering (token totals, first input / last output, per-turn
summary, tool start times) lives on ``tracer.sessions`` — see
``session_state.py``. Nothing in this package holds state; everything is
routed through the tracer singleton so test reset is just
``get_tracer()`` re-creation.

Modules:

* ``_common``      — tracer lookup, ``_fail_open``, previews, session resolution
* ``attributes``   — the attribute builders every hook shares
* ``usage``        — token-usage normalization, attributes and metrics
* ``session``      — ``on_session_start`` / ``on_session_end`` (the turn root),
                     ``on_session_finalize`` / ``on_session_reset`` (session boundaries)
* ``tools``        — ``pre_tool_call`` / ``post_tool_call`` and skill spans
* ``approval``     — ``pre_approval_request`` / ``post_approval_response``
* ``llm``          — ``pre/post_llm_call``, ``pre/post_api_request``, ``api_request_error``
* ``auxiliary``    — ``pre/post_auxiliary_call`` (titling, compression, MoA, vision, ...)
* ``subagent``     — ``subagent_start`` / ``subagent_stop``
* ``propagation``  — W3C ``traceparent`` (``get_current_traceparent``, ``mcp_request_headers``)
* ``transforms``   — observers on ``transform_terminal_output`` / ``transform_llm_output``
* ``kanban``       — ``kanban_task_*`` / ``on_kanban_worker_*`` board transitions
"""

from __future__ import annotations

from ._common import get_tracer
from .approval import on_post_approval_response, on_pre_approval_request
from .auxiliary import on_post_auxiliary_call, on_pre_auxiliary_call
from .kanban import (
    on_kanban_task_blocked,
    on_kanban_task_claimed,
    on_kanban_task_completed,
    on_kanban_worker_exited,
    on_kanban_worker_spawned,
    on_kanban_worker_stale_claim,
)
from .llm import (
    on_api_request_error,
    on_post_api_request,
    on_post_llm_call,
    on_pre_api_request,
    on_pre_llm_call,
)
from .propagation import get_current_traceparent, on_mcp_request_headers
from .session import on_session_end, on_session_finalize, on_session_reset, on_session_start
from .subagent import on_subagent_start, on_subagent_stop
from .tools import on_post_tool_call, on_pre_tool_call
from .transforms import on_transform_llm_output, on_transform_terminal_output

__all__ = [
    "get_current_traceparent",
    "get_tracer",
    "on_api_request_error",
    "on_kanban_task_blocked",
    "on_kanban_task_claimed",
    "on_kanban_task_completed",
    "on_kanban_worker_exited",
    "on_kanban_worker_spawned",
    "on_kanban_worker_stale_claim",
    "on_mcp_request_headers",
    "on_post_api_request",
    "on_post_approval_response",
    "on_post_auxiliary_call",
    "on_post_llm_call",
    "on_post_tool_call",
    "on_pre_api_request",
    "on_pre_approval_request",
    "on_pre_auxiliary_call",
    "on_pre_llm_call",
    "on_pre_tool_call",
    "on_session_end",
    "on_session_finalize",
    "on_session_reset",
    "on_session_start",
    "on_subagent_start",
    "on_subagent_stop",
    "on_transform_llm_output",
    "on_transform_terminal_output",
]
