"""Hermes OTel plugin — hook callbacks.

Each hook starts or ends a span, passing data through to OTel attributes.
The package is split by hook family; this module re-exports every callback
(``on_*``), the public :func:`get_current_traceparent`, and the private
helpers tests exercise, so ``hermes_otel.hooks.<name>`` keeps working.

Per-session buffering (token totals, first input / last output, per-turn
summary, tool start times) lives on ``tracer.sessions`` — see
``session_state.py``. Nothing in this package holds state; everything is
routed through the tracer singleton so test reset is just
``get_tracer()`` re-creation.

Modules:

* ``_common``      — tracer lookup, ``_fail_open``, previews, session resolution
* ``attributes``   — the attribute builders every hook shares
* ``usage``        — token-usage normalization, attributes and metrics
* ``session``      — ``on_session_start`` / ``on_session_end`` (the turn root)
* ``tools``        — ``pre_tool_call`` / ``post_tool_call`` and skill spans
* ``approval``     — ``pre_approval_request`` / ``post_approval_response``
* ``llm``          — ``pre/post_llm_call``, ``pre/post_api_request``, ``api_request_error``
* ``subagent``     — ``subagent_start`` / ``subagent_stop``
* ``propagation``  — W3C ``traceparent`` (``get_current_traceparent``, ``mcp_request_headers``)
"""

from __future__ import annotations

from ._common import (
    _FAIL_OPEN_WARNED,
    _as_dict,
    _fail_open,
    _preview,
    _preview_for,
    _resolve_session_id,
    get_tracer,
)
from .approval import _approval_span_key, on_post_approval_response, on_pre_approval_request
from .attributes import (
    _correlation_attributes,
    _extract_correlation_id,
    _gen_ai_attributes,
    _gen_ai_request_param_attributes,
    _message_json,
    _metric_model_labels,
    _model_attributes,
    _per_session_sender_attributes,
    _provider_attributes,
    _sender_attributes,
    _serialize_conversation_history,
    _session_context_attributes,
    _session_identity_attributes,
    _session_sender_attributes,
    _summary_attributes,
    _tool_host_utilization_attributes,
    _turn_attributes,
    _weave_turn_attributes,
)
from .llm import (
    on_api_request_error,
    on_post_api_request,
    on_post_llm_call,
    on_pre_api_request,
    on_pre_llm_call,
)
from .propagation import get_current_traceparent, on_mcp_request_headers
from .session import _start_session_span, on_session_end, on_session_start
from .subagent import on_subagent_start, on_subagent_stop
from .tools import (
    _open_skill_span,
    _resolve_tool_outcome,
    _tool_call_id,
    on_post_tool_call,
    on_pre_tool_call,
)
from .usage import (
    _USAGE_FIELDS,
    _genai_metric_dims,
    _normalize_usage,
    _record_genai_token_usage,
    _record_prompt_cache_metrics,
    _record_usage_metrics,
    _usage_attributes,
)

__all__ = [
    "get_current_traceparent",
    "get_tracer",
    "on_api_request_error",
    "on_mcp_request_headers",
    "on_post_api_request",
    "on_post_approval_response",
    "on_post_llm_call",
    "on_post_tool_call",
    "on_pre_api_request",
    "on_pre_approval_request",
    "on_pre_llm_call",
    "on_pre_tool_call",
    "on_session_end",
    "on_session_start",
    "on_subagent_start",
    "on_subagent_stop",
]
