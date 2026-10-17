"""Unit tests for the ``aspire`` backend resolver (.NET Aspire Dashboard). No network."""

import pytest

from hermes_otel import backends
from hermes_otel.backend_config import BackendConfig
from hermes_otel.backend_specs import spec_for

EP = "http://localhost:4418"


class TestAspireBackendType:
    def test_base_endpoint_gets_traces_suffix_all_signals_no_headers(self):
        rb = backends.resolve(BackendConfig(type="aspire", endpoint=EP))
        assert rb.type == "aspire" and rb.display_name == "Aspire Dashboard"
        assert rb.endpoint == "http://localhost:4418/v1/traces"
        assert rb.headers is None
        assert (rb.supports_traces, rb.supports_metrics, rb.supports_logs) == (True, True, True)

    @pytest.mark.parametrize("suffix", ["/v1/traces", "/v1/metrics", "/v1/logs", "/"])
    def test_signal_suffixes_are_rewritten(self, suffix):
        rb = backends.resolve(BackendConfig(type="aspire", endpoint=EP + suffix))
        assert rb.endpoint == "http://localhost:4418/v1/traces"

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="aspire"))

    def test_api_key_goes_in_the_otlp_header(self, monkeypatch):
        rb = backends.resolve(BackendConfig(type="aspire", endpoint=EP, api_key="k1"))
        assert rb.headers == {"x-otlp-api-key": "k1"}
        monkeypatch.setenv("OTEL_ASPIRE_API_KEY", "envkey")
        rb = backends.resolve(BackendConfig(type="aspire", endpoint=EP))
        assert rb.headers == {"x-otlp-api-key": "envkey"}
        monkeypatch.setenv("MY_KEY", "custom")
        rb = backends.resolve(BackendConfig(type="aspire", endpoint=EP, api_key_env="MY_KEY"))
        assert rb.headers == {"x-otlp-api-key": "custom"}

    def test_user_headers_merge(self):
        rb = backends.resolve(
            BackendConfig(type="aspire", endpoint=EP, api_key="k", headers={"X-Custom": "v"})
        )
        assert rb.headers == {"x-otlp-api-key": "k", "X-Custom": "v"}

    def test_signal_overrides_and_presets(self):
        rb = backends.resolve(BackendConfig(type="aspire", endpoint=EP, metrics=False, logs=False))
        assert rb.supports_metrics is False and rb.supports_logs is False
        assert spec_for("aspire").temporality is None
        assert backends.signal_support("aspire") == {"traces": True, "metrics": True, "logs": True}

    def test_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_ASPIRE_ENDPOINT", "http://aspire:18890")
        rb = backends.resolve(BackendConfig(type="aspire"))
        assert rb.endpoint == "http://aspire:18890/v1/traces"


class TestAspireEnvMode:
    def test_endpoint_alone_selects_aspire(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_ASPIRE_ENDPOINT", EP)
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "aspire" and rb.headers is None

    def test_key_alone_selects_nothing(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_ASPIRE_API_KEY", "k")
        assert backends.resolve_from_env() is None

    def test_victoria_outranks_aspire_and_aspire_outranks_jaeger(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_JAEGER_ENDPOINT", "http://j:4318/v1/traces")
        monkeypatch.setenv("OTEL_ASPIRE_ENDPOINT", EP)
        assert backends.resolve_from_env().type == "aspire"
        monkeypatch.setenv("OTEL_VICTORIA_ENDPOINT", "http://v:10428")
        assert backends.resolve_from_env().type == "victoria"
