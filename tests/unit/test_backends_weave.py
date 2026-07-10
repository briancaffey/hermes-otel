"""Unit tests for the W&B Weave backend resolver."""

import pytest
from hermes_otel import backends
from hermes_otel.plugin_config import BackendConfig


def _clear_env(monkeypatch):
    for var in (
        "OTEL_LANGFUSE_PUBLIC_API_KEY",
        "OTEL_LANGFUSE_SECRET_API_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "OTEL_SIGNOZ_ENDPOINT",
        "OTEL_UPTRACE_ENDPOINT",
        "OTEL_UPTRACE_DSN",
        "UPTRACE_DSN",
        "OTEL_OPENOBSERVE_ENDPOINT",
        "OTEL_OPENOBSERVE_USER",
        "OTEL_OPENOBSERVE_PASSWORD",
        "OPENOBSERVE_USER",
        "OPENOBSERVE_PASSWORD",
        "HONEYCOMB_API_KEY",
        "OTEL_HONEYCOMB_API_KEY",
        "OTEL_JAEGER_ENDPOINT",
        "OTEL_TEMPO_ENDPOINT",
        "OTEL_PHOENIX_ENDPOINT",
        "WANDB_API_KEY",
        "WANDB_ENTITY",
        "WANDB_PROJECT",
        "DEFAULT_WANDB_ENTITY",
        "DEFAULT_WANDB_PROJECT",
    ):
        monkeypatch.delenv(var, raising=False)


class TestWeaveBackendType:
    def test_resolves_with_inline_key_and_default_endpoint(self):
        rb = backends.resolve(
            BackendConfig(type="weave", api_key="wandb_key", entity="team", project="proj")
        )
        assert rb.type == "weave"
        assert rb.display_name == "W&B Weave"
        assert rb.endpoint == "https://trace.wandb.ai/otel/v1/traces"
        assert rb.headers["wandb-api-key"] == "wandb_key"
        assert rb.resource_attributes == {
            "wandb.entity": "team",
            "wandb.project": "proj",
        }
        assert rb.supports_traces is True
        assert rb.supports_metrics is False
        assert rb.supports_logs is False

    def test_dedicated_base_url_gets_trace_path(self):
        rb = backends.resolve(
            BackendConfig(
                type="weave",
                api_key="k",
                entity="team",
                project="proj",
                base_url="https://acme.wandb.io",
            )
        )
        assert rb.endpoint == "https://acme.wandb.io/traces/otel/v1/traces"

    def test_trace_base_url_gets_cloud_path(self):
        rb = backends.resolve(
            BackendConfig(
                type="weave",
                api_key="k",
                entity="team",
                project="proj",
                base_url="https://trace.wandb.ai",
            )
        )
        assert rb.endpoint == "https://trace.wandb.ai/otel/v1/traces"

    def test_explicit_endpoint_wins(self):
        rb = backends.resolve(
            BackendConfig(
                type="weave",
                api_key="k",
                entity="team",
                project="proj",
                endpoint="https://proxy.internal/otel/v1/traces",
            )
        )
        assert rb.endpoint == "https://proxy.internal/otel/v1/traces"

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("WANDB_API_KEY", "env_key")
        rb = backends.resolve(BackendConfig(type="weave", entity="team", project="proj"))
        assert rb.headers["wandb-api-key"] == "env_key"

    def test_named_env_fields(self, monkeypatch):
        monkeypatch.setenv("MY_WANDB_KEY", "named_key")
        monkeypatch.setenv("MY_WANDB_ENTITY", "env_team")
        monkeypatch.setenv("MY_WANDB_PROJECT", "env_proj")
        rb = backends.resolve(
            BackendConfig(
                type="weave",
                api_key_env="MY_WANDB_KEY",
                entity_env="MY_WANDB_ENTITY",
                project_env="MY_WANDB_PROJECT",
            )
        )
        assert rb.headers["wandb-api-key"] == "named_key"
        assert rb.resource_attributes == {
            "wandb.entity": "env_team",
            "wandb.project": "env_proj",
        }

    def test_user_headers_merge_on_top(self):
        rb = backends.resolve(
            BackendConfig(
                type="weave",
                api_key="k",
                entity="team",
                project="proj",
                headers={"X-Extra": "1"},
            )
        )
        assert rb.headers["wandb-api-key"] == "k"
        assert rb.headers["X-Extra"] == "1"

    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            backends.resolve(BackendConfig(type="weave", entity="team", project="proj"))

    def test_env_priority_requires_routing(self, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("WANDB_API_KEY", "env_key")
        assert backends.resolve_from_env() is None

    def test_env_priority_picks_weave_with_routing(self, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("WANDB_API_KEY", "env_key")
        monkeypatch.setenv("WANDB_ENTITY", "team")
        monkeypatch.setenv("WANDB_PROJECT", "proj")
        rb = backends.resolve_from_env()
        assert rb is not None
        assert rb.type == "weave"
