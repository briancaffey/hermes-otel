"""#472: exporters built without headers must not inherit ``OTEL_EXPORTER_OTLP_*HEADERS``.

The OTLP HTTP exporters do ``self._headers = headers or parse_env_headers(env)``,
so a backend entry without ``headers:`` (``headers=None``) would carry a token
exported for some other tool to every credential-less backend in the file.
The plugin hides the four variables while it constructs exporters.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from _helpers import clear_backend_env

from hermes_otel import log_handler
from hermes_otel.backends import _ResolvedBackend
from hermes_otel.helpers import OTLP_HEADER_ENV_VARS, without_otlp_header_env
from hermes_otel.plugin_config import BackendConfig, HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin

# A header the shell might export for some other tool; the name is arbitrary.
LEAK = "x-leak-token=from-the-shell"


@pytest.fixture
def leaked_env(monkeypatch):
    clear_backend_env(monkeypatch)
    for name in OTLP_HEADER_ENV_VARS:
        monkeypatch.setenv(name, LEAK)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://unrelated:4318")
    return dict(os.environ)


class TestContextManager:
    def test_hides_all_four_and_restores(self, leaked_env):
        with without_otlp_header_env():
            for name in OTLP_HEADER_ENV_VARS:
                assert name not in os.environ
            # Only the header variables are touched.
            assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://unrelated:4318"
        for name in OTLP_HEADER_ENV_VARS:
            assert os.environ[name] == LEAK

    def test_restores_when_construction_raises(self, leaked_env):
        with pytest.raises(RuntimeError):
            with without_otlp_header_env():
                raise RuntimeError("exporter init failed")
        for name in OTLP_HEADER_ENV_VARS:
            assert os.environ[name] == LEAK

    def test_noop_when_unset(self, monkeypatch):
        for name in OTLP_HEADER_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        with without_otlp_header_env():
            pass
        for name in OTLP_HEADER_ENV_VARS:
            assert name not in os.environ


class TestRealSdkExporters:
    """The SDK's own fallback, and that hiding the variables defeats it."""

    def test_sdk_falls_back_to_env_without_the_guard(self, leaked_env):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # The bug: ``headers=None`` *and* ``headers={}`` both read the shell.
        assert OTLPSpanExporter(endpoint="http://x/v1/traces", headers=None)._headers
        assert OTLPSpanExporter(endpoint="http://x/v1/traces", headers={})._headers

    @pytest.mark.parametrize("headers", [None, {}])
    def test_guarded_exporters_carry_no_env_headers(self, leaked_env, headers):
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        with without_otlp_header_env():
            span = OTLPSpanExporter(endpoint="http://x/v1/traces", headers=headers)
            metric = OTLPMetricExporter(endpoint="http://x/v1/metrics", headers=headers)
            log = OTLPLogExporter(endpoint="http://x/v1/logs", headers=headers)
        for exporter in (span, metric, log):
            assert not exporter._headers
            assert "x-leak-token" not in {k.lower() for k in exporter._session.headers}

    def test_configured_headers_still_apply(self, leaked_env):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        with without_otlp_header_env():
            exporter = OTLPSpanExporter(endpoint="http://x/v1/traces", headers={"x-a": "1"})
        assert exporter._headers == {"x-a": "1"}


def _env_at_construction(seen: dict):
    """A stand-in exporter that records the header env as it was when built."""

    def factory(endpoint=None, headers=None, **_kw):
        seen[endpoint] = {
            "headers": headers,
            "env": {n: os.environ.get(n) for n in OTLP_HEADER_ENV_VARS},
        }
        return MagicMock()

    return factory


class TestPipeline:
    def test_span_and_metric_exporters_built_with_env_hidden(self, leaked_env):
        seen: dict = {}
        cfg = HermesOtelConfig(
            backends=(
                BackendConfig(type="jaeger", endpoint="http://jaeger:4318/v1/traces"),
                BackendConfig(
                    type="otlp",
                    endpoint="http://oo:5080/api/default/v1/traces",
                    headers={"Authorization": "Basic abc"},
                    metrics=True,
                ),
            ),
        )
        plugin = HermesOTelPlugin(config=cfg)
        with (
            patch("hermes_otel.pipeline.OTLPSpanExporter", side_effect=_env_at_construction(seen)),
            patch(
                "hermes_otel.pipeline.OTLPMetricExporter", side_effect=_env_at_construction(seen)
            ),
            patch("hermes_otel.pipeline.trace.set_tracer_provider"),
            patch("hermes_otel.pipeline.metrics.set_meter_provider"),
        ):
            assert plugin.init() is True

        assert len(seen) == 3  # two trace exporters + one metric exporter
        for endpoint, record in seen.items():
            assert record["env"] == {n: None for n in OTLP_HEADER_ENV_VARS}, endpoint
        assert seen["http://jaeger:4318/v1/traces"]["headers"] is None
        assert seen["http://oo:5080/api/default/v1/traces"]["headers"] == {
            "Authorization": "Basic abc"
        }
        # Restored afterwards: the shell's variables are not the plugin's to drop.
        for name in OTLP_HEADER_ENV_VARS:
            assert os.environ[name] == LEAK

    def test_log_exporter_built_with_env_hidden(self, leaked_env):
        seen: dict = {}
        backends = [
            _ResolvedBackend(type="otlp", endpoint="http://otlp:4318/v1/traces", supports_logs=True)
        ]
        with patch.object(log_handler, "OTLPLogExporter", side_effect=_env_at_construction(seen)):
            procs = log_handler.build_log_processors(backends)
        assert len(procs) == 1
        (record,) = seen.values()
        assert record["headers"] is None
        assert record["env"] == {n: None for n in OTLP_HEADER_ENV_VARS}
        for name in OTLP_HEADER_ENV_VARS:
            assert os.environ[name] == LEAK
