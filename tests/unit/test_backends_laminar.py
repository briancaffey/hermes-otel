"""Unit tests for the ``laminar`` backend resolver (bearer key, traces + logs, no metrics)."""

import pytest

from hermes_otel import backends
from hermes_otel.backend_config import BackendConfig
from hermes_otel.backend_specs import spec_for


class TestLaminarBackendType:
    def test_self_host_defaults(self):
        rb = backends.resolve(
            BackendConfig(type="laminar", endpoint="http://localhost:8100", api_key="pk")
        )
        assert rb.type == "laminar"
        assert rb.display_name == "Laminar"
        assert rb.endpoint == "http://localhost:8100/v1/traces"
        assert rb.headers == {"Authorization": "Bearer pk"}
        assert rb.supports_traces is True
        # /v1/metrics answers 200 and stores nothing: off unless forced.
        assert rb.supports_metrics is False
        assert rb.supports_logs is True

    def test_cloud_default_when_no_endpoint(self):
        rb = backends.resolve(BackendConfig(type="laminar", api_key="pk"))
        assert rb.endpoint == "https://api.lmnr.ai/v1/traces"

    @pytest.mark.parametrize("suffix", ["", "/", "/v1/traces", "/v1/logs", "/v1/metrics/"])
    def test_endpoint_forms_normalise(self, suffix):
        rb = backends.resolve(
            BackendConfig(type="laminar", endpoint=f"http://localhost:8100{suffix}", api_key="pk")
        )
        assert rb.endpoint == "http://localhost:8100/v1/traces"

    def test_missing_key_raises(self):
        with pytest.raises(ValueError, match="api_key"):
            backends.resolve(BackendConfig(type="laminar", endpoint="http://localhost:8100"))

    def test_key_from_env_names(self, monkeypatch):
        monkeypatch.setenv("LMNR_PROJECT_API_KEY", "vendor")
        rb = backends.resolve(BackendConfig(type="laminar", endpoint="http://localhost:8100"))
        assert rb.headers["Authorization"] == "Bearer vendor"
        monkeypatch.setenv("OTEL_LAMINAR_API_KEY", "preferred")
        rb = backends.resolve(BackendConfig(type="laminar", endpoint="http://localhost:8100"))
        assert rb.headers["Authorization"] == "Bearer preferred"

    def test_api_key_env_field_selects_var(self, monkeypatch):
        monkeypatch.setenv("MY_LMNR", "named")
        rb = backends.resolve(
            BackendConfig(type="laminar", endpoint="http://localhost:8100", api_key_env="MY_LMNR")
        )
        assert rb.headers["Authorization"] == "Bearer named"

    def test_sdk_base_url_env(self, monkeypatch):
        monkeypatch.setenv("LMNR_BASE_URL", "http://lmnr:8000")
        rb = backends.resolve(BackendConfig(type="laminar", api_key="pk"))
        assert rb.endpoint == "http://lmnr:8000/v1/traces"
        monkeypatch.setenv("OTEL_LAMINAR_ENDPOINT", "http://other:8000")
        rb = backends.resolve(BackendConfig(type="laminar", api_key="pk"))
        assert rb.endpoint == "http://other:8000/v1/traces"

    def test_user_headers_merge_and_override(self):
        rb = backends.resolve(
            BackendConfig(
                type="laminar",
                endpoint="http://localhost:8100",
                api_key="pk",
                headers={"Authorization": "Bearer other", "X-Custom": "v"},
            )
        )
        assert rb.headers == {"Authorization": "Bearer other", "X-Custom": "v"}

    def test_metrics_can_be_forced_on_and_logs_off(self):
        rb = backends.resolve(
            BackendConfig(
                type="laminar",
                endpoint="http://localhost:8100",
                api_key="pk",
                metrics=True,
                logs=False,
            )
        )
        assert rb.supports_metrics is True and rb.supports_logs is False

    def test_no_temporality_preset(self):
        assert spec_for("laminar").temporality is None

    def test_no_metrics_is_separate_from_traces_only(self):
        # Laminar keeps logs, so it is not a traces-only type.
        spec = spec_for("laminar")
        assert spec.metrics is False and spec.logs is True
        assert spec.traces_only is False


class TestLaminarEnvMode:
    def test_key_alone_selects_cloud(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LAMINAR_API_KEY", "pk")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "laminar"
        assert rb.endpoint == "https://api.lmnr.ai/v1/traces"

    def test_endpoint_without_key_is_skipped(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LAMINAR_ENDPOINT", "http://localhost:8100")
        assert backends.resolve_from_env() is None

    def test_vendor_key_alone_does_not_select(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("LMNR_PROJECT_API_KEY", "pk")
        monkeypatch.setenv("LMNR_BASE_URL", "http://localhost:8100")
        assert backends.resolve_from_env() is None

    def test_opik_outranks_laminar(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_OPIK_ENDPOINT", "http://o:5173")
        monkeypatch.setenv("OTEL_LAMINAR_API_KEY", "pk")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "opik"


class TestSignalSupportTable:
    def test_signal_support_refuses_metrics_like_the_resolver(self):
        # ``signal_support`` once read a traces-only table, so the Settings tab showed
        # Laminar metrics as exported while the resolver skipped them (#413).
        assert backends.signal_support("laminar") == {
            "traces": True,
            "metrics": False,
            "logs": True,
        }

    def test_signal_support_agrees_with_signals_for_on_a_bare_entry(self):
        # ``signals_for`` (what the exporter does) and ``signal_support`` (what
        # the Settings tab shows) read the same spec; a bare entry has no override.
        for t in sorted(backends.KNOWN_TYPES):
            assert backends.signals_for(BackendConfig(type=t)) == backends.signal_support(t), t
