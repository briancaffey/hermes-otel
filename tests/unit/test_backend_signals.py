"""Which signals each backend type receives by default (#160)."""

from hermes_otel.backends import _TRACES_ONLY, resolve
from hermes_otel.plugin_config import BackendConfig


class TestPhoenixIsTracesOnly:
    def test_default_creates_no_metrics_or_logs_exporter(self):
        rb = resolve(BackendConfig(type="phoenix", endpoint="http://localhost:6006/v1/traces"))
        assert rb.supports_traces is True
        assert rb.supports_metrics is False  # Phoenix answers 405 on /v1/metrics
        assert rb.supports_logs is False

    def test_explicit_override_for_a_collector_in_front(self):
        rb = resolve(
            BackendConfig(type="phoenix", endpoint="http://collector:4318/v1/traces", metrics=True)
        )
        assert rb.supports_metrics is True

    def test_traces_only_set_matches_the_docs(self):
        assert _TRACES_ONLY == {"phoenix", "langfuse", "jaeger", "tempo", "weave"}
