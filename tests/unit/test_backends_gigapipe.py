"""Unit tests for the ``gigapipe`` backend resolver (Gigapipe, formerly qryn). No network."""

import pytest

from hermes_otel import backends
from hermes_otel.backend_config import BackendConfig
from hermes_otel.backend_specs import spec_for

EP = "http://localhost:3110"


class TestGigapipeBackendType:
    def test_base_endpoint_all_signals_no_headers(self):
        rb = backends.resolve(BackendConfig(type="gigapipe", endpoint=EP))
        assert rb.type == "gigapipe" and rb.display_name == "Gigapipe"
        assert rb.endpoint == "http://localhost:3110/v1/traces"
        assert rb.headers is None
        assert (rb.supports_traces, rb.supports_metrics, rb.supports_logs) == (True, True, True)

    @pytest.mark.parametrize("suffix", ["/v1/traces", "/v1/metrics", "/v1/logs", "/"])
    def test_signal_suffixes_are_rewritten(self, suffix):
        rb = backends.resolve(BackendConfig(type="gigapipe", endpoint=EP + suffix))
        assert rb.endpoint == "http://localhost:3110/v1/traces"

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="gigapipe"))

    def test_basic_auth(self, monkeypatch):
        rb = backends.resolve(BackendConfig(type="gigapipe", endpoint=EP, user="u", password="p"))
        assert rb.headers == {"Authorization": "Basic dTpw"}
        monkeypatch.setenv("OTEL_GIGAPIPE_USER", "eu")
        monkeypatch.setenv("OTEL_GIGAPIPE_PASSWORD", "ep")
        rb = backends.resolve(BackendConfig(type="gigapipe", endpoint=EP))
        assert rb.headers == {"Authorization": "Basic ZXU6ZXA="}
        rb = backends.resolve(BackendConfig(type="gigapipe", endpoint=EP, user="only"))
        assert rb.headers == {"Authorization": "Basic b25seTplcA=="}  # inline user + env password

    def test_user_without_password_sends_nothing(self):
        rb = backends.resolve(BackendConfig(type="gigapipe", endpoint=EP, user="u"))
        assert rb.headers is None

    def test_user_headers_and_overrides(self):
        rb = backends.resolve(
            BackendConfig(
                type="gigapipe", endpoint=EP, headers={"X-Scope-Meta": "t"}, metrics=False
            )
        )
        assert rb.headers == {"X-Scope-Meta": "t"} and rb.supports_metrics is False
        assert spec_for("gigapipe").temporality is None
        assert backends.signal_support("gigapipe") == {
            "traces": True,
            "metrics": True,
            "logs": True,
        }

    def test_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_GIGAPIPE_ENDPOINT", "http://gp:3100")
        assert (
            backends.resolve(BackendConfig(type="gigapipe")).endpoint == "http://gp:3100/v1/traces"
        )


class TestGigapipeEnvMode:
    def test_endpoint_alone_selects_gigapipe(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_GIGAPIPE_ENDPOINT", EP)
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "gigapipe"

    def test_credentials_alone_select_nothing(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_GIGAPIPE_USER", "u")
        monkeypatch.setenv("OTEL_GIGAPIPE_PASSWORD", "p")
        assert backends.resolve_from_env() is None

    def test_priority_between_aspire_and_jaeger(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_JAEGER_ENDPOINT", "http://j:4318/v1/traces")
        monkeypatch.setenv("OTEL_GIGAPIPE_ENDPOINT", EP)
        assert backends.resolve_from_env().type == "gigapipe"
        monkeypatch.setenv("OTEL_ASPIRE_ENDPOINT", "http://a:4418")
        assert backends.resolve_from_env().type == "aspire"
