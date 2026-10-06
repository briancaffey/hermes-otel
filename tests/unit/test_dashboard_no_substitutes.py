"""Dashboard adapters never show substitute data as if it were what was asked for (#159)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve().parent.parent.parent
_DASHBOARD = _HERE / "hermes_otel" / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))
try:  # the real FastAPI when the dev extra has it; a stub otherwise
    import fastapi  # noqa: F401
except ImportError:
    _stub = types.ModuleType("fastapi")

    class _StubHTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    _stub.HTTPException = _StubHTTPException  # type: ignore[attr-defined]
    sys.modules["fastapi"] = _stub

from backends.langfuse import LangfuseAdapter  # noqa: E402
from backends.phoenix import PhoenixAdapter  # noqa: E402
from fastapi import HTTPException  # noqa: E402

_PROJECTS = {
    "projects": {
        "edges": [
            {"node": {"id": "P1", "name": "empty-project", "hasTraces": False}},
            {"node": {"id": "P2", "name": "hermes-agent", "hasTraces": True}},
        ]
    }
}


def _phoenix(project_name=None):
    cfg: Dict[str, Any] = {"type": "phoenix", "endpoint": "http://localhost:6006"}
    if project_name:
        cfg["project_name"] = project_name
    with patch("backends.top_level_config", return_value={}):
        return PhoenixAdapter(cfg)


def _fake_post(url, body, headers=None, timeout=None):
    return {"data": _PROJECTS}


class TestPhoenixProject:
    def test_configured_but_missing_project_is_an_error_not_a_substitute(self):
        adapter = _phoenix("pr-typo")
        with patch("backends.phoenix.http_post_json", side_effect=_fake_post):
            with pytest.raises(HTTPException) as exc:
                adapter._resolve_project_id()
        assert exc.value.status_code == 404
        assert "'pr-typo' not found" in exc.value.detail
        assert "empty-project, hermes-agent" in exc.value.detail
        assert adapter._project_id_cache is None  # nothing cached, nothing shown

    def test_configured_project_resolves_by_name(self):
        adapter = _phoenix("hermes-agent")
        with patch("backends.phoenix.http_post_json", side_effect=_fake_post):
            assert adapter._resolve_project_id() == "P2"
        st = adapter.status()
        assert st["project_name"] == "hermes-agent"
        assert st["project_resolved"] == "hermes-agent"
        assert st["project_fallback"] is False

    def test_no_project_configured_falls_back_and_says_so(self):
        adapter = _phoenix()
        with patch("backends.phoenix.http_post_json", side_effect=_fake_post):
            assert adapter._resolve_project_id() == "P2"  # first with traces
        st = adapter.status()
        assert st["project_name"] is None
        assert st["project_resolved"] == "hermes-agent"
        assert st["project_fallback"] is True


def _langfuse() -> LangfuseAdapter:
    return LangfuseAdapter(
        {
            "type": "langfuse",
            "endpoint": "http://localhost:3000",
            "public_key": "pk",
            "secret_key": "sk",
        }
    )


def _obs(oid: str, name: str, parent=None) -> Dict[str, Any]:
    return {
        "id": oid,
        "type": "GENERATION",
        "name": name,
        "startTime": "2026-09-20T10:00:00.100Z",
        "endTime": "2026-09-20T10:00:01.000Z",
        "parentObservationId": parent,
    }


class TestLangfuseSyntheticRoot:
    def test_root_built_from_the_trace_record_is_marked(self):
        # Two parentless observations: nothing holds the tree together, so a
        # synthetic root is built from the trace record and marked as such.
        trace = {
            "id": "abcdef0123456789abcdef0123456789",
            "name": "agent",
            "timestamp": "2026-09-20T10:00:00.000Z",
            "sessionId": "s-1",
            "observations": [_obs("obs1", "api.m"), _obs("obs2", "tool.terminal")],
        }
        with patch("backends.langfuse.http_get_json", return_value=trace):
            out = _langfuse().get_trace(trace["id"])
        spans = out["batches"][0]["scopeSpans"][0]["spans"]
        root = spans[0]
        attrs = {a["key"]: a["value"] for a in root["attributes"]}
        assert root["parentSpanId"] is None
        assert attrs["synthetic"] == {"boolValue": True}
        assert "synthetic.reason" in attrs
        assert attrs["langfuse.sessionId"] == {"stringValue": "s-1"}
        # A synthetic id can never collide with a real 16-hex observation id.
        assert root["spanId"].startswith("synthetic-")
        # The real observations are untouched and hang off the synthetic root.
        for real in spans[1:]:
            assert "synthetic" not in {a["key"] for a in real["attributes"]}
            assert real["parentSpanId"] == root["spanId"]
        assert out["span_count"] == 2 and out["truncated"] is False

    def test_a_real_root_observation_is_kept_as_the_root(self):
        # An OTLP-ingested turn has its ``agent`` span as the one parentless
        # observation: no synthetic node (#294), the trace record's facts join it.
        trace = {
            "id": "abcdef0123456789abcdef0123456789",
            "name": "agent",
            "timestamp": "2026-09-20T10:00:00.000Z",
            "sessionId": "s-1",
            "input": {"q": "hi"},
            "observations": [_obs("root1", "agent"), _obs("obs1", "api.m", parent="root1")],
        }
        with patch("backends.langfuse.http_get_json", return_value=trace):
            out = _langfuse().get_trace(trace["id"])
        spans = out["batches"][0]["scopeSpans"][0]["spans"]
        assert [s["name"] for s in spans] == ["agent", "api.m"]
        root = spans[0]
        attrs = {a["key"]: a["value"] for a in root["attributes"]}
        assert root["parentSpanId"] is None and "synthetic" not in attrs
        assert attrs["langfuse.sessionId"] == {"stringValue": "s-1"}
        assert attrs["input.value"] == {"stringValue": '{"q": "hi"}'}
        assert spans[1]["parentSpanId"] == "root1"

    def test_non_dict_observations_are_skipped_not_a_crash(self):
        trace = {
            "id": "abcdef0123456789abcdef0123456789",
            "name": "agent",
            "timestamp": "2026-09-20T10:00:00.000Z",
            "observations": ["obs-id-only", _obs("root1", "agent")],
        }
        with patch("backends.langfuse.http_get_json", return_value=trace):
            out = _langfuse().get_trace(trace["id"])
        assert out["span_count"] == 1

    def test_unknown_trace_is_not_found(self):
        with patch("backends.langfuse.http_get_json", return_value={}):
            with pytest.raises(HTTPException) as exc:
                _langfuse().get_trace("nope")
        assert exc.value.status_code == 404 and exc.value.kind == "not_found"
