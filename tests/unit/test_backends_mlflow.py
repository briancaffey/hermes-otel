"""Unit tests for the ``mlflow`` backend resolver (traces only, experiment header)."""

import pytest

from hermes_otel import backends
from hermes_otel.plugin_config import BackendConfig


class TestMlflowBackendType:
    def test_defaults(self):
        rb = backends.resolve(BackendConfig(type="mlflow", endpoint="http://localhost:5001"))
        assert rb.type == "mlflow"
        assert rb.display_name == "MLflow"
        assert rb.endpoint == "http://localhost:5001/v1/traces"
        # The header is mandatory (400 without it); 0 is the Default experiment.
        assert rb.headers == {"x-mlflow-experiment-id": "0"}
        assert rb.supports_traces is True
        assert rb.supports_metrics is False
        assert rb.supports_logs is False

    def test_endpoint_forms_normalise(self):
        for suffix in ("", "/", "/v1/traces", "/v1/traces/", "/v1/metrics"):
            rb = backends.resolve(
                BackendConfig(type="mlflow", endpoint=f"http://localhost:5001{suffix}")
            )
            assert rb.endpoint == "http://localhost:5001/v1/traces"

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="mlflow"))

    def test_experiment_id_field(self):
        rb = backends.resolve(
            BackendConfig(type="mlflow", endpoint="http://localhost:5001", experiment_id="42")
        )
        assert rb.headers["x-mlflow-experiment-id"] == "42"

    def test_experiment_id_from_env_names(self, monkeypatch):
        monkeypatch.setenv("MLFLOW_EXPERIMENT_ID", "7")
        rb = backends.resolve(BackendConfig(type="mlflow", endpoint="http://localhost:5001"))
        assert rb.headers["x-mlflow-experiment-id"] == "7"
        monkeypatch.setenv("OTEL_MLFLOW_EXPERIMENT_ID", "8")
        rb = backends.resolve(BackendConfig(type="mlflow", endpoint="http://localhost:5001"))
        assert rb.headers["x-mlflow-experiment-id"] == "8"

    def test_experiment_id_env_field_selects_var(self, monkeypatch):
        monkeypatch.setenv("MY_EXP", "9")
        rb = backends.resolve(
            BackendConfig(
                type="mlflow", endpoint="http://localhost:5001", experiment_id_env="MY_EXP"
            )
        )
        assert rb.headers["x-mlflow-experiment-id"] == "9"

    def test_workspace_header(self):
        rb = backends.resolve(
            BackendConfig(type="mlflow", endpoint="http://localhost:5001", workspace="team-a")
        )
        assert rb.headers["X-MLFLOW-WORKSPACE"] == "team-a"

    def test_tracking_token_is_a_bearer(self, monkeypatch):
        rb = backends.resolve(
            BackendConfig(type="mlflow", endpoint="http://localhost:5001", api_key="tok")
        )
        assert rb.headers["Authorization"] == "Bearer tok"
        monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", "envtok")
        rb = backends.resolve(BackendConfig(type="mlflow", endpoint="http://localhost:5001"))
        assert rb.headers["Authorization"] == "Bearer envtok"

    def test_no_token_no_authorization(self):
        rb = backends.resolve(BackendConfig(type="mlflow", endpoint="http://localhost:5001"))
        assert "Authorization" not in rb.headers

    def test_user_headers_merge_and_override(self):
        rb = backends.resolve(
            BackendConfig(
                type="mlflow",
                endpoint="http://localhost:5001",
                headers={"x-mlflow-experiment-id": "3", "X-Custom": "v"},
            )
        )
        assert rb.headers["x-mlflow-experiment-id"] == "3"
        assert rb.headers["X-Custom"] == "v"

    def test_signals_can_be_forced_on(self):
        # A collector in front of MLflow could take metrics; the override wins.
        rb = backends.resolve(
            BackendConfig(type="mlflow", endpoint="http://localhost:5001", metrics=True, logs=True)
        )
        assert rb.supports_metrics is True and rb.supports_logs is True

    def test_no_temporality_preset(self):
        assert backends.preset_temporality("mlflow") is None


class TestMlflowEnvMode:
    def test_endpoint_alone_selects_mlflow(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_MLFLOW_ENDPOINT", "http://localhost:5001")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "mlflow"
        assert rb.headers == {"x-mlflow-experiment-id": "0"}

    def test_token_or_experiment_alone_do_not_select(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", "t")
        monkeypatch.setenv("MLFLOW_EXPERIMENT_ID", "1")
        assert backends.resolve_from_env() is None

    def test_openlit_outranks_mlflow(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_OPENLIT_ENDPOINT", "http://o:4318")
        monkeypatch.setenv("OTEL_MLFLOW_ENDPOINT", "http://m:5001")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "openlit"
