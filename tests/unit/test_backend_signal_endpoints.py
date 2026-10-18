"""An entry's ``metrics_endpoint`` / ``logs_endpoint`` override the derived
per-signal URL on every type whose resolver sets none (#607)."""

import pytest

from hermes_otel.backend_config import BackendConfig
from hermes_otel.backends import resolve
from hermes_otel.pipeline import _build_exporter
from hermes_otel.plugin_config import HermesOtelConfig

M = "http://m:9/v1/metrics"
L = "http://l:9/v1/logs"


class TestEntryEndpointsOverrideTheDerivedOnes:
    def test_http_otlp_entry(self):
        rb = resolve(
            BackendConfig(
                type="otlp",
                endpoint="http://a:4318/v1/traces",
                metrics_endpoint=M,
                logs_endpoint=L,
            )
        )
        assert rb.target("traces").endpoint == "http://a:4318/v1/traces"
        assert rb.target("metrics").endpoint == M
        assert rb.target("logs").endpoint == L

    def test_grpc_otlp_entry(self):
        rb = resolve(
            BackendConfig(
                type="otlp",
                protocol="grpc",
                endpoint="http://a:4317",
                metrics_endpoint="http://m:4317",
                logs_endpoint="http://l:4317",
            )
        )
        assert rb.target("traces").endpoint == "http://a:4317"
        assert rb.target("metrics").endpoint == "http://m:4317"
        assert rb.target("logs").endpoint == "http://l:4317"

    @pytest.mark.parametrize("backend_type", ["lgtm", "signoz", "greptime", "aspire"])
    def test_typed_entries(self, backend_type):
        rb = resolve(
            BackendConfig(
                type=backend_type,
                endpoint="http://a:4318/v1/traces",
                api_key="k",
                metrics_endpoint=M,
                logs_endpoint=L,
            )
        )
        assert rb.target("metrics").endpoint == M
        assert rb.target("logs").endpoint == L

    def test_one_signal_only_leaves_the_other_derived(self):
        rb = resolve(
            BackendConfig(type="otlp", endpoint="http://a:4318/v1/traces", logs_endpoint=L)
        )
        assert rb.target("metrics").endpoint == "http://a:4318/v1/metrics"
        assert rb.target("logs").endpoint == L

    def test_blank_values_are_ignored(self):
        rb = resolve(
            BackendConfig(
                type="otlp",
                endpoint="http://a:4318/v1/traces",
                metrics_endpoint="  ",
                logs_endpoint="",
            )
        )
        assert rb.metrics_endpoint is None and rb.logs_endpoint is None
        assert rb.target("metrics").endpoint == "http://a:4318/v1/metrics"

    def test_values_are_stripped(self):
        rb = resolve(
            BackendConfig(
                type="otlp", endpoint="http://a:4318/v1/traces", metrics_endpoint=f" {M} "
            )
        )
        assert rb.target("metrics").endpoint == M

    def test_victoria_keeps_its_own_store_paths(self):
        rb = resolve(
            BackendConfig(
                type="victoria",
                endpoint="http://localhost:10428",
                metrics_endpoint="http://localhost:8428",
                logs_endpoint="http://localhost:9428",
            )
        )
        assert rb.target("metrics").endpoint == "http://localhost:8428/opentelemetry/v1/metrics"
        assert rb.target("logs").endpoint == "http://localhost:9428/insert/opentelemetry/v1/logs"

    def test_signal_flags_are_unchanged(self):
        """The override moves a signal; it does not turn one on."""
        rb = resolve(
            BackendConfig(type="phoenix", endpoint="http://p:6006/v1/traces", metrics_endpoint=M)
        )
        assert rb.supports_metrics is False


class TestTheMetricExporterIsBuiltWithTheOverride:
    def test_http_metric_exporter_endpoint(self, monkeypatch):
        from hermes_otel import pipeline as pipeline_mod

        calls = []

        def fake_exporter(**kwargs):
            calls.append(kwargs)
            return object()

        monkeypatch.setattr(pipeline_mod, "OTLPMetricExporter", fake_exporter)
        monkeypatch.setattr(
            pipeline_mod, "PeriodicExportingMetricReader", lambda exporter, **kw: exporter
        )
        rb = resolve(
            BackendConfig(type="otlp", endpoint="http://127.0.0.1:9/v1/traces", metrics_endpoint=M)
        )
        _build_exporter("metrics", rb, HermesOtelConfig())
        assert calls[-1]["endpoint"] == M
