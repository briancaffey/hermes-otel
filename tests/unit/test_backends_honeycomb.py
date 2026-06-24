"""Unit tests for the ``honeycomb`` backend resolver.

No network: these assert the resolved endpoint, headers, and signal-support
flags only. Honeycomb is SaaS with no free tier for the maintainers, so live
export is verified manually in the Honeycomb UI rather than here.
"""

import pytest
from hermes_otel import backends
from hermes_otel.plugin_config import BackendConfig


class TestHoneycombBackendType:
    def test_resolves_with_inline_key_us_default(self):
        rb = backends.resolve(BackendConfig(type="honeycomb", api_key="hcaik_test"))
        assert rb.type == "honeycomb"
        assert rb.display_name == "Honeycomb"
        # US is the default region.
        assert rb.endpoint == "https://api.honeycomb.io/v1/traces"
        assert rb.headers["x-honeycomb-team"] == "hcaik_test"
        # No dataset given → no dataset header.
        assert "x-honeycomb-dataset" not in rb.headers
        # Honeycomb ingests all three signals.
        assert rb.supports_traces is True
        assert rb.supports_metrics is True
        assert rb.supports_logs is True

    def test_eu_region_endpoint(self):
        rb = backends.resolve(BackendConfig(type="honeycomb", api_key="k", region="eu"))
        assert rb.endpoint == "https://api.eu1.honeycomb.io/v1/traces"

    def test_region_is_case_insensitive(self):
        rb = backends.resolve(BackendConfig(type="honeycomb", api_key="k", region="EU"))
        assert rb.endpoint == "https://api.eu1.honeycomb.io/v1/traces"

    def test_unknown_region_raises(self):
        with pytest.raises(ValueError, match="region"):
            backends.resolve(BackendConfig(type="honeycomb", api_key="k", region="moon"))

    def test_dataset_sets_header(self):
        rb = backends.resolve(BackendConfig(type="honeycomb", api_key="k", dataset="hermes"))
        assert rb.headers["x-honeycomb-dataset"] == "hermes"

    def test_explicit_endpoint_overrides_region(self):
        rb = backends.resolve(
            BackendConfig(
                type="honeycomb",
                api_key="k",
                region="eu",
                endpoint="https://proxy.internal/v1/traces",
            )
        )
        # Explicit endpoint wins over the region default.
        assert rb.endpoint == "https://proxy.internal/v1/traces"

    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            backends.resolve(BackendConfig(type="honeycomb"))

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("HONEYCOMB_API_KEY", "env_key")
        rb = backends.resolve(BackendConfig(type="honeycomb"))
        assert rb.headers["x-honeycomb-team"] == "env_key"

    def test_named_api_key_env(self, monkeypatch):
        monkeypatch.setenv("MY_HC_KEY", "named_env_key")
        rb = backends.resolve(BackendConfig(type="honeycomb", api_key_env="MY_HC_KEY"))
        assert rb.headers["x-honeycomb-team"] == "named_env_key"

    def test_inline_key_beats_env(self, monkeypatch):
        monkeypatch.setenv("HONEYCOMB_API_KEY", "env_key")
        rb = backends.resolve(BackendConfig(type="honeycomb", api_key="inline"))
        assert rb.headers["x-honeycomb-team"] == "inline"

    def test_user_headers_merge_on_top(self):
        rb = backends.resolve(
            BackendConfig(
                type="honeycomb",
                api_key="k",
                headers={"X-Extra": "1"},
            )
        )
        assert rb.headers["X-Extra"] == "1"
        assert rb.headers["x-honeycomb-team"] == "k"

    def test_name_override(self):
        rb = backends.resolve(BackendConfig(type="honeycomb", api_key="k", name="prod-hc"))
        assert rb.display_name == "prod-hc"

    def test_metrics_can_be_disabled(self):
        rb = backends.resolve(BackendConfig(type="honeycomb", api_key="k", metrics=False))
        assert rb.supports_metrics is False

    def test_env_priority_picks_honeycomb(self, monkeypatch):
        # With only HONEYCOMB_API_KEY set and no config.yaml, the env-driven
        # single-backend path should resolve to honeycomb.
        for var in (
            "OTEL_PHOENIX_ENDPOINT",
            "OTEL_SIGNOZ_ENDPOINT",
            "OTEL_JAEGER_ENDPOINT",
            "OTEL_TEMPO_ENDPOINT",
            "OTEL_UPTRACE_ENDPOINT",
            "OTEL_OPENOBSERVE_ENDPOINT",
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
            "OTEL_LANGFUSE_PUBLIC_API_KEY",
            "OTEL_LANGFUSE_SECRET_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HONEYCOMB_API_KEY", "env_key")
        rb = backends.resolve_from_env()
        assert rb is not None
        assert rb.type == "honeycomb"
