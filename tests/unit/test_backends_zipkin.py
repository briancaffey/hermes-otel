"""Unit tests for the ``zipkin`` backend resolver.

No network: these assert the resolved endpoint, headers and signal flags only.
The live path is verified against the compose stack in docker-compose/zipkin.
"""

import pytest

from hermes_otel import backends
from hermes_otel.backend_config import BackendConfig
from hermes_otel.backend_specs import spec_for


class TestZipkinBackendType:
    def test_origin_gets_traces_suffix(self):
        rb = backends.resolve(BackendConfig(type="zipkin", endpoint="http://localhost:9411"))
        assert rb.type == "zipkin"
        assert rb.display_name == "Zipkin"
        assert rb.endpoint == "http://localhost:9411/v1/traces"
        assert rb.supports_traces is True
        assert rb.supports_metrics is False
        assert rb.supports_logs is False

    def test_full_traces_endpoint_left_alone(self):
        rb = backends.resolve(
            BackendConfig(type="zipkin", endpoint="http://localhost:9411/v1/traces")
        )
        assert rb.endpoint == "http://localhost:9411/v1/traces"

    def test_metrics_or_logs_suffix_is_rewritten_to_traces(self):
        for suffix in ("/v1/metrics", "/v1/logs", "/v1/traces/", "/"):
            rb = backends.resolve(
                BackendConfig(type="zipkin", endpoint=f"http://localhost:9411{suffix}")
            )
            assert rb.endpoint == "http://localhost:9411/v1/traces"

    def test_hostname_without_port_gets_the_suffix(self):
        rb = backends.resolve(BackendConfig(type="zipkin", endpoint="https://zipkin.example.com"))
        assert rb.endpoint == "https://zipkin.example.com/v1/traces"

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="zipkin"))

    def test_no_headers_by_default(self):
        rb = backends.resolve(BackendConfig(type="zipkin", endpoint="http://localhost:9411"))
        assert rb.headers is None

    def test_user_headers_pass_through(self):
        rb = backends.resolve(
            BackendConfig(
                type="zipkin",
                endpoint="http://localhost:9411",
                headers={"Authorization": "Basic abc"},
            )
        )
        assert rb.headers == {"Authorization": "Basic abc"}

    def test_signal_overrides(self):
        # A collector in front could take metrics; the entry may say so.
        rb = backends.resolve(
            BackendConfig(type="zipkin", endpoint="http://localhost:9411", metrics=True, logs=True)
        )
        assert rb.supports_metrics is True
        assert rb.supports_logs is True

    def test_no_temporality_preset(self):
        assert spec_for("zipkin").temporality is None

    def test_root_usage_kept(self):
        rb = backends.resolve(BackendConfig(type="zipkin", endpoint="http://localhost:9411"))
        assert rb.root_usage is True

    def test_signal_defaults_table(self):
        assert backends.signal_support("zipkin") == {
            "traces": True,
            "metrics": False,
            "logs": False,
        }

    def test_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_ZIPKIN_ENDPOINT", "http://zipkin:9411")
        rb = backends.resolve(BackendConfig(type="zipkin"))
        assert rb.endpoint == "http://zipkin:9411/v1/traces"

    def test_endpoint_field_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_ZIPKIN_ENDPOINT", "http://env:9411")
        rb = backends.resolve(BackendConfig(type="zipkin", endpoint="http://file:9411"))
        assert rb.endpoint == "http://file:9411/v1/traces"


class TestZipkinEnvMode:
    def test_nothing_set_selects_nothing(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        assert backends.resolve_from_env() is None

    def test_endpoint_alone_selects_zipkin(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_ZIPKIN_ENDPOINT", "http://localhost:9411")
        rb = backends.resolve_from_env()
        assert rb is not None
        assert rb.type == "zipkin"
        assert rb.endpoint == "http://localhost:9411/v1/traces"
        assert rb.supports_metrics is False

    def test_jaeger_outranks_zipkin_in_env_priority(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_JAEGER_ENDPOINT", "http://j:4318/v1/traces")
        monkeypatch.setenv("OTEL_ZIPKIN_ENDPOINT", "http://z:9411")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "jaeger"

    def test_zipkin_outranks_tempo_in_env_priority(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_TEMPO_ENDPOINT", "http://t:4318/v1/traces")
        monkeypatch.setenv("OTEL_ZIPKIN_ENDPOINT", "http://z:9411")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "zipkin"
