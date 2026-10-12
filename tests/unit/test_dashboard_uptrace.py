"""Uptrace adapter: response parsing and request shape (moved out of the
contract test in #406; the shared invariants stay in test_dashboard_adapter_contract.py)."""

from __future__ import annotations

from urllib import parse as _urlparse

import pytest
from helpers.dashboard import (  # noqa: F401
    CONTRACT_CFG as CFG,
)

from hermes_otel.dashboard import backends
from hermes_otel.dashboard.backends import (
    jaeger,
    langfuse,
    openobserve,
    phoenix,
    signoz,
    tempo,
    uptrace,
)
from hermes_otel.dashboard.backends.base import (
    BackendError,
    LogFilter,
    StructuredFilter,
)

MODULES = {
    "phx": phoenix,
    "lf": langfuse,
    "jg": jaeger,
    "lgtm": tempo,
    "sz": signoz,
    "up": uptrace,
    "oo": openobserve,
}


# ── declarations and helpers ───────────────────────────────────────────


class TestUptrace:
    def _adapter(self, extra=None):
        return uptrace.UptraceAdapter(
            {
                "type": "uptrace",
                "endpoint": "http://localhost:14318",
                "user_token": "t",
                **(extra or {}),
            }
        )

    def test_a_rejected_token_is_an_auth_error_and_caches_nothing(self, monkeypatch):
        def fake(url, headers=None, timeout=None):
            raise BackendError(503, "Backend rejected the credentials (401): bad token", "auth")

        monkeypatch.setattr(uptrace, "http_get_json", fake)
        with pytest.raises(BackendError) as exc:
            self._adapter().search(StructuredFilter(), 0, 10, 5)
        assert exc.value.kind == "auth" and uptrace._DIALECT_CACHE == {}

    def test_an_auth_error_after_the_probe_forgets_the_dialect(self, monkeypatch):
        answers = iter([{"systems": []}, None])

        def fake(url, headers=None, timeout=None):
            a = next(answers)
            if a is None:
                raise BackendError(503, "Backend rejected the credentials (401)", "auth")
            return a

        monkeypatch.setattr(uptrace, "http_get_json", fake)
        a = self._adapter()
        with pytest.raises(BackendError):
            a.search(StructuredFilter(), 0, 10, 5)
        assert uptrace._DIALECT_CACHE == {}

    def test_prefix_pin_and_status(self, monkeypatch):
        a = self._adapter()
        assert 'where _name like "tool.%"' in a._build_uql(StructuredFilter(name_prefix="tool."))
        # The log pin follows the plugin's resource service name; ``off`` removes it.
        monkeypatch.setattr(
            backends, "top_level_config", lambda: {"resource_attributes": {"service.name": "bot"}}
        )
        assert (
            uptrace.UptraceAdapter(
                {"type": "uptrace", "endpoint": "http://u:14318", "user_token": "t"}
            ).service_name
            == "bot"
        )
        off = uptrace.UptraceAdapter(
            {
                "type": "uptrace",
                "endpoint": "http://u:14318",
                "user_token": "t",
                "service_name": "off",
            }
        )
        assert off.service_name is None and off._log_clauses(LogFilter()) == []
        hit = uptrace._search_hit(
            {
                "id": "x",
                "traceId": "T",
                "name": "agent",
                "time": 1000.0,
                "statusCode": "unset",
                "attrs": {},
            },
            "T",
        )
        assert "status" not in {a["key"] for a in hit["spanSets"][0]["spans"][0]["attributes"]}

    def test_query_errors_on_spans_are_surfaced(self, monkeypatch):
        def fake(url, headers=None, timeout=None):
            if url.endswith("/systems?" + _urlparse.urlparse(url).query):
                return {"systems": []}
            return {"query": [{"error": "bad UQL"}], "spans": []}

        monkeypatch.setattr(uptrace, "http_get_json", fake)
        with pytest.raises(BackendError, match="bad UQL"):
            self._adapter().search(StructuredFilter(raw="where nonsense"), 0, 10, 5)

    def test_trace_search_is_pinned_to_the_agent_service_by_default(self):
        # Uptrace stores its own ``serve`` spans in the same project: without a
        # pin they fill page one (#298). The search's own service wins.
        a = self._adapter()
        assert a._build_uql(StructuredFilter()).startswith('where service_name = "hermes-agent"')
        assert a._build_uql(StructuredFilter(service="other")).startswith(
            'where service_name = "other"'
        )
        off = self._adapter({"service_name": "off"})
        assert "service_name" not in off._build_uql(StructuredFilter())
