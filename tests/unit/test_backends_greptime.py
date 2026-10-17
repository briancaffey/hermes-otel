"""Unit tests for the ``greptime`` backend resolver.

No network: these assert the resolved endpoint, the per-signal headers and the
signal flags only. The live path is verified against the compose stack in
docker-compose/greptime (GreptimeDB rejects traces without the pipeline header
and logs with it, so the header split is the whole point of the type).
"""

import base64

import pytest

from hermes_otel import backends
from hermes_otel.backend_specs import spec_for
from hermes_otel.plugin_config import BackendConfig

PIPELINE = {"X-Greptime-Pipeline-Name": "greptime_trace_v1"}


def _b64(user: str, pw: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


class TestGreptimeBackendType:
    def test_origin_gets_the_otlp_prefix_and_traces_suffix(self):
        rb = backends.resolve(BackendConfig(type="greptime", endpoint="http://localhost:4398"))
        assert rb.type == "greptime"
        assert rb.display_name == "GreptimeDB"
        assert rb.endpoint == "http://localhost:4398/v1/otlp/v1/traces"
        assert rb.supports_traces is True
        assert rb.supports_metrics is True
        assert rb.supports_logs is True

    @pytest.mark.parametrize(
        "given",
        [
            "http://localhost:4398/",
            "http://localhost:4398/v1/otlp",
            "http://localhost:4398/v1/otlp/",
            "http://localhost:4398/v1/otlp/v1/traces",
            "http://localhost:4398/v1/otlp/v1/metrics",
            "http://localhost:4398/v1/otlp/v1/logs",
        ],
    )
    def test_every_spelling_resolves_to_the_traces_url(self, given):
        rb = backends.resolve(BackendConfig(type="greptime", endpoint=given))
        assert rb.endpoint == "http://localhost:4398/v1/otlp/v1/traces"

    def test_hostname_without_port(self):
        rb = backends.resolve(
            BackendConfig(type="greptime", endpoint="https://greptime.example.com")
        )
        assert rb.endpoint == "https://greptime.example.com/v1/otlp/v1/traces"

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="greptime"))

    def test_traces_carry_the_pipeline_header_and_the_other_signals_do_not(self):
        rb = backends.resolve(BackendConfig(type="greptime", endpoint="http://localhost:4398"))
        assert rb.headers == PIPELINE
        # Empty, not None: the exporters must not fall back to the trace headers,
        # because GreptimeDB answers 400 "Unsupported pipeline for logs".
        assert rb.metrics_headers == {}
        assert rb.logs_headers == {}

    def test_db_goes_on_every_signal(self):
        rb = backends.resolve(
            BackendConfig(type="greptime", endpoint="http://localhost:4398", db="otel")
        )
        assert rb.headers == {**PIPELINE, "X-Greptime-DB-Name": "otel"}
        assert rb.metrics_headers == {"X-Greptime-DB-Name": "otel"}
        assert rb.logs_headers == {"X-Greptime-DB-Name": "otel"}

    def test_db_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_GREPTIME_DB", "envdb")
        rb = backends.resolve(BackendConfig(type="greptime", endpoint="http://localhost:4398"))
        assert rb.logs_headers == {"X-Greptime-DB-Name": "envdb"}

    def test_db_env_field_selects_var(self, monkeypatch):
        monkeypatch.setenv("MY_DB", "mine")
        rb = backends.resolve(
            BackendConfig(type="greptime", endpoint="http://localhost:4398", db_env="MY_DB")
        )
        assert rb.metrics_headers == {"X-Greptime-DB-Name": "mine"}

    def test_basic_auth_from_user_and_password(self):
        rb = backends.resolve(
            BackendConfig(type="greptime", endpoint="http://h:4000", user="u", password="p")
        )
        assert rb.headers["Authorization"] == _b64("u", "p")
        assert rb.metrics_headers["Authorization"] == _b64("u", "p")
        assert rb.logs_headers["Authorization"] == _b64("u", "p")

    def test_basic_auth_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_GREPTIME_USER", "eu")
        monkeypatch.setenv("OTEL_GREPTIME_PASSWORD", "ep")
        rb = backends.resolve(BackendConfig(type="greptime", endpoint="http://h:4000"))
        assert rb.headers["Authorization"] == _b64("eu", "ep")

    def test_user_without_password_sends_no_auth(self):
        rb = backends.resolve(BackendConfig(type="greptime", endpoint="http://h:4000", user="u"))
        assert "Authorization" not in rb.headers
        assert rb.metrics_headers == {}

    def test_user_headers_are_merged_on_every_signal(self):
        rb = backends.resolve(
            BackendConfig(
                type="greptime", endpoint="http://h:4000", headers={"x-greptime-hints": "ttl=7d"}
            )
        )
        assert rb.headers == {**PIPELINE, "x-greptime-hints": "ttl=7d"}
        assert rb.metrics_headers == {"x-greptime-hints": "ttl=7d"}
        assert rb.logs_headers == {"x-greptime-hints": "ttl=7d"}

    def test_user_pipeline_header_wins_on_traces_only(self):
        rb = backends.resolve(
            BackendConfig(
                type="greptime",
                endpoint="http://h:4000",
                headers={"X-Greptime-Pipeline-Name": "greptime_trace_v2"},
            )
        )
        assert rb.headers == {"X-Greptime-Pipeline-Name": "greptime_trace_v2"}
        # A user-supplied pipeline header is passed through on every signal, as
        # every entry in ``headers`` is; GreptimeDB then decides.
        assert rb.logs_headers == {"X-Greptime-Pipeline-Name": "greptime_trace_v2"}

    def test_signal_overrides(self):
        rb = backends.resolve(
            BackendConfig(type="greptime", endpoint="http://h:4000", metrics=False, logs=False)
        )
        assert rb.supports_metrics is False
        assert rb.supports_logs is False

    def test_no_temporality_preset(self):
        assert spec_for("greptime").temporality is None

    def test_signal_support_table(self):
        assert backends.signal_support("greptime") == {
            "traces": True,
            "metrics": True,
            "logs": True,
        }

    def test_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_GREPTIME_ENDPOINT", "http://greptime:4000")
        rb = backends.resolve(BackendConfig(type="greptime"))
        assert rb.endpoint == "http://greptime:4000/v1/otlp/v1/traces"


