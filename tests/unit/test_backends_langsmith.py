"""The ``langsmith`` OTLP preset (#415): the run-tree client is gone; LangSmith
is a regular backend entry that fans out like every other OTLP target."""

from __future__ import annotations

import pytest
from _helpers import clear_backend_env

from hermes_otel import backends
from hermes_otel.backend_config import BackendConfig
from hermes_otel.backend_specs import spec_for

US = "https://api.smith.langchain.com/otel/v1/traces"


class TestResolver:
    def test_inline_key_and_project_become_the_two_headers(self):
        rb = backends.resolve(
            BackendConfig(type="langsmith", api_key="lsv2-test", project="hermes")
        )
        assert rb.type == "langsmith" and rb.display_name == "LangSmith"
        assert rb.endpoint == US
        assert rb.headers == {"x-api-key": "lsv2-test", "Langsmith-Project": "hermes"}
        assert (rb.supports_traces, rb.supports_metrics, rb.supports_logs) == (True, False, False)

    def test_env_names_and_vendor_fallbacks_fill_the_entry(self, monkeypatch):
        clear_backend_env(monkeypatch)
        monkeypatch.setenv("MY_LS_KEY", "lsv2-env")
        monkeypatch.setenv("LANGSMITH_PROJECT", "from-vendor")
        rb = backends.resolve(BackendConfig(type="langsmith", api_key_env="MY_LS_KEY"))
        assert rb.headers == {"x-api-key": "lsv2-env", "Langsmith-Project": "from-vendor"}
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-vendor")
        rb = backends.resolve(BackendConfig(type="langsmith"))
        assert rb.headers["x-api-key"] == "lsv2-vendor"

    def test_missing_key_is_a_config_error(self, monkeypatch):
        clear_backend_env(monkeypatch)
        with pytest.raises(ValueError, match="OTEL_LANGSMITH_API_KEY"):
            backends.resolve(BackendConfig(type="langsmith"))

    def test_region_eu_and_an_unknown_region(self):
        rb = backends.resolve(BackendConfig(type="langsmith", api_key="k", region="eu"))
        assert rb.endpoint == "https://eu.api.smith.langchain.com/otel/v1/traces"
        with pytest.raises(ValueError, match="region"):
            backends.resolve(BackendConfig(type="langsmith", api_key="k", region="mars"))

    @pytest.mark.parametrize(
        "given, expected",
        [
            ("https://api.smith.langchain.com", US),
            ("https://api.smith.langchain.com/", US),
            ("https://api.smith.langchain.com/otel", US),
            ("https://api.smith.langchain.com/otel/v1/traces", US),
            ("https://ls.internal/api/v1", "https://ls.internal/api/v1/otel/v1/traces"),
        ],
    )
    def test_endpoint_spellings_normalise_to_the_otlp_route(self, given, expected):
        rb = backends.resolve(BackendConfig(type="langsmith", api_key="k", endpoint=given))
        assert rb.endpoint == expected

    def test_vendor_endpoint_is_the_api_base(self, monkeypatch):
        clear_backend_env(monkeypatch)
        monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://eu.api.smith.langchain.com")
        rb = backends.resolve(BackendConfig(type="langsmith", api_key="k"))
        assert rb.endpoint == "https://eu.api.smith.langchain.com/otel/v1/traces"

    def test_signal_table(self):
        assert backends.signal_support("langsmith") == {
            "traces": True,
            "metrics": False,
            "logs": False,
        }
        assert "langsmith" in backends.KNOWN_TYPES and spec_for("langsmith").traces_only


class TestEnvMode:
    def test_opt_in_selects_it_first(self, monkeypatch):
        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_LANGSMITH_API_KEY", "k")
        monkeypatch.setenv("OTEL_LANGFUSE_PUBLIC_API_KEY", "pk")
        monkeypatch.setenv("OTEL_LANGFUSE_SECRET_API_KEY", "sk")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "langsmith"

    def test_vendor_key_alone_is_a_hint_not_a_backend(self, monkeypatch):
        clear_backend_env(monkeypatch)
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "k")
        assert backends.resolve_from_env() is None
        hints = backends.env_opt_in_hints()
        assert any("LANGSMITH_API_KEY" in h and "OTEL_LANGSMITH_API_KEY" in h for h in hints)
