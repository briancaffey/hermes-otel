"""Hermes OTel plugin — hook callbacks.

Each hook starts or ends a span, passing data through to OTel attributes.
The package is split by hook family; this module re-exports every callback
(``on_*``), :func:`get_tracer` and the public :func:`get_current_traceparent`,
and holds :data:`HOOKS`, the one ``(hook name, callback)`` table that
``register()`` loops over. Helpers live in their family module and are
imported from there (#411).

Per-session buffering (token totals, first input / last output, per-turn
summary) lives on ``tracer.sessions`` — see ``session_state.py``; when a
tool call, approval or human-input wait started lives on ``tracer.spans``
(``mark_started`` / ``pop_elapsed``, keyed like the span). Nothing in this
package holds telemetry state; everything is routed through the tracer
singleton so test reset is just ``get_tracer()`` re-creation. What the
modules keep at module level is not telemetry: warn-once markers, the
kanban span-key counter, and the ``TRACEPARENT`` kanban put in ``os.environ``
so that cleanup removes exactly that one.

Modules:

* ``_common``      — tracer lookup, ``_fail_open``, previews, session resolution
* ``attributes``   — the attribute builders every hook shares
* ``api_call``     — the ``api.<model>`` attribute and metric builders shared by
                     the main-loop, auxiliary and error hooks
* ``usage``        — token-usage normalization, attributes and metrics
* ``session``      — ``on_session_start`` / ``on_session_end`` (the turn root),
                     ``on_session_finalize`` / ``on_session_reset`` (session boundaries),
                     ``agent_loop_stopped`` / ``pre_verify``
* ``tools``        — ``pre_tool_call`` / ``post_tool_call`` and skill spans
* ``skills``       — which skill a tool call loaded (pure functions for ``tools``)
* ``approval``     — ``pre_approval_request`` / ``post_approval_response``
* ``llm``          — ``pre/post_llm_call``, ``pre/post_api_request``, ``api_request_error``
* ``auxiliary``    — ``pre/post_auxiliary_call`` (titling, compression, MoA, vision, ...)
* ``subagent``     — ``subagent_start`` / ``subagent_stop``
* ``propagation``  — W3C ``traceparent`` (``get_current_traceparent``, ``mcp_request_headers``)
* ``transforms``   — observers on ``transform_terminal_output`` / ``transform_llm_output``
* ``kanban``       — ``kanban_task_*`` / ``on_kanban_worker_*`` board transitions and
                     ``on_kanban_dispatch_tick`` / ``on_kanban_task_updated``
* ``observers``    — ``pre_command``, human-input waits, ``on_skill_lifecycle``,
                     ``pre_transcription``
"""

from __future__ import annotations

from typing import Any, Callable, Tuple

from ._common import get_tracer
from .approval import on_post_approval_response, on_pre_approval_request
from .auxiliary import on_post_auxiliary_call, on_pre_auxiliary_call
from .kanban import (
    on_kanban_dispatch_tick,
    on_kanban_task_blocked,
    on_kanban_task_claimed,
    on_kanban_task_completed,
    on_kanban_task_updated,
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
from .observers import (
    on_human_input_request,
    on_human_input_resolved,
    on_pre_command,
    on_pre_transcription,
    on_skill_lifecycle,
)
from .propagation import get_current_traceparent, on_mcp_request_headers
from .session import (
    on_agent_loop_stopped,
    on_pre_verify,
    on_session_end,
    on_session_finalize,
    on_session_reset,
    on_session_start,
)
from .subagent import on_subagent_start, on_subagent_stop
from .tools import on_post_tool_call, on_pre_tool_call
from .transforms import on_transform_llm_output, on_transform_terminal_output

# The one hook table: ``(Hermes hook name, callback)`` in ``plugin.yaml``
# ``provides_hooks`` order. ``register()`` loops over it and ``__all__`` is
# derived from it; the manifest is pinned to it by tests/unit/test_hooks_package.
# ``mcp_request_headers`` is deliberately absent: it is not a hook in any
# Hermes release, so ``register()`` adds it only when the running Hermes
# lists it in ``VALID_HOOKS`` (#130).
HOOKS: Tuple[Tuple[str, Callable[..., Any]], ...] = (
    ("on_session_start", on_session_start),
    ("on_session_end", on_session_end),
    ("on_session_finalize", on_session_finalize),
    ("on_session_reset", on_session_reset),
    ("pre_tool_call", on_pre_tool_call),
    ("post_tool_call", on_post_tool_call),
    ("pre_llm_call", on_pre_llm_call),
    ("post_llm_call", on_post_llm_call),
    ("pre_api_request", on_pre_api_request),
    ("post_api_request", on_post_api_request),
    ("api_request_error", on_api_request_error),
    # Auxiliary LLM calls (#389): titling, compression, MoA, vision, ...
    ("pre_auxiliary_call", on_pre_auxiliary_call),
    ("post_auxiliary_call", on_post_auxiliary_call),
    ("subagent_start", on_subagent_start),
    ("subagent_stop", on_subagent_stop),
    ("pre_approval_request", on_pre_approval_request),
    ("post_approval_response", on_post_approval_response),
    # Observers on two transform hooks: they always return None, so the
    # content is never replaced (#33).
    ("transform_terminal_output", on_transform_terminal_output),
    ("transform_llm_output", on_transform_llm_output),
    # Kanban board transitions (#31): dispatcher-side claim / spawn / exit /
    # stale-claim and worker-side complete / block; board health (#397).
    ("kanban_task_claimed", on_kanban_task_claimed),
    ("kanban_task_completed", on_kanban_task_completed),
    ("kanban_task_blocked", on_kanban_task_blocked),
    ("on_kanban_worker_spawned", on_kanban_worker_spawned),
    ("on_kanban_worker_exited", on_kanban_worker_exited),
    ("on_kanban_worker_stale_claim", on_kanban_worker_stale_claim),
    ("on_kanban_dispatch_tick", on_kanban_dispatch_tick),
    # Interruption and verification (#396).
    ("agent_loop_stopped", on_agent_loop_stopped),
    ("pre_verify", on_pre_verify),
    # Small observers (#400): slash commands, human-input waits, skill
    # lifecycle facts, voice transcriptions. All return None.
    ("pre_command", on_pre_command),
    ("on_human_input_request", on_human_input_request),
    ("on_human_input_resolved", on_human_input_resolved),
    ("on_skill_lifecycle", on_skill_lifecycle),
    ("pre_transcription", on_pre_transcription),
    ("on_kanban_task_updated", on_kanban_task_updated),
)

__all__ = ["HOOKS", "get_current_traceparent", "get_tracer", "on_mcp_request_headers"] + [
    callback.__name__ for _, callback in HOOKS
]
