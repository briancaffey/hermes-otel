"""Tests for NoopSpan and graceful degradation."""

from hermes_otel.tracer import NoopSpan, HermesOTelPlugin


class TestNoopSpan:
    def test_set_attribute_does_not_raise(self):
        span = NoopSpan()
        span.set_attribute("key", "value")

    def test_set_status_does_not_raise(self):
        span = NoopSpan()
        span.set_status("OK", description="fine")

    def test_record_exception_does_not_raise(self):
        span = NoopSpan()
        span.record_exception(RuntimeError("boom"))

    def test_end_does_not_raise(self):
        span = NoopSpan()
        span.end()


class TestUninitializedPlugin:
    def test_start_span_returns_noop_when_not_initialized(self):
        plugin = HermesOTelPlugin()
        assert not plugin.is_enabled
        span = plugin.start_span(name="test", key="k1")
        assert isinstance(span, NoopSpan)

    def test_is_enabled_false_by_default(self):
        plugin = HermesOTelPlugin()
        assert plugin.is_enabled is False

    def test_record_metric_noop_when_no_meter(self):
        plugin = HermesOTelPlugin()
        # Should not raise even with no meter configured
        plugin.record_metric("session_count", 1, {"session_id": "s1"})