class TestGreptimeEnvMode:
    def test_nothing_set_selects_nothing(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        assert backends.resolve_from_env() is None

    def test_endpoint_alone_selects_greptime(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_GREPTIME_ENDPOINT", "http://localhost:4398")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "greptime"
        assert rb.endpoint == "http://localhost:4398/v1/otlp/v1/traces"

    def test_credentials_alone_do_not_select_greptime(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_GREPTIME_USER", "u")
        monkeypatch.setenv("OTEL_GREPTIME_PASSWORD", "p")
        assert backends.resolve_from_env() is None

    def test_greptime_outranks_jaeger_in_env_priority(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_JAEGER_ENDPOINT", "http://j:4318/v1/traces")
        monkeypatch.setenv("OTEL_GREPTIME_ENDPOINT", "http://g:4000")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "greptime"


class TestGreptimeMetricExporterHeaders:
    def test_metric_exporter_gets_the_empty_per_signal_headers(self, monkeypatch, tmp_path):
        """The tracer must honour an empty ``metrics_headers`` (#363): GreptimeDB's
        trace pipeline header would otherwise be sent to ``/v1/otlp/v1/metrics``."""
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        from hermes_otel import pipeline as pipeline_mod
        from hermes_otel import tracer as tracer_mod
        from hermes_otel.plugin_config import HermesOtelConfig

        metric_calls = []
        span_calls = []

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

        monkeypatch.setattr(pipeline_mod, "OTLPMetricExporter", FakeMetricExporter)
        monkeypatch.setattr(pipeline_mod, "OTLPSpanExporter", FakeSpanExporter)
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
            backends=[BackendConfig(type="greptime", endpoint="http://127.0.0.1:9", db="otel")],
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
        assert metric_calls, "no metric exporter was created"
        assert metric_calls[-1]["endpoint"] == "http://127.0.0.1:9/v1/otlp/v1/metrics"
        assert metric_calls[-1]["headers"] == {"X-Greptime-DB-Name": "otel"}
        assert span_calls[-1]["headers"]["X-Greptime-Pipeline-Name"] == "greptime_trace_v1"
