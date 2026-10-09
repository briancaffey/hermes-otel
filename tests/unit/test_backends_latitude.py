"""Unit tests for the ``latitude`` backend resolver (bearer key + project header, traces only)."""

import pytest

from hermes_otel import backends
from hermes_otel.plugin_config import BackendConfig


def _cfg(**kw):
    base = dict(
        type="latitude", endpoint="http://localhost:3002", api_key="lat_k", project="my-proj"
    )
    base.update(kw)
    return BackendConfig(**base)


class TestLatitudeBackendType:
    def test_self_host_defaults(self):
        rb = backends.resolve(_cfg())
        assert rb.type == "latitude"
        assert rb.display_name == "Latitude"
        assert rb.endpoint == "http://localhost:3002/v1/traces"
        assert rb.headers == {"Authorization": "Bearer lat_k", "X-Latitude-Project": "my-proj"}
        assert rb.supports_traces is True
        assert rb.supports_metrics is False
        assert rb.supports_logs is False

    def test_cloud_default_when_no_endpoint(self):
        rb = backends.resolve(_cfg(endpoint=None))
        assert rb.endpoint == "https://ingest.latitude.so/v1/traces"

    @pytest.mark.parametrize("suffix", ["", "/", "/v1/traces", "/v1/traces/", "/v1/metrics"])
    def test_endpoint_forms_normalise(self, suffix):
        rb = backends.resolve(_cfg(endpoint=f"http://localhost:3002{suffix}"))
        assert rb.endpoint == "http://localhost:3002/v1/traces"

    def test_missing_key_raises(self):
        with pytest.raises(ValueError, match="api_key"):
            backends.resolve(_cfg(api_key=None))

    def test_missing_project_raises(self):
        # Spans without a project are rejected by Latitude: fail early instead.
        with pytest.raises(ValueError, match="project"):
            backends.resolve(_cfg(project=None))

    def test_key_and_project_from_env(self, monkeypatch):
        monkeypatch.setenv("LATITUDE_API_KEY", "vendor")
        monkeypatch.setenv("LATITUDE_PROJECT", "env-proj")
        rb = backends.resolve(BackendConfig(type="latitude", endpoint="http://localhost:3002"))
        assert rb.headers == {"Authorization": "Bearer vendor", "X-Latitude-Project": "env-proj"}
        monkeypatch.setenv("OTEL_LATITUDE_API_KEY", "preferred")
        rb = backends.resolve(BackendConfig(type="latitude", endpoint="http://localhost:3002"))
        assert rb.headers["Authorization"] == "Bearer preferred"

    def test_named_env_fields(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "k2")
        monkeypatch.setenv("MY_PROJ", "p2")
        rb = backends.resolve(
            BackendConfig(
                type="latitude",
                endpoint="http://localhost:3002",
                api_key_env="MY_KEY",
                project_env="MY_PROJ",
            )
        )
        assert rb.headers == {"Authorization": "Bearer k2", "X-Latitude-Project": "p2"}

    def test_ingest_url_env(self, monkeypatch):
        monkeypatch.setenv("LATITUDE_INGEST_URL", "http://lat:3002")
        rb = backends.resolve(_cfg(endpoint=None))
        assert rb.endpoint == "http://lat:3002/v1/traces"
        monkeypatch.setenv("OTEL_LATITUDE_ENDPOINT", "http://other:3002")
        rb = backends.resolve(_cfg(endpoint=None))
        assert rb.endpoint == "http://other:3002/v1/traces"

    def test_user_headers_merge_and_override(self):
        rb = backends.resolve(_cfg(headers={"X-Latitude-Project": "other", "X-Custom": "v"}))
        assert rb.headers["X-Latitude-Project"] == "other"
        assert rb.headers["X-Custom"] == "v"

    def test_signals_can_be_forced_on(self):
        rb = backends.resolve(_cfg(metrics=True, logs=True))
        assert rb.supports_metrics is True and rb.supports_logs is True

    def test_no_temporality_preset(self):
        assert backends.preset_temporality("latitude") is None


class TestLatitudeEnvMode:
    def test_key_and_project_select_cloud(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LATITUDE_API_KEY", "k")
        monkeypatch.setenv("LATITUDE_PROJECT", "p")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "latitude"
        assert rb.endpoint == "https://ingest.latitude.so/v1/traces"

    def test_key_without_project_is_skipped(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LATITUDE_API_KEY", "k")
        assert backends.resolve_from_env() is None

    def test_vendor_vars_alone_do_not_select(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("LATITUDE_API_KEY", "k")
        monkeypatch.setenv("LATITUDE_PROJECT", "p")
        assert backends.resolve_from_env() is None

    def test_langwatch_outranks_latitude(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LANGWATCH_API_KEY", "k")
        monkeypatch.setenv("OTEL_LATITUDE_API_KEY", "k")
        monkeypatch.setenv("LATITUDE_PROJECT", "p")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "langwatch"
