"""Regressions for the bugs the refactor audit found (#413) that have no
natural home in an adapter's own test file."""

from __future__ import annotations

import logging

import pytest
from _helpers import clear_backend_env

from hermes_otel import backends
from hermes_otel.backend_specs import ENV_PRIORITY
from hermes_otel.dashboard.backends import langfuse, openobserve, phoenix
from hermes_otel.dashboard.backends.base import (
    BackendError,
    StructuredFilter,
    resolve_env_or_literal,
)
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin


class TestSecretPrecedence:
    """The exporter (``backends.choose_secret``) and the dashboard adapters
    (``resolve_env_or_literal``) must pick the same key when an entry sets
    both the inline value and a ``*_env`` name."""

    def test_inline_wins_over_the_named_env_in_both(self, monkeypatch):
        monkeypatch.setenv("K", "from-env")
        cfg = {"api_key": "inline", "api_key_env": "K"}
        assert resolve_env_or_literal(cfg, "api_key", "api_key_env") == "inline"
        assert backends.choose_secret("inline", "K", []).value == "inline"

    def test_named_env_when_no_inline(self, monkeypatch):
        monkeypatch.setenv("K", "from-env")
        assert resolve_env_or_literal({"api_key_env": "K"}, "api_key", "api_key_env") == "from-env"
        assert resolve_env_or_literal({"api_key": "  "}, "api_key", "api_key_env") is None


class TestPhoenixWithoutAProject:
    @pytest.fixture()
    def adapter(self, monkeypatch):
        monkeypatch.setattr(phoenix.PhoenixAdapter, "_resolve_project_id", lambda self: None)
        return phoenix.PhoenixAdapter(
            {"type": "phoenix", "endpoint": "http://localhost:6006/v1/traces"}
        )

    def test_search_returns_the_full_page_envelope(self, adapter):
        # ``{"traces": []}`` left the UI without ``has_more`` / the cursor (#413).
        assert adapter.search(StructuredFilter(), 0, 10, 5) == {
            "traces": [],
            "next_before_ns": None,
            "has_more": False,
        }

    def test_get_trace_is_not_found_not_an_empty_detail(self, adapter):
        with pytest.raises(BackendError) as ei:
            adapter.get_trace("0" * 32)
        assert ei.value.kind == "not_found"


class TestLangfuseStatusDoesNotProbe:
    def _adapter(self, **extra):
        return langfuse.LangfuseAdapter(
            {
                "type": "langfuse",
                "endpoint": "http://localhost:3002",
                "public_key": "pk",
                "secret_key": "sk",
                **extra,
            }
        )

    def test_unknown_api_answers_the_declaration_without_a_request(self, monkeypatch):
        def never(*_a, **_k):
            raise AssertionError("filter_support_for must not probe")

        monkeypatch.setattr(langfuse, "http_get_json", never)
        monkeypatch.setattr(langfuse, "_API_CACHE", {})
        assert self._adapter().filter_support_for(StructuredFilter()) == {}

    def test_a_cached_or_explicit_v4_answers_v4(self, monkeypatch):
        monkeypatch.setattr(langfuse, "http_get_json", lambda *a, **k: {"data": []})
        a = self._adapter()
        monkeypatch.setattr(langfuse, "_API_CACHE", {a.query_url: "v4"})
        assert a.filter_support_for() == dict(langfuse._V4_FILTER_SUPPORT)
        monkeypatch.setattr(langfuse, "_API_CACHE", {})
        assert self._adapter(query_api="v4").filter_support_for() == dict(
            langfuse._V4_FILTER_SUPPORT
        )


class TestOpenObserveMetricsShape:
    @staticmethod
    def _rows():
        # One cumulative counter series, three samples, timestamps in µs.
        base = 1_790_000_000_000_000
        return [
            {
                "_timestamp": base + i * 60_000_000,
                "value": v,
                "aggregation_temporality": "AGGREGATION_TEMPORALITY_CUMULATIVE",
                "is_monotonic": "true",
                "start_time": 1,
                "model": "m",
            }
            for i, v in enumerate((10, 15, 22))
        ]

    def test_reports_the_requested_and_the_effective_aggregate(self, monkeypatch):
        rows = self._rows()
        monkeypatch.setattr(openobserve.OpenObserveAdapter, "_search", lambda self, *a, **k: rows)
        a = openobserve.OpenObserveAdapter(
            {"type": "openobserve", "endpoint": "http://localhost:5080/api/default/v1/traces"}
        )
        start = 1_790_000_000 - 60
        end = start + 600
        out = a.metrics_query("hermes_token_usage_total", start, end, 60, agg="last")
        # ``last`` of a counter's increases is folded with ``sum``, like the
        # Prometheus-family adapters, and both spellings are reported (#413).
        assert out["cumulative"] is True
        assert out["agg_requested"] == "last" and out["agg"] == "sum"
        out = a.metrics_query("hermes_token_usage_total", start, end, 60, agg="sum")
        assert out["agg_requested"] == "sum" and out["agg"] == "sum"


class TestNoBackendMessage:
    def test_names_every_env_selectable_type(self, monkeypatch, caplog):
        # The message listed four env vars by hand; twenty types later it was
        # stale (#413). It now derives from the env priority list.
        clear_backend_env(monkeypatch)
        monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
        plugin = HermesOTelPlugin(config=HermesOtelConfig(dashboard_live=False))
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            assert plugin._backends_from_env() is None
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "No backend configured" in text
        for backend_type in ENV_PRIORITY:
            assert backend_type in text
