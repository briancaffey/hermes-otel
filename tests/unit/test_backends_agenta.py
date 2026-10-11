"""Unit tests for the ``agenta`` backend resolver. No network."""

import pytest

from hermes_otel import backends
from hermes_otel.plugin_config import BackendConfig

EP = "http://localhost:3060"


class TestAgentaBackendType:
    def test_self_hosted_origin_gets_the_vendor_path_and_apikey_header(self):
        rb = backends.resolve(BackendConfig(type="agenta", endpoint=EP, api_key="ak"))
        assert rb.type == "agenta" and rb.display_name == "Agenta"
        assert rb.endpoint == "http://localhost:3060/api/otlp/v1/traces"
        assert rb.headers == {"Authorization": "ApiKey ak"}
        assert (rb.supports_traces, rb.supports_metrics, rb.supports_logs) == (True, False, False)

    @pytest.mark.parametrize(
        "suffix", ["/", "/api", "/api/", "/api/otlp", "/api/otlp/v1/traces", "/api/otlp/v1/traces/"]
    )
    def test_spellings(self, suffix):
        rb = backends.resolve(BackendConfig(type="agenta", endpoint=EP + suffix, api_key="k"))
        assert rb.endpoint == "http://localhost:3060/api/otlp/v1/traces"

    def test_cloud_default_hosts(self):
        rb = backends.resolve(BackendConfig(type="agenta", api_key="k"))
        assert rb.endpoint == "https://us.cloud.agenta.ai/api/otlp/v1/traces"
        rb = backends.resolve(BackendConfig(type="agenta", api_key="k", region="eu"))
        assert rb.endpoint == "https://eu.cloud.agenta.ai/api/otlp/v1/traces"

    def test_missing_key_raises_so_the_entry_is_skipped(self):
        with pytest.raises(ValueError, match="api_key"):
            backends.resolve(BackendConfig(type="agenta", endpoint=EP))

    def test_key_sources(self, monkeypatch):
        monkeypatch.setenv("OTEL_AGENTA_API_KEY", "plugin")
        assert backends.resolve(BackendConfig(type="agenta", endpoint=EP)).headers == {
            "Authorization": "ApiKey plugin"
        }
        monkeypatch.delenv("OTEL_AGENTA_API_KEY")
        monkeypatch.setenv("AGENTA_API_KEY", "sdk")
        assert backends.resolve(BackendConfig(type="agenta", endpoint=EP)).headers == {
            "Authorization": "ApiKey sdk"
        }
        monkeypatch.setenv("MY_AG", "custom")
        assert backends.resolve(
            BackendConfig(type="agenta", endpoint=EP, api_key_env="MY_AG")
        ).headers == {"Authorization": "ApiKey custom"}

    def test_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_AGENTA_ENDPOINT", "http://ag:3060")
        assert (
            backends.resolve(BackendConfig(type="agenta", api_key="k")).endpoint
            == "http://ag:3060/api/otlp/v1/traces"
        )
        monkeypatch.delenv("OTEL_AGENTA_ENDPOINT")
        monkeypatch.setenv("AGENTA_HOST", "http://sdk-host")
        assert (
            backends.resolve(BackendConfig(type="agenta", api_key="k")).endpoint
            == "http://sdk-host/api/otlp/v1/traces"
        )

    def test_user_headers_merge_and_signals(self):
        rb = backends.resolve(
            BackendConfig(
                type="agenta", endpoint=EP, api_key="k", headers={"X-A": "1"}, metrics=True
            )
        )
        assert rb.headers == {"Authorization": "ApiKey k", "X-A": "1"}
        assert rb.supports_metrics is True  # a collector in front could take them
        assert backends.preset_temporality("agenta") is None
        assert backends.signal_support("agenta") == {
            "traces": True,
            "metrics": False,
            "logs": False,
        }
        assert rb.root_usage is True


class TestAgentaEnvMode:
    def test_plugin_key_alone_selects_cloud(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_AGENTA_API_KEY", "k")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "agenta"
        assert rb.endpoint == "https://us.cloud.agenta.ai/api/otlp/v1/traces"

    def test_sdk_key_alone_is_not_an_opt_in(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("AGENTA_API_KEY", "sdk")
        assert backends.resolve_from_env() is None
        assert any(
            "AGENTA_API_KEY" in h and "OTEL_AGENTA_API_KEY" in h
            for h in backends.env_opt_in_hints()
        )

    def test_endpoint_opt_in_with_sdk_key(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_AGENTA_ENDPOINT", EP)
        monkeypatch.setenv("AGENTA_API_KEY", "sdk")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.endpoint == "http://localhost:3060/api/otlp/v1/traces"

    def test_latitude_outranks_agenta_and_agenta_outranks_clickstack(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_CLICKSTACK_ENDPOINT", "http://c:4408")
        monkeypatch.setenv("OTEL_CLICKSTACK_API_KEY", "c")
        monkeypatch.setenv("OTEL_AGENTA_API_KEY", "a")
        assert backends.resolve_from_env().type == "agenta"
        monkeypatch.setenv("OTEL_LATITUDE_API_KEY", "l")
        monkeypatch.setenv("LATITUDE_PROJECT", "p")
        assert backends.resolve_from_env().type == "latitude"
