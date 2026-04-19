"""Graceful-degradation tests: uninitialized plugin returns no-op spans."""

from hermes_otel.tracer import HermesOTelPlugin
from opentelemetry.trace import NonRecordingSpan


class TestUninitializedPlugin:
    def test_start_span_returns_noop_when_not_initialized(self):
        plugin = HermesOTelPlugin()
        assert not plugin.is_enabled
        span = plugin.start_span(name="test", key="k1")
        # Fallback is OTel's own NonRecordingSpan — it accepts all the
        # standard Span methods as no-ops, so hooks can call .set_attribute,
        # .set_status, .record_exception, .end without guarding.
        assert isinstance(span, NonRecordingSpan)
        # Smoke-check that the no-op methods don't raise.
        span.set_attribute("k", "v")
        span.set_status("OK", description="fine")
        span.record_exception(RuntimeError("boom"))
        span.end()

    def test_is_enabled_false_by_default(self):
        plugin = HermesOTelPlugin()
        assert plugin.is_enabled is False

    def test_record_metric_noop_when_no_meter(self):
        plugin = HermesOTelPlugin()
        # Should not raise even with no meter configured
        plugin.record_metric("session_count", 1, {"session_id": "s1"})
