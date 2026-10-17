"""Unit tests for the ``victoria`` backend resolver (VictoriaTraces +
VictoriaMetrics + VictoriaLogs) and the per-signal endpoints it introduced on
``_ResolvedBackend`` (#366). No network."""

import pytest

from hermes_otel import backends
from hermes_otel.backend_specs import spec_for
from hermes_otel.plugin_config import BackendConfig

VT, VM, VL = "http://localhost:10428", "http://localhost:8428", "http://localhost:9428"


class TestVictoriaBackendType:
    def test_three_origins_become_three_urls(self):
        rb = backends.resolve(
            BackendConfig(type="victoria", endpoint=VT, metrics_endpoint=VM, logs_endpoint=VL)
        )
        assert rb.type == "victoria" and rb.display_name == "Victoria"
        assert rb.endpoint == "http://localhost:10428/insert/opentelemetry/v1/traces"
        assert rb.metrics_endpoint == "http://localhost:8428/opentelemetry/v1/metrics"
        assert rb.logs_endpoint == "http://localhost:9428/insert/opentelemetry/v1/logs"
        assert (rb.supports_traces, rb.supports_metrics, rb.supports_logs) == (True, True, True)

    @pytest.mark.parametrize(
        "given",
        [
            VT,
            VT + "/",
            VT + "/insert/opentelemetry/v1/traces",
            VT + "/v1/traces",
            VT + "/insert/opentelemetry/v1/metrics",
        ],
    )
    def test_traces_spellings(self, given):
        rb = backends.resolve(BackendConfig(type="victoria", endpoint=given))
        assert rb.endpoint == "http://localhost:10428/insert/opentelemetry/v1/traces"

    def test_metrics_and_logs_spellings(self):
        rb = backends.resolve(
            BackendConfig(
                type="victoria",
                endpoint=VT,
                metrics_endpoint=VM + "/opentelemetry/v1/metrics",
                logs_endpoint=VL + "/insert/opentelemetry/v1/logs/",
            )
        )
        assert rb.metrics_endpoint == "http://localhost:8428/opentelemetry/v1/metrics"
        assert rb.logs_endpoint == "http://localhost:9428/insert/opentelemetry/v1/logs"

    def test_unnamed_stores_switch_their_signal_off(self):
        rb = backends.resolve(BackendConfig(type="victoria", endpoint=VT))
        assert rb.supports_traces is True
        assert rb.supports_metrics is False and rb.metrics_endpoint is None
        assert rb.supports_logs is False and rb.logs_endpoint is None

    def test_explicit_signal_without_its_store_is_an_error(self):
        with pytest.raises(ValueError, match="metrics_endpoint"):
            backends.resolve(BackendConfig(type="victoria", endpoint=VT, metrics=True))
        with pytest.raises(ValueError, match="logs_endpoint"):
            backends.resolve(BackendConfig(type="victoria", endpoint=VT, logs=True))

    def test_signal_overrides_off(self):
        rb = backends.resolve(
            BackendConfig(
                type="victoria", endpoint=VT, metrics_endpoint=VM, logs_endpoint=VL, metrics=False
            )
        )
        assert rb.supports_metrics is False and rb.supports_logs is True

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="victoria"))

    def test_logs_carry_the_stream_fields_header_only(self):
        rb = backends.resolve(
            BackendConfig(type="victoria", endpoint=VT, metrics_endpoint=VM, logs_endpoint=VL)
        )
        assert rb.headers == {} and rb.metrics_headers == {}
        assert rb.logs_headers == {"VL-Stream-Fields": "service.name"}

    def test_user_headers_merge_everywhere_and_may_override_the_stream_fields(self):
        rb = backends.resolve(
            BackendConfig(
                type="victoria",
                endpoint=VT,
                logs_endpoint=VL,
                headers={"VL-Stream-Fields": "service.name,hermes.profile", "X-Scope": "t"},
            )
        )
        assert rb.headers == {"VL-Stream-Fields": "service.name,hermes.profile", "X-Scope": "t"}
        assert rb.logs_headers["VL-Stream-Fields"] == "service.name,hermes.profile"

    def test_basic_auth_on_every_signal(self, monkeypatch):
        monkeypatch.setenv("OTEL_VICTORIA_USER", "u")
        monkeypatch.setenv("OTEL_VICTORIA_PASSWORD", "p")
        rb = backends.resolve(
            BackendConfig(type="victoria", endpoint=VT, metrics_endpoint=VM, logs_endpoint=VL)
        )
        assert rb.headers["Authorization"] == "Basic dTpw"
        assert rb.metrics_headers["Authorization"] == "Basic dTpw"
        assert rb.logs_headers["Authorization"] == "Basic dTpw"

    def test_env_endpoints(self, monkeypatch):
        monkeypatch.setenv("OTEL_VICTORIA_ENDPOINT", "http://vt:10428")
        monkeypatch.setenv("OTEL_VICTORIA_METRICS_ENDPOINT", "http://vm:8428")
        monkeypatch.setenv("OTEL_VICTORIA_LOGS_ENDPOINT", "http://vl:9428")
        rb = backends.resolve(BackendConfig(type="victoria"))
        assert rb.endpoint == "http://vt:10428/insert/opentelemetry/v1/traces"
        assert rb.metrics_endpoint == "http://vm:8428/opentelemetry/v1/metrics"
        assert rb.logs_endpoint == "http://vl:9428/insert/opentelemetry/v1/logs"

    def test_no_temporality_preset_and_signal_table(self):
        assert spec_for("victoria").temporality is None
        assert backends.signal_support("victoria") == {
            "traces": True,
            "metrics": True,
            "logs": True,
        }


