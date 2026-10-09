"""Unit tests for the ``langwatch`` backend resolver (vendor prefix, bearer key, all signals)."""

import pytest

from hermes_otel import backends
from hermes_otel.plugin_config import BackendConfig

TRACES = "/api/otel/v1/traces"


class TestLangwatchBackendType:
    def test_self_host_defaults(self):
        rb = backends.resolve(
            BackendConfig(type="langwatch", endpoint="http://localhost:5560", api_key="sk-lw-1")
        )
        assert rb.type == "langwatch"
        assert rb.display_name == "LangWatch"
        assert rb.endpoint == "http://localhost:5560" + TRACES
        assert rb.headers == {"Authorization": "Bearer sk-lw-1"}
        assert rb.supports_traces and rb.supports_metrics and rb.supports_logs

    def test_cloud_default_when_no_endpoint(self):
        rb = backends.resolve(BackendConfig(type="langwatch", api_key="sk-lw-1"))
        assert rb.endpoint == "https://app.langwatch.ai" + TRACES

    @pytest.mark.parametrize(
        "given",
        [
            "http://localhost:5560",
            "http://localhost:5560/",
            "http://localhost:5560/api/otel",
            "http://localhost:5560/api/otel/",
            "http://localhost:5560/api/otel/v1/traces",
            "http://localhost:5560/api/otel/v1/logs",
        ],
    )
    def test_endpoint_forms_normalise(self, given):
        rb = backends.resolve(BackendConfig(type="langwatch", endpoint=given, api_key="k"))
        assert rb.endpoint == "http://localhost:5560" + TRACES

    def test_missing_key_raises(self):
        with pytest.raises(ValueError, match="api_key"):
            backends.resolve(BackendConfig(type="langwatch", endpoint="http://localhost:5560"))

    def test_key_from_env_names(self, monkeypatch):
        monkeypatch.setenv("LANGWATCH_API_KEY", "vendor")
        rb = backends.resolve(BackendConfig(type="langwatch", endpoint="http://localhost:5560"))
        assert rb.headers["Authorization"] == "Bearer vendor"
        monkeypatch.setenv("OTEL_LANGWATCH_API_KEY", "preferred")
        rb = backends.resolve(BackendConfig(type="langwatch", endpoint="http://localhost:5560"))
        assert rb.headers["Authorization"] == "Bearer preferred"

    def test_sdk_endpoint_env(self, monkeypatch):
        monkeypatch.setenv("LANGWATCH_ENDPOINT", "http://lw:5560")
        rb = backends.resolve(BackendConfig(type="langwatch", api_key="k"))
        assert rb.endpoint == "http://lw:5560" + TRACES
        monkeypatch.setenv("OTEL_LANGWATCH_ENDPOINT", "http://other:5560")
        rb = backends.resolve(BackendConfig(type="langwatch", api_key="k"))
        assert rb.endpoint == "http://other:5560" + TRACES

    def test_project_id_header_for_service_keys(self, monkeypatch):
        rb = backends.resolve(
            BackendConfig(
                type="langwatch", endpoint="http://localhost:5560", api_key="k", project="p1"
            )
        )
        assert rb.headers["X-Project-Id"] == "p1"
        monkeypatch.setenv("LANGWATCH_PROJECT_ID", "p2")
        rb = backends.resolve(
            BackendConfig(type="langwatch", endpoint="http://localhost:5560", api_key="k")
        )
        assert rb.headers["X-Project-Id"] == "p2"

    def test_user_headers_merge_and_override(self):
        rb = backends.resolve(
            BackendConfig(
                type="langwatch",
                endpoint="http://localhost:5560",
                api_key="k",
                headers={"X-Auth-Token": "legacy", "Authorization": "Bearer other"},
            )
        )
        assert rb.headers == {"Authorization": "Bearer other", "X-Auth-Token": "legacy"}

    def test_signal_overrides(self):
        rb = backends.resolve(
            BackendConfig(
                type="langwatch",
                endpoint="http://localhost:5560",
                api_key="k",
                metrics=False,
                logs=False,
            )
        )
        assert rb.supports_metrics is False and rb.supports_logs is False

    def test_no_temporality_preset(self):
        assert backends.preset_temporality("langwatch") is None


class TestLangwatchEnvMode:
    def test_key_alone_selects_cloud(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LANGWATCH_API_KEY", "k")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "langwatch"
        assert rb.endpoint == "https://app.langwatch.ai" + TRACES

    def test_endpoint_without_key_is_skipped(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LANGWATCH_ENDPOINT", "http://localhost:5560")
        assert backends.resolve_from_env() is None

    def test_vendor_vars_alone_do_not_select(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("LANGWATCH_API_KEY", "k")
        monkeypatch.setenv("LANGWATCH_ENDPOINT", "http://localhost:5560")
        assert backends.resolve_from_env() is None

    def test_laminar_outranks_langwatch(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LAMINAR_API_KEY", "pk")
        monkeypatch.setenv("OTEL_LANGWATCH_API_KEY", "k")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "laminar"
