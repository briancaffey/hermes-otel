"""Unit tests for the ``openlit`` backend resolver.

No network: these assert the resolved endpoint, headers and signal flags only.
The live path is verified against the compose stack in docker-compose/openlit.
"""

import pytest

from hermes_otel import backends
from hermes_otel.plugin_config import BackendConfig


class TestOpenlitBackendType:
    def test_base_endpoint_gets_traces_suffix(self):
        rb = backends.resolve(BackendConfig(type="openlit", endpoint="http://localhost:4318"))
        assert rb.type == "openlit"
        assert rb.display_name == "OpenLIT"
        assert rb.endpoint == "http://localhost:4318/v1/traces"
        assert rb.supports_traces is True
        assert rb.supports_metrics is True
        assert rb.supports_logs is True

    def test_full_traces_endpoint_left_alone(self):
        rb = backends.resolve(
            BackendConfig(type="openlit", endpoint="http://localhost:4318/v1/traces")
        )
        assert rb.endpoint == "http://localhost:4318/v1/traces"

    def test_metrics_or_logs_suffix_is_rewritten_to_traces(self):
        for suffix in ("/v1/metrics", "/v1/logs", "/v1/traces/", "/"):
            rb = backends.resolve(
                BackendConfig(type="openlit", endpoint=f"http://localhost:4318{suffix}")
            )
            assert rb.endpoint == "http://localhost:4318/v1/traces"

    def test_hostname_without_port_gets_the_suffix(self):
        # A reverse-proxied deployment names no port; the suffix still applies.
        rb = backends.resolve(BackendConfig(type="openlit", endpoint="https://openlit.example.com"))
        assert rb.endpoint == "https://openlit.example.com/v1/traces"

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="openlit"))

    def test_no_key_omits_authorization_header(self):
        rb = backends.resolve(BackendConfig(type="openlit", endpoint="http://localhost:4318"))
        assert rb.headers == {}

    def test_inline_api_key_is_a_bearer_token(self):
        rb = backends.resolve(
            BackendConfig(type="openlit", endpoint="http://localhost:4318", api_key="olk_abc")
        )
        assert rb.headers["Authorization"] == "Bearer olk_abc"

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_OPENLIT_API_KEY", "env_key")
        rb = backends.resolve(BackendConfig(type="openlit", endpoint="http://localhost:4318"))
        assert rb.headers["Authorization"] == "Bearer env_key"

    def test_api_key_env_fallback_name(self, monkeypatch):
        monkeypatch.setenv("OPENLIT_API_KEY", "fallback_key")
        rb = backends.resolve(BackendConfig(type="openlit", endpoint="http://localhost:4318"))
        assert rb.headers["Authorization"] == "Bearer fallback_key"

    def test_api_key_env_field_selects_var(self, monkeypatch):
        monkeypatch.setenv("MY_OPENLIT_KEY", "custom")
        rb = backends.resolve(
            BackendConfig(
                type="openlit", endpoint="http://localhost:4318", api_key_env="MY_OPENLIT_KEY"
            )
        )
        assert rb.headers["Authorization"] == "Bearer custom"

    def test_inline_key_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_OPENLIT_API_KEY", "env_key")
        rb = backends.resolve(
            BackendConfig(type="openlit", endpoint="http://localhost:4318", api_key="inline")
        )
        assert rb.headers["Authorization"] == "Bearer inline"

    def test_user_headers_are_merged_on_top_of_auth(self):
        rb = backends.resolve(
            BackendConfig(
                type="openlit",
                endpoint="http://localhost:4318",
                api_key="k",
                headers={"X-Custom": "v"},
            )
        )
        assert rb.headers == {"Authorization": "Bearer k", "X-Custom": "v"}

    def test_user_authorization_header_wins_over_api_key(self):
        rb = backends.resolve(
            BackendConfig(
                type="openlit",
                endpoint="http://localhost:4318",
                api_key="k",
                headers={"Authorization": "Bearer other"},
            )
        )
        assert rb.headers["Authorization"] == "Bearer other"

    def test_signal_overrides(self):
        rb = backends.resolve(
            BackendConfig(
                type="openlit", endpoint="http://localhost:4318", metrics=False, logs=False
            )
        )
        assert rb.supports_metrics is False
        assert rb.supports_logs is False

    def test_no_temporality_preset(self):
        # OpenLIT stores cumulative metrics as-is; the global default applies.
        assert backends.preset_temporality("openlit") is None

    def test_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_OPENLIT_ENDPOINT", "http://openlit:4318")
        rb = backends.resolve(BackendConfig(type="openlit"))
        assert rb.endpoint == "http://openlit:4318/v1/traces"

    def test_endpoint_field_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_OPENLIT_ENDPOINT", "http://env:4318")
        rb = backends.resolve(BackendConfig(type="openlit", endpoint="http://file:4318"))
        assert rb.endpoint == "http://file:4318/v1/traces"


class TestOpenlitEnvMode:
    def test_nothing_set_selects_nothing(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        assert backends.resolve_from_env() is None

    def test_endpoint_alone_selects_openlit(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_OPENLIT_ENDPOINT", "http://localhost:4318")
        rb = backends.resolve_from_env()
        assert rb is not None
        assert rb.type == "openlit"
        assert rb.headers == {}

    def test_endpoint_and_key_select_openlit_with_bearer(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_OPENLIT_ENDPOINT", "http://localhost:4318")
        monkeypatch.setenv("OTEL_OPENLIT_API_KEY", "k")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.headers["Authorization"] == "Bearer k"

    def test_otel_key_alone_does_not_select_openlit(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_OPENLIT_API_KEY", "k")
        assert backends.resolve_from_env() is None

    def test_vendor_key_alone_does_not_select_openlit(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OPENLIT_API_KEY", "vendor")
        assert backends.resolve_from_env() is None

    def test_elastic_outranks_openlit_in_env_priority(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_ELASTIC_ENDPOINT", "http://e:4318")
        monkeypatch.setenv("OTEL_OPENLIT_ENDPOINT", "http://o:4318")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "elastic"
