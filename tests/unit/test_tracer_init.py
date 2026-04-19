"""Tests for HermesOTelPlugin.init() environment detection logic."""

import base64
from unittest.mock import MagicMock, patch

import pytest
from hermes_otel.tracer import HermesOTelPlugin


def _clear_backend_env(monkeypatch):
    """Remove all backend env vars so tests start from a clean slate."""
    for var in [
        "OTEL_PHOENIX_ENDPOINT", "OTEL_PROJECT_NAME",
        "LANGSMITH_TRACING", "LANGSMITH_API_KEY",
        "OTEL_LANGFUSE_PUBLIC_API_KEY", "OTEL_LANGFUSE_SECRET_API_KEY",
        "OTEL_LANGFUSE_ENDPOINT",
        "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL",
        "OTEL_SIGNOZ_ENDPOINT", "OTEL_SIGNOZ_INGESTION_KEY",
        "OTEL_JAEGER_ENDPOINT",
        "OTEL_TEMPO_ENDPOINT",
    ]:
        monkeypatch.delenv(var, raising=False)


class TestInitPhoenix:
    def test_init_with_otel_endpoint(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            mock_otlp.assert_called_once_with(
                "http://localhost:6006/v1/traces",
                backend_name="Phoenix",
            )

    def test_init_no_endpoint_returns_false(self, monkeypatch):
        _clear_backend_env(monkeypatch)

        plugin = HermesOTelPlugin()
        assert plugin.init() is False
        assert plugin.is_enabled is False

    def test_init_with_explicit_endpoint_arg(self, monkeypatch):
        _clear_backend_env(monkeypatch)

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init(endpoint="http://custom:8080/v1/traces") is True
            mock_otlp.assert_called_once_with(
                "http://custom:8080/v1/traces",
                backend_name="Phoenix",
            )


class TestInitLangfuse:
    def test_init_with_langfuse_plugin_vars(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LANGFUSE_PUBLIC_API_KEY", "pk-lf-test")
        monkeypatch.setenv("OTEL_LANGFUSE_SECRET_API_KEY", "sk-lf-test")
        monkeypatch.setenv("OTEL_LANGFUSE_ENDPOINT", "https://langfuse.example.com/api/public/otel")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            mock_otlp.assert_called_once()
            call_kwargs = mock_otlp.call_args
            assert call_kwargs[0][0] == "https://langfuse.example.com/api/public/otel"
            assert call_kwargs[1]["backend_name"] == "Langfuse"
            headers = call_kwargs[1]["headers"]
            expected_auth = base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
            assert headers["Authorization"] == f"Basic {expected_auth}"
            assert headers["x-langfuse-ingestion-version"] == "4"

    def test_init_with_langfuse_standard_vars(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-std")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-std")
        monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            endpoint = mock_otlp.call_args[0][0]
            assert endpoint == "https://cloud.langfuse.com/api/public/otel/v1/traces"

    def test_langfuse_defaults_to_eu_cloud(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LANGFUSE_PUBLIC_API_KEY", "pk")
        monkeypatch.setenv("OTEL_LANGFUSE_SECRET_API_KEY", "sk")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            endpoint = mock_otlp.call_args[0][0]
            assert endpoint == "https://cloud.langfuse.com/api/public/otel/v1/traces"


class TestInitLangSmith:
    def test_init_with_langsmith(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_langsmith", return_value=True) as mock_ls:
            assert plugin.init() is True
            mock_ls.assert_called_once()

    def test_langsmith_takes_priority_over_langfuse(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_key")
        monkeypatch.setenv("OTEL_LANGFUSE_PUBLIC_API_KEY", "pk-lf")
        monkeypatch.setenv("OTEL_LANGFUSE_SECRET_API_KEY", "sk-lf")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_langsmith", return_value=True) as mock_ls, \
             patch.object(plugin, "_init_otlp") as mock_otlp:
            assert plugin.init() is True
            mock_ls.assert_called_once()
            mock_otlp.assert_not_called()

    def test_langsmith_disabled_when_tracing_not_true(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("LANGSMITH_TRACING", "false")
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_key")

        plugin = HermesOTelPlugin()
        assert plugin.init() is False


class TestInitSigNoz:
    def test_init_self_hosted(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_SIGNOZ_ENDPOINT", "http://localhost:4328/v1/traces")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            mock_otlp.assert_called_once_with(
                "http://localhost:4328/v1/traces",
                headers=None,
                backend_name="SigNoz",
            )

    def test_init_cloud_with_ingestion_key(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_SIGNOZ_ENDPOINT",
                           "https://ingest.us.signoz.cloud:443/v1/traces")
        monkeypatch.setenv("OTEL_SIGNOZ_INGESTION_KEY", "sz-key-abc123")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            call_kwargs = mock_otlp.call_args[1]
            assert call_kwargs["headers"] == {"signoz-ingestion-key": "sz-key-abc123"}
            assert call_kwargs["backend_name"] == "SigNoz"

    def test_langfuse_takes_priority_over_signoz(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LANGFUSE_PUBLIC_API_KEY", "pk")
        monkeypatch.setenv("OTEL_LANGFUSE_SECRET_API_KEY", "sk")
        monkeypatch.setenv("OTEL_SIGNOZ_ENDPOINT", "http://localhost:4328/v1/traces")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            assert mock_otlp.call_args[1]["backend_name"] == "Langfuse"


class TestInitJaeger:
    def test_init_local_jaeger(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_JAEGER_ENDPOINT", "http://localhost:4318/v1/traces")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            mock_otlp.assert_called_once_with(
                "http://localhost:4318/v1/traces",
                backend_name="Jaeger",
            )

    def test_signoz_takes_priority_over_jaeger(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_SIGNOZ_ENDPOINT", "http://localhost:4328/v1/traces")
        monkeypatch.setenv("OTEL_JAEGER_ENDPOINT", "http://localhost:4318/v1/traces")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            assert mock_otlp.call_args[1]["backend_name"] == "SigNoz"

    def test_jaeger_takes_priority_over_phoenix(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_JAEGER_ENDPOINT", "http://localhost:4318/v1/traces")
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            assert mock_otlp.call_args[1]["backend_name"] == "Jaeger"
            assert mock_otlp.call_args[0][0] == "http://localhost:4318/v1/traces"

    def test_jaeger_skips_metrics_init(self, monkeypatch):
        """Jaeger is traces-only — _init_metrics must short-circuit."""
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_JAEGER_ENDPOINT", "http://localhost:4318/v1/traces")

        from opentelemetry.sdk.trace import TracerProvider

        plugin = HermesOTelPlugin()
        # Let the real _init_otlp run so _init_metrics is called with backend_name="Jaeger".
        real_tp = TracerProvider
        with patch("hermes_otel.tracer.TracerProvider", side_effect=lambda **k: real_tp(**k)):
            plugin.init()
        # No meter provider should have been created for a traces-only backend.
        assert plugin._meter is None
        assert plugin._meter_provider is None


class TestInitTempo:
    def test_init_local_tempo(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_TEMPO_ENDPOINT", "http://localhost:4318/v1/traces")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            mock_otlp.assert_called_once_with(
                "http://localhost:4318/v1/traces",
                backend_name="Tempo",
            )

    def test_jaeger_takes_priority_over_tempo(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_JAEGER_ENDPOINT", "http://localhost:4318/v1/traces")
        monkeypatch.setenv("OTEL_TEMPO_ENDPOINT", "http://localhost:4318/v1/traces")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            assert mock_otlp.call_args[1]["backend_name"] == "Jaeger"

    def test_tempo_takes_priority_over_phoenix(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_TEMPO_ENDPOINT", "http://localhost:4318/v1/traces")
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")

        plugin = HermesOTelPlugin()
        with patch.object(plugin, "_init_otlp", return_value=True) as mock_otlp:
            assert plugin.init() is True
            assert mock_otlp.call_args[1]["backend_name"] == "Tempo"

    def test_tempo_skips_metrics_init(self, monkeypatch):
        """Tempo is traces-only — _init_metrics must short-circuit."""
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_TEMPO_ENDPOINT", "http://localhost:4318/v1/traces")

        from opentelemetry.sdk.trace import TracerProvider

        plugin = HermesOTelPlugin()
        real_tp = TracerProvider
        with patch("hermes_otel.tracer.TracerProvider", side_effect=lambda **k: real_tp(**k)):
            plugin.init()
        assert plugin._meter is None
        assert plugin._meter_provider is None


class TestInitOtelUnavailable:
    def test_returns_false_when_otel_not_available(self, monkeypatch):
        import hermes_otel.tracer as tracer_mod
        monkeypatch.setattr(tracer_mod, "_OTEL_AVAILABLE", False)

        plugin = HermesOTelPlugin()
        assert plugin.init() is False


class TestConfigDisabled:
    def test_init_returns_false_when_config_disabled(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")

        from hermes_otel.plugin_config import HermesOtelConfig
        plugin = HermesOTelPlugin(config=HermesOtelConfig(enabled=False))
        # _init_otlp should NOT be called because we short-circuit on disabled.
        with patch.object(plugin, "_init_otlp") as mock_otlp:
            assert plugin.init() is False
            mock_otlp.assert_not_called()


class TestResourceAttributes:
    def test_resource_attributes_merged(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")

        from hermes_otel.plugin_config import HermesOtelConfig
        from opentelemetry.sdk.trace import TracerProvider

        cfg = HermesOtelConfig(
            resource_attributes={"env": "prod", "region": "us-east-1"},
            global_tags={"team": "platform"},
            project_name="cfg-project",
        )
        plugin = HermesOTelPlugin(config=cfg)
        captured = {}

        real_tp = TracerProvider
        def _spy(**kwargs):
            captured["resource"] = kwargs["resource"]
            return real_tp(**kwargs)

        with patch("hermes_otel.tracer.TracerProvider", side_effect=_spy):
            plugin.init()

        attrs = dict(captured["resource"].attributes)
        assert attrs["service.name"] == "hermes-agent"
        assert attrs["env"] == "prod"
        assert attrs["region"] == "us-east-1"
        assert attrs["team"] == "platform"
        assert attrs["openinference.project.name"] == "cfg-project"

    def test_resource_attributes_override_global_tags(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")

        from hermes_otel.plugin_config import HermesOtelConfig
        from opentelemetry.sdk.trace import TracerProvider

        cfg = HermesOtelConfig(
            global_tags={"env": "staging"},
            resource_attributes={"env": "prod"},
        )
        plugin = HermesOTelPlugin(config=cfg)
        captured = {}
        real_tp = TracerProvider
        def _spy(**kwargs):
            captured["resource"] = kwargs["resource"]
            return real_tp(**kwargs)

        with patch("hermes_otel.tracer.TracerProvider", side_effect=_spy):
            plugin.init()
        assert dict(captured["resource"].attributes)["env"] == "prod"

    def test_user_can_override_service_name(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")

        from hermes_otel.plugin_config import HermesOtelConfig
        from opentelemetry.sdk.trace import TracerProvider

        cfg = HermesOtelConfig(resource_attributes={"service.name": "custom-svc"})
        plugin = HermesOTelPlugin(config=cfg)
        captured = {}
        real_tp = TracerProvider
        def _spy(**kwargs):
            captured["resource"] = kwargs["resource"]
            return real_tp(**kwargs)

        with patch("hermes_otel.tracer.TracerProvider", side_effect=_spy):
            plugin.init()
        assert dict(captured["resource"].attributes)["service.name"] == "custom-svc"

    def test_project_name_env_fallback(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")
        monkeypatch.setenv("OTEL_PROJECT_NAME", "env-project")

        from hermes_otel.plugin_config import HermesOtelConfig
        from opentelemetry.sdk.trace import TracerProvider

        plugin = HermesOTelPlugin(config=HermesOtelConfig())
        captured = {}
        real_tp = TracerProvider
        def _spy(**kwargs):
            captured["resource"] = kwargs["resource"]
            return real_tp(**kwargs)

        with patch("hermes_otel.tracer.TracerProvider", side_effect=_spy):
            plugin.init()
        assert dict(captured["resource"].attributes)["openinference.project.name"] == "env-project"

    def test_config_project_name_supersedes_env(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")
        monkeypatch.setenv("OTEL_PROJECT_NAME", "env-project")

        from hermes_otel.plugin_config import HermesOtelConfig
        from opentelemetry.sdk.trace import TracerProvider

        cfg = HermesOtelConfig(project_name="cfg-wins")
        plugin = HermesOTelPlugin(config=cfg)
        captured = {}
        real_tp = TracerProvider
        def _spy(**kwargs):
            captured["resource"] = kwargs["resource"]
            return real_tp(**kwargs)

        with patch("hermes_otel.tracer.TracerProvider", side_effect=_spy):
            plugin.init()
        assert dict(captured["resource"].attributes)["openinference.project.name"] == "cfg-wins"


class TestSampling:
    def test_no_sampler_when_rate_none(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")

        from hermes_otel.plugin_config import HermesOtelConfig
        from opentelemetry.sdk.trace import TracerProvider

        plugin = HermesOTelPlugin(config=HermesOtelConfig(sample_rate=None))
        captured = {}
        real_tp = TracerProvider
        def _spy(**kwargs):
            captured["kwargs"] = kwargs
            return real_tp(**kwargs)
        with patch("hermes_otel.tracer.TracerProvider", side_effect=_spy):
            plugin.init()
        assert "sampler" not in captured["kwargs"]

    def test_sampler_attached_when_rate_set(self, monkeypatch):
        _clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")

        from hermes_otel.plugin_config import HermesOtelConfig
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        plugin = HermesOTelPlugin(config=HermesOtelConfig(sample_rate=0.3))
        captured = {}
        real_tp = TracerProvider
        def _spy(**kwargs):
            captured["kwargs"] = kwargs
            return real_tp(**kwargs)
        with patch("hermes_otel.tracer.TracerProvider", side_effect=_spy):
            plugin.init()
        sampler = captured["kwargs"].get("sampler")
        assert isinstance(sampler, ParentBased)
