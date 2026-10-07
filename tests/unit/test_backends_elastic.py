"""Unit tests for the ``elastic`` backend resolver.

No network: these assert the resolved endpoint, headers, temporality preset,
and signal-support flags only. Live export against a real Elastic deployment
is verified manually in the Elastic UI rather than here.
"""

import pytest

from hermes_otel import backends
from hermes_otel.plugin_config import BackendConfig


class TestElasticBackendType:
    def test_cloud_endpoint_gets_traces_suffix(self):
        rb = backends.resolve(
            BackendConfig(
                type="elastic",
                endpoint="https://hash.apm.eu-west-1.gcp.elastic-cloud.com:443",
                api_key="key_abc",
            )
        )
        assert rb.type == "elastic"
        assert rb.display_name == "Elastic"
        assert rb.endpoint == "https://hash.apm.eu-west-1.gcp.elastic-cloud.com:443/v1/traces"
        # ApiKey scheme, NOT Bearer.
        assert rb.headers["Authorization"] == "ApiKey key_abc"
        assert rb.supports_traces is True
        assert rb.supports_metrics is True
        assert rb.supports_logs is True

    def test_full_traces_endpoint_left_alone(self):
        rb = backends.resolve(
            BackendConfig(type="elastic", endpoint="http://localhost:4318/v1/traces")
        )
        assert rb.endpoint == "http://localhost:4318/v1/traces"

    def test_trailing_slash_stripped(self):
        rb = backends.resolve(BackendConfig(type="elastic", endpoint="http://localhost:4318/"))
        assert rb.endpoint == "http://localhost:4318/v1/traces"

    def test_no_key_omits_authorization_header(self):
        # A self-hosted EDOT Collector on a trusted network runs without auth.
        rb = backends.resolve(BackendConfig(type="elastic", endpoint="http://localhost:4318"))
        assert "Authorization" not in rb.headers
        assert rb.headers == {}

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="elastic", api_key="k"))

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_ELASTIC_API_KEY", "env_key")
        rb = backends.resolve(BackendConfig(type="elastic", endpoint="http://localhost:4318"))
        assert rb.headers["Authorization"] == "ApiKey env_key"

    def test_api_key_env_fallback_name(self, monkeypatch):
        monkeypatch.setenv("ELASTIC_API_KEY", "fallback_key")
        rb = backends.resolve(BackendConfig(type="elastic", endpoint="http://localhost:4318"))
        assert rb.headers["Authorization"] == "ApiKey fallback_key"

    def test_api_key_env_field_selects_var(self, monkeypatch):
        monkeypatch.setenv("MY_ELASTIC_KEY", "custom_key")
        rb = backends.resolve(
            BackendConfig(
                type="elastic", endpoint="http://localhost:4318", api_key_env="MY_ELASTIC_KEY"
            )
        )
        assert rb.headers["Authorization"] == "ApiKey custom_key"

    def test_inline_api_key_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_ELASTIC_API_KEY", "env_key")
        rb = backends.resolve(
            BackendConfig(type="elastic", endpoint="http://localhost:4318", api_key="inline")
        )
        assert rb.headers["Authorization"] == "ApiKey inline"

    def test_dataset_and_namespace_become_resource_attributes(self):
        rb = backends.resolve(
            BackendConfig(
                type="elastic",
                endpoint="http://localhost:4318",
                dataset="hermes-otel",
                namespace="agents",
            )
        )
        assert rb.resource_attributes == {
            "data_stream.dataset": "hermes-otel",
            "data_stream.namespace": "agents",
        }

    def test_no_dataset_no_resource_attributes(self):
        rb = backends.resolve(BackendConfig(type="elastic", endpoint="http://localhost:4318"))
        assert rb.resource_attributes is None

    def test_user_headers_do_not_clobber_auth(self):
        rb = backends.resolve(
            BackendConfig(
                type="elastic",
                endpoint="http://localhost:4318",
                api_key="k",
                headers={"X-Custom": "v"},
            )
        )
        assert rb.headers["Authorization"] == "ApiKey k"
        assert rb.headers["X-Custom"] == "v"

    def test_delta_temporality_preset(self):
        # ES does not handle cumulative histograms; elastic defaults to delta.
        assert backends.preset_temporality("elastic") == "delta"
        rb = backends.resolve(BackendConfig(type="elastic", endpoint="http://localhost:4318"))
        assert rb.metrics_temporality == "delta"

    def test_metrics_temporality_override_wins(self):
        rb = backends.resolve(
            BackendConfig(
                type="elastic",
                endpoint="http://localhost:4318",
                metrics_temporality="cumulative",
            )
        )
        assert rb.metrics_temporality == "cumulative"

    def test_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_ELASTIC_ENDPOINT", "http://collector:4318")
        rb = backends.resolve(BackendConfig(type="elastic"))
        assert rb.endpoint == "http://collector:4318/v1/traces"

    def test_endpoint_field_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_ELASTIC_ENDPOINT", "http://env:4318")
        rb = backends.resolve(BackendConfig(type="elastic", endpoint="http://file:4318"))
        assert rb.endpoint == "http://file:4318/v1/traces"


class TestElasticEnvMode:
    def test_opt_in_not_satisfied_without_elastic_vars(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        assert backends.resolve_from_env() is None

    def test_opt_in_endpoint_and_key_select_elastic(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv(
            "OTEL_ELASTIC_ENDPOINT", "https://hash.apm.eu-west-1.gcp.elastic-cloud.com:443"
        )
        monkeypatch.setenv("OTEL_ELASTIC_API_KEY", "k")
        rb = backends.resolve_from_env()
        assert rb is not None
        assert rb.type == "elastic"

    def test_opt_in_endpoint_alone_selects_elastic(self, monkeypatch):
        # A local EDOT collector: endpoint set, no key required.
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_ELASTIC_ENDPOINT", "http://localhost:4318")
        rb = backends.resolve_from_env()
        assert rb is not None
        assert rb.type == "elastic"

    def test_vendor_key_alone_does_not_select_elastic(self, monkeypatch):
        # ELASTIC_API_KEY without an OTEL_* opt-in must not switch export on.
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("ELASTIC_API_KEY", "vendor_key")
        assert backends.resolve_from_env() is None
