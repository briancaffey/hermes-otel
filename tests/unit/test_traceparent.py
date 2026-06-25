"""Unit tests for W3C traceparent propagation helpers.

Exercises ``get_current_traceparent`` and the ``on_mcp_request_headers`` hook
against a real OTel span pushed onto the tracer's session-keyed parent stack.
No network.
"""

import re

import pytest
from hermes_otel import hooks
from hermes_otel.tracer import get_tracer
from opentelemetry import trace as _trace
from opentelemetry.sdk.trace import TracerProvider

_TRACEPARENT_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$")


@pytest.fixture
def real_span():
    """Yield a real, recording OTel span (not attached to the OTel context)."""
    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    span = tracer.start_span("unit-parent")
    try:
        yield span
    finally:
        span.end()


@pytest.fixture(autouse=True)
def fresh_tracer():
    """Reset the singleton so each test starts with an empty span registry."""
    import hermes_otel.tracer as tracer_mod

    tracer_mod._tracer = None
    yield
    tracer_mod._tracer = None


class TestGetCurrentTraceparent:
    def test_none_when_no_active_span(self):
        assert hooks.get_current_traceparent("s1") is None
        assert hooks.get_current_traceparent() is None

    def test_returns_well_formed_traceparent_for_session(self, real_span):
        get_tracer().spans.push_parent(real_span, session_id="s1")
        tp = hooks.get_current_traceparent("s1")
        assert tp is not None
        assert _TRACEPARENT_RE.match(tp), tp

    def test_traceparent_carries_span_context(self, real_span):
        get_tracer().spans.push_parent(real_span, session_id="s1")
        tp = hooks.get_current_traceparent("s1")
        ctx = real_span.get_span_context()
        assert tp == f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-01"

    def test_unknown_session_falls_back_to_any_active_stack(self, real_span):
        # Span registered under s1, but the caller doesn't know the session id.
        get_tracer().spans.push_parent(real_span, session_id="s1")
        tp_known = hooks.get_current_traceparent("s1")
        tp_unknown = hooks.get_current_traceparent("does-not-exist")
        assert tp_unknown == tp_known

    def test_none_after_parent_popped(self, real_span):
        spans = get_tracer().spans
        spans.push_parent(real_span, session_id="s1")
        spans.pop_parent(session_id="s1")
        assert hooks.get_current_traceparent("s1") is None


class TestMcpRequestHeadersHook:
    def test_empty_dict_when_no_span(self):
        assert hooks.on_mcp_request_headers(server_name="srv", tool_name="t") == {}

    def test_injects_traceparent(self, real_span):
        get_tracer().spans.push_parent(real_span, session_id="s1")
        headers = hooks.on_mcp_request_headers(server_name="srv", tool_name="t", session_id="s1")
        assert set(headers) == {"traceparent"}
        assert headers["traceparent"] == hooks.get_current_traceparent("s1")

    def test_session_id_via_kwargs(self, real_span):
        get_tracer().spans.push_parent(real_span, session_id="s1")
        headers = hooks.on_mcp_request_headers(session_id="s1")
        assert "traceparent" in headers

    def test_never_raises_on_missing_args(self):
        # Defensive: a host that calls with no kwargs must not blow up.
        assert hooks.on_mcp_request_headers() == {}