class TestVictoriaEnvMode:
    def test_traces_endpoint_alone_selects_victoria(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_VICTORIA_ENDPOINT", VT)
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "victoria"
        assert rb.supports_metrics is False and rb.supports_logs is False

    def test_greptime_outranks_victoria(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_GREPTIME_ENDPOINT", "http://g:4000")
        monkeypatch.setenv("OTEL_VICTORIA_ENDPOINT", VT)
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "greptime"

    def test_victoria_outranks_jaeger(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_JAEGER_ENDPOINT", "http://j:4318/v1/traces")
        monkeypatch.setenv("OTEL_VICTORIA_ENDPOINT", VT)
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "victoria"


class TestPerSignalEndpointsReachTheExporters:
    def test_metric_and_log_exporters_use_the_named_stores(self, monkeypatch, tmp_path):
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        from hermes_otel import log_handler
        from hermes_otel import pipeline as pipeline_mod
        from hermes_otel import tracer as tracer_mod
        from hermes_otel.plugin_config import HermesOtelConfig

        metric_calls, span_calls, log_calls = [], [], []

        class FakeMetricExporter:
            def __init__(self, **kwargs):
                metric_calls.append(kwargs)

            @property
            def _preferred_temporality(self):
                return metric_calls[-1].get("preferred_temporality") or {}

            @property
            def _preferred_aggregation(self):
                return metric_calls[-1].get("preferred_aggregation") or {}

        class FakeSpanExporter:
            def __init__(self, **kwargs):
                span_calls.append(kwargs)

            def export(self, *a, **k):
                from opentelemetry.sdk.trace.export import SpanExportResult

                return SpanExportResult.SUCCESS

            def force_flush(self, *a, **k):
                return True

            def shutdown(self, *a, **k):
                return None

        class FakeLogExporter:
            def __init__(self, **kwargs):
                log_calls.append(kwargs)

            def export(self, *a, **k):
                return None

            def force_flush(self, *a, **k):
                return True

            def shutdown(self, *a, **k):
                return None

        monkeypatch.setattr(pipeline_mod, "OTLPMetricExporter", FakeMetricExporter)
        monkeypatch.setattr(pipeline_mod, "OTLPSpanExporter", FakeSpanExporter)
        monkeypatch.setattr(log_handler, "OTLPLogExporter", FakeLogExporter)
        monkeypatch.setattr(
            pipeline_mod,
            "PeriodicExportingMetricReader",
            lambda exporter, **kw: InMemoryMetricReader(
                preferred_temporality=exporter._preferred_temporality,
                preferred_aggregation=exporter._preferred_aggregation,
            ),
        )
        monkeypatch.setattr(
            "hermes_otel.live_store._default_db_path", lambda: str(tmp_path / "l.db")
        )
        cfg = HermesOtelConfig(
            backends=[
                BackendConfig(
                    type="victoria",
                    endpoint="http://127.0.0.1:9",
                    metrics_endpoint="http://127.0.0.1:8",
                    logs_endpoint="http://127.0.0.1:7",
                )
            ],
            capture_logs=True,
            dashboard_live=False,
            host_metrics=False,
        )
        plugin = tracer_mod.HermesOTelPlugin(config=cfg)
        try:
            assert plugin.init() is True
        finally:
            try:
                plugin.shutdown()
            except Exception:
                pass
            log_handler.uninstall_handler(None)
        assert span_calls[-1]["endpoint"] == "http://127.0.0.1:9/insert/opentelemetry/v1/traces"
        assert metric_calls[-1]["endpoint"] == "http://127.0.0.1:8/opentelemetry/v1/metrics"
        assert log_calls, "no log exporter was created"
        assert log_calls[-1]["endpoint"] == "http://127.0.0.1:7/insert/opentelemetry/v1/logs"
        assert log_calls[-1]["headers"]["VL-Stream-Fields"] == "service.name"
