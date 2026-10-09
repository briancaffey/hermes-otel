"""Unit tests for the ``opik`` backend resolver (vendor path, cloud headers, traces only)."""

import pytest

from hermes_otel import backends
from hermes_otel.plugin_config import BackendConfig

TRACES = "/api/v1/private/otel/v1/traces"


class TestOpikBackendType:
    def test_self_host_base_defaults(self):
        rb = backends.resolve(BackendConfig(type="opik", endpoint="http://localhost:5173"))
        assert rb.type == "opik"
        assert rb.display_name == "Comet Opik"
        assert rb.endpoint == "http://localhost:5173" + TRACES
        assert rb.headers == {}
        assert rb.supports_traces is True
        assert rb.supports_metrics is False
        assert rb.supports_logs is False

    @pytest.mark.parametrize(
        "given",
        [
            "http://localhost:5173",
            "http://localhost:5173/",
            "http://localhost:5173/api",
            "http://localhost:5173/api/v1/private/otel",
            "http://localhost:5173/api/v1/private/otel/v1/traces",
            "http://localhost:5173/api/v1/private/otel/v1/metrics",
        ],
    )
    def test_endpoint_forms_normalise(self, given):
        rb = backends.resolve(BackendConfig(type="opik", endpoint=given))
        assert rb.endpoint == "http://localhost:5173" + TRACES

    def test_cloud_default_when_no_endpoint(self):
        rb = backends.resolve(BackendConfig(type="opik", api_key="k", workspace="ws"))
        assert rb.endpoint == "https://www.comet.com/opik" + TRACES
        # Bare key, not Bearer: Opik's own scheme.
        assert rb.headers == {"Authorization": "k", "Comet-Workspace": "ws"}

    def test_sdk_url_override_env(self, monkeypatch):
        monkeypatch.setenv("OPIK_URL_OVERRIDE", "https://www.comet.com/opik/api")
        rb = backends.resolve(BackendConfig(type="opik"))
        assert rb.endpoint == "https://www.comet.com/opik" + TRACES

    def test_otel_endpoint_env_wins_over_sdk_override(self, monkeypatch):
        monkeypatch.setenv("OPIK_URL_OVERRIDE", "https://www.comet.com/opik/api")
        monkeypatch.setenv("OTEL_OPIK_ENDPOINT", "http://opik:5173")
        rb = backends.resolve(BackendConfig(type="opik"))
        assert rb.endpoint == "http://opik:5173" + TRACES

    def test_project_header(self, monkeypatch):
        rb = backends.resolve(
            BackendConfig(type="opik", endpoint="http://localhost:5173", project="hermes-agent")
        )
        assert rb.headers == {"projectName": "hermes-agent"}
        monkeypatch.setenv("OPIK_PROJECT_NAME", "from-env")
        rb = backends.resolve(BackendConfig(type="opik", endpoint="http://localhost:5173"))
        assert rb.headers["projectName"] == "from-env"
        monkeypatch.setenv("MY_PROJECT", "named")
        rb = backends.resolve(
            BackendConfig(type="opik", endpoint="http://localhost:5173", project_env="MY_PROJECT")
        )
        assert rb.headers["projectName"] == "named"

    def test_workspace_and_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OPIK_API_KEY", "vendor")
        monkeypatch.setenv("OPIK_WORKSPACE", "team")
        rb = backends.resolve(BackendConfig(type="opik", endpoint="http://localhost:5173"))
        assert rb.headers["Authorization"] == "vendor"
        assert rb.headers["Comet-Workspace"] == "team"
        monkeypatch.setenv("OTEL_OPIK_API_KEY", "preferred")
        rb = backends.resolve(BackendConfig(type="opik", endpoint="http://localhost:5173"))
        assert rb.headers["Authorization"] == "preferred"

    def test_user_headers_merge_and_override(self):
        rb = backends.resolve(
            BackendConfig(
                type="opik",
                endpoint="http://localhost:5173",
                api_key="k",
                headers={"Authorization": "Bearer other", "X-Custom": "v"},
            )
        )
        assert rb.headers["Authorization"] == "Bearer other"
        assert rb.headers["X-Custom"] == "v"

    def test_signals_can_be_forced_on(self):
        rb = backends.resolve(
            BackendConfig(type="opik", endpoint="http://localhost:5173", metrics=True, logs=True)
        )
        assert rb.supports_metrics is True and rb.supports_logs is True

    def test_no_temporality_preset(self):
        assert backends.preset_temporality("opik") is None


class TestOpikEnvMode:
    def test_key_alone_selects_cloud(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_OPIK_API_KEY", "k")
        monkeypatch.setenv("OPIK_WORKSPACE", "ws")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "opik"
        assert rb.endpoint == "https://www.comet.com/opik" + TRACES
        assert rb.headers == {"Authorization": "k", "Comet-Workspace": "ws"}

    def test_endpoint_alone_selects_self_host(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_OPIK_ENDPOINT", "http://localhost:5173")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "opik" and rb.headers == {}

    def test_vendor_vars_alone_do_not_select(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OPIK_API_KEY", "k")
        monkeypatch.setenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
        monkeypatch.setenv("OPIK_PROJECT_NAME", "p")
        assert backends.resolve_from_env() is None

    def test_mlflow_outranks_opik(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_MLFLOW_ENDPOINT", "http://m:5001")
        monkeypatch.setenv("OTEL_OPIK_ENDPOINT", "http://o:5173")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "mlflow"
