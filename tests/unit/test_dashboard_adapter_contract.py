"""The adapter contract of milestone 8 (#290, #292–#299): declared filter
support, cursor paging, the error taxonomy, the caches, and the response
parsing of the adapters that only had request-shape tests."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch
from urllib import error as _urlerror
from urllib import parse as _urlparse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from hermes_otel.dashboard import backends, plugin_api
from hermes_otel.dashboard.backends import (
    _loki,
    _prometheus,
    base,
    jaeger,
    langfuse,
    openobserve,
    phoenix,
    signoz,
    tempo,
    uptrace,
)
from hermes_otel.dashboard.backends.base import (
    FILTER_KEYS,
    BackendError,
    ConfigError,
    LogFilter,
    StructuredFilter,
    error_kind,
    filter_support_of,
    otlp_metric_name,
    split_applied_filters,
    strictly_older_traces,
    trace_page,
)

NS = 1_000_000_000

CFG: List[Dict[str, Any]] = [
    {"type": "phoenix", "name": "phx", "endpoint": "http://localhost:6006"},
    {
        "type": "langfuse",
        "name": "lf",
        "endpoint": "http://localhost:3000",
        "public_key": "pk",
        "secret_key": "sk",
    },
    {"type": "jaeger", "name": "jg", "endpoint": "http://localhost:16686"},
    {"type": "lgtm", "name": "lgtm", "endpoint": "http://localhost:4318/v1/traces"},
    {"type": "signoz", "name": "sz", "endpoint": "http://localhost:3301", "api_key": "k"},
    {"type": "uptrace", "name": "up", "endpoint": "http://localhost:14318", "user_token": "t"},
    {
        "type": "openobserve",
        "name": "oo",
        "endpoint": "http://user:pw@localhost:5080/api/default/v1/traces",
        "user": "u",
        "password": "p",
    },
]
MODULES = {
    "phx": phoenix,
    "lf": langfuse,
    "jg": jaeger,
    "lgtm": tempo,
    "sz": signoz,
    "up": uptrace,
    "oo": openobserve,
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """No developer config, no cached adapters, no probed Uptrace dialect."""
    monkeypatch.setattr(backends, "top_level_config", lambda: {})
    backends.clear_caches()
    uptrace._DIALECT_CACHE.clear()
    yield
    backends.clear_caches()
    uptrace._DIALECT_CACHE.clear()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(backends, "load_config", lambda: (Path("/x/hermes_otel.yaml"), CFG, None))
    app = FastAPI()
    app.include_router(plugin_api.router)
    with TestClient(app) as c:
        yield c


def _down(*_a, **_k):
    raise BackendError(502, "Backend unreachable: [Errno 61] Connection refused")


def _row(trace_id: str, start_ns: int) -> Dict[str, Any]:
    return {"traceID": trace_id, "startTimeUnixNano": str(start_ns), "spanSets": []}


# ── declarations and helpers ───────────────────────────────────────────


class TestDeclarations:
    def test_every_adapter_declares_every_filter_field(self):
        for cls in backends.adapters():
            declared = cls.filter_support
            assert declared, f"{cls.__name__} declares no filter_support"
            assert set(declared) == set(FILTER_KEYS), cls.__name__
            assert set(declared.values()) <= {"server", "client", "none"}, cls.__name__
            assert filter_support_of(cls) == declared

    def test_status_reports_the_declaration_and_strips_userinfo(self, client):
        st = client.get("/status", params={"backend": "lf"}).json()
        assert st["filters"]["model"] == "none" and st["filters"]["session"] == "server"
        by = {b["name"]: b for b in st["available"]}
        assert by["oo"]["endpoint"] == "http://localhost:5080/api/default/v1/traces"
        assert by["jg"]["filters"]["roots_only"] == "client"

    def test_split_applied_filters(self):
        f = StructuredFilter(
            attr_equals={"llm.model_name": "m", "hermes.session_id": "s"},
            min_duration_ms=5,
            free_text="x",
            name_prefix="tool.",
        )
        applied, ignored = split_applied_filters(langfuse.LangfuseAdapter, f)
        assert applied == ["name", "session", "min_duration"]
        assert ignored == ["model", "free_text", "roots_only"]

    def test_trace_page_and_cursor(self):
        rows = [_row("c", 10 * NS), _row("a", 30 * NS), _row("b", 20 * NS), _row("d", 5 * NS)]
        page = trace_page(rows, 2)
        assert [t["traceID"] for t in page["traces"]] == ["a", "b"]
        assert (page["has_more"], page["next_before_ns"]) == (True, 20 * NS)
        older = strictly_older_traces(rows, StructuredFilter(before_ns=page["next_before_ns"]))
        assert [t["traceID"] for t in older] == ["c", "d"]
        last = trace_page(older, 2)
        assert (last["has_more"], last["next_before_ns"]) == (False, None)

    def test_otlp_metric_names(self):
        assert otlp_metric_name("hermes_token_usage_total") == "hermes.token.usage"
        assert otlp_metric_name("hermes_tool_duration_milliseconds_sum") == "hermes.tool.duration"
        assert otlp_metric_name("hermes_prompt_cache_tokens_total") == "hermes.prompt_cache.tokens"
        assert otlp_metric_name("hermes.tool.duration.count") == "hermes.tool.duration"
        assert otlp_metric_name("gen_ai_client_token_usage") == "gen_ai.client.token.usage"
        assert otlp_metric_name("process_cpu_utilization_ratio") == "process.cpu.utilization"


# ── error taxonomy ─────────────────────────────────────────────────────


def _http_error(code: int, body: str = "") -> _urlerror.HTTPError:
    return _urlerror.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


class TestErrorTaxonomy:
    @pytest.mark.parametrize(
        "raised, kind, status, needle",
        [
            (_http_error(404, '{"error": "no such trace"}'), "not_found", 404, "404"),
            (_http_error(401, "unauthorized"), "auth", 503, "credentials"),
            (_http_error(403, "forbidden"), "auth", 503, "credentials"),
            (_http_error(500, "<html><body>boom</body></html>"), "backend", 502, "HTML page"),
            (_urlerror.URLError("refused"), "backend", 502, "unreachable"),
            (TimeoutError("timed out"), "backend", 502, "failed"),
        ],
    )
    def test_execute_maps_every_failure_to_a_kind(self, raised, kind, status, needle):
        with patch.object(base._urlrequest, "urlopen", side_effect=raised):
            with pytest.raises(BackendError) as exc:
                base.http_get_json("http://x/api")
        assert (exc.value.kind, exc.value.status_code) == (kind, status)
        assert needle in exc.value.detail

    def test_non_json_body_and_body_excerpt(self):
        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch.object(base._urlrequest, "urlopen", return_value=_Resp(b"not json")):
            with pytest.raises(BackendError, match="non-JSON"):
                base.http_get_json("http://x/api")
        long = _http_error(500, "x" * 1000)
        with patch.object(base._urlrequest, "urlopen", side_effect=long):
            with pytest.raises(BackendError) as exc:
                base.http_get_json("http://x/api")
        assert len(exc.value.detail) < 300 and exc.value.detail.endswith("…")

    def test_error_kind_of_plain_exceptions(self):
        assert error_kind(HTTPException(status_code=422, detail="x")) == "request"
        assert error_kind(HTTPException(status_code=503, detail="x")) == "config"
        assert error_kind(RuntimeError("x")) == "backend"
        assert error_kind(ConfigError("x")) == "config"

    @pytest.mark.parametrize("name", list(MODULES))
    def test_backend_down_is_a_502_with_a_kind_on_every_adapter(self, client, monkeypatch, name):
        mod = MODULES[name]
        for fn in ("http_get_json", "http_post_json"):
            if hasattr(mod, fn):
                monkeypatch.setattr(mod, fn, _down)
        for helper in (_loki, _prometheus):
            monkeypatch.setattr(helper, "http_get_json", _down)
        r = client.get("/traces/search", params={"backend": name})
        assert r.status_code == 502, (name, r.text)
        assert r.json() == {
            "detail": "Backend unreachable: [Errno 61] Connection refused",
            "kind": "backend",
        }
        if MODULES[name].__name__.endswith(("lgtm", "tempo", "signoz", "uptrace", "openobserve")):
            r = client.get("/logs/search", params={"backend": name})
            assert r.status_code == 502 and r.json()["kind"] == "backend"

    def test_a_search_404_is_a_wrong_url_not_a_missing_trace(self, client, monkeypatch):
        def gone(*_a, **_k):
            raise BackendError(
                404, "Backend returned 404: (an HTML page, not an API answer)", "not_found"
            )

        monkeypatch.setattr(jaeger, "http_get_json", gone)
        r = client.get("/traces/search", params={"backend": "jg"})
        assert r.status_code == 502 and r.json()["kind"] == "backend"

    @pytest.mark.parametrize(
        "name, answer",
        [
            ("phx", {"data": {"node": {"name": "p", "trace": None}}}),
            ("lf", {}),
            ("jg", {"data": []}),
            ("lgtm", {"batches": []}),
            ("sz", []),
            ("up", {"spans": []}),
            ("oo", {"hits": []}),
        ],
    )
    def test_unknown_trace_is_404_not_found(self, client, monkeypatch, name, answer):
        mod = MODULES[name]

        def fake(*_a, **_k):
            return answer

        for fn in ("http_get_json", "http_post_json"):
            if hasattr(mod, fn):
                monkeypatch.setattr(mod, fn, fake)
        if name == "phx":
            monkeypatch.setattr(
                phoenix.PhoenixAdapter, "_resolve_project_id", lambda self: "P1", raising=True
            )
            monkeypatch.setattr(phoenix.PhoenixAdapter, "trace_url", lambda self, t: None)
        if name == "lf":
            monkeypatch.setattr(langfuse.LangfuseAdapter, "trace_url", lambda self, t: None)
        r = client.get("/traces/deadbeef", params={"backend": name})
        assert r.status_code == 404, (name, r.text)
        assert r.json()["kind"] == "not_found" and "deadbeef" in r.json()["detail"]

    @pytest.mark.parametrize(
        "entry",
        [
            {"type": "langfuse", "name": "x", "endpoint": "http://localhost:3000"},
            {"type": "signoz", "name": "x", "endpoint": "http://localhost:3301"},
            {"type": "openobserve", "name": "x", "endpoint": "http://localhost:5080"},
            {"type": "uptrace", "name": "x", "endpoint": "http://localhost:14318"},
            {"type": "uptrace", "name": "x", "user_token": "t"},
        ],
    )
    def test_missing_credentials_or_url_is_a_503_config_error(self, monkeypatch, entry):
        monkeypatch.setattr(backends, "load_config", lambda: (None, [entry], None))
        monkeypatch.delenv("UPTRACE_USER_TOKEN", raising=False)
        app = FastAPI()
        app.include_router(plugin_api.router)
        with TestClient(app) as c:
            r = c.get("/traces/search", params={"backend": "x"})
        assert r.status_code == 503 and r.json()["kind"] == "config", r.text

    def test_a_broken_entry_is_reported_as_config_not_unsupported(self, monkeypatch):
        entry = {"type": "uptrace", "name": "x", "endpoint": "http://u:14318", "project_id": "abc"}
        monkeypatch.setattr(backends, "load_config", lambda: (None, [entry], None))
        app = FastAPI()
        app.include_router(plugin_api.router)
        with TestClient(app) as c:
            r = c.get("/traces/search", params={"backend": "x"})
        assert r.status_code == 503 and r.json()["kind"] == "config"
        assert "could not be set up" in r.json()["detail"]
        # The default pick skips it instead of failing the whole dashboard.
        assert backends.resolve_adapter()[0] is None

    def test_an_adapter_parsing_error_is_a_502_not_a_500(self, client, monkeypatch):
        broken = {"data": [{"traceID": "t", "spans": [{"spanID": "a", "tags": 5}]}]}
        monkeypatch.setattr(jaeger, "http_get_json", lambda *a, **k: broken)
        r = client.get("/traces/search", params={"backend": "jg"})
        assert r.status_code == 502 and r.json()["kind"] == "backend"
        assert "could not read the backend's answer" in r.json()["detail"]


# ── routes: paging, windows, validation ───────────────────────────────


class TestRoutes:
    def test_search_passes_the_cursor_and_prefix_and_reports_filters(self, client, monkeypatch):
        seen = {}

        def fake_search(self, f, start_s, end_s, limit):
            seen.update(f=f, start=start_s, end=end_s, limit=limit)
            return {"traces": [_row("a", 5 * NS)], "has_more": True, "next_before_ns": 5 * NS}

        monkeypatch.setattr(langfuse.LangfuseAdapter, "search", fake_search)
        r = client.get(
            "/traces/search",
            params={
                "backend": "lf",
                "name_prefix": "tool.",
                "before_ns": 99 * NS,
                "model": "gpt-4o",
                "session": "s1",
                "start_s": 100,
                "end_s": 200,
            },
        ).json()
        assert seen["f"].name_prefix == "tool." and seen["f"].before_ns == 99 * NS
        assert (seen["start"], seen["end"]) == (100, 200)
        assert r["has_more"] is True and r["next_before_ns"] == 5 * NS
        assert r["applied_filters"] == ["name", "session"]
        # Langfuse lists traces, so the (default) roots-only switch does nothing there.
        assert r["ignored_filters"] == ["model", "roots_only"]
        assert r["backend"] == "lf"

    def test_legacy_adapter_answers_get_the_envelope(self, client, monkeypatch):
        monkeypatch.setattr(
            phoenix.PhoenixAdapter, "search", lambda self, f, s, e, l: {"traces": []}
        )
        r = client.get("/traces/search", params={"backend": "phx"}).json()
        assert (r["has_more"], r["next_before_ns"], r["traces"]) == (False, None, [])

    def test_trace_detail_carries_truncated_and_span_count(self, client, monkeypatch):
        monkeypatch.setattr(
            tempo.TempoAdapter,
            "get_trace",
            lambda self, t: {"batches": [], "truncated": True, "span_count": 501},
        )
        r = client.get("/traces/abc", params={"backend": "lgtm"}).json()
        assert r["truncated"] is True and r["span_count"] == 501 and r["backend"] == "lgtm"

    def test_backend_metrics_take_an_absolute_window_and_cap_the_buckets(self, client, monkeypatch):
        seen = {}

        def fake_query(self, name, start_s, end_s, bucket_s, group_by=None, agg="sum"):
            seen.update(start=start_s, end=end_s, bucket=bucket_s, group_by=group_by)
            return {"name": name, "agg": agg, "bucketS": bucket_s, "buckets": [], "series": {}}

        monkeypatch.setattr(tempo.TempoAdapter, "metrics_query", fake_query)
        r = client.get(
            "/metrics/query",
            params={
                "backend": "lgtm",
                "name": "hermes_token_usage_total",
                "start_s": 1000,
                "end_s": 4600,
                "bucket_s": 60,
                "group_by": "model",
            },
        )
        assert r.status_code == 200 and (seen["start"], seen["end"], seen["bucket"]) == (
            1000,
            4600,
            60,
        )
        assert r.json()["otlp_name"] == "hermes.token.usage" and seen["group_by"] == "model"
        r = client.get(
            "/metrics/query",
            params={"backend": "lgtm", "name": "x", "lookback_hours": 8760, "bucket_s": 1},
        )
        assert r.status_code == 422 and "buckets" in r.json()["detail"]

    def test_metric_name_and_label_are_validated_before_any_query(self, client, monkeypatch):
        monkeypatch.setattr(_prometheus, "http_get_json", _down)
        r = client.get(
            "/metrics/query", params={"backend": "lgtm", "name": "up) or vector(1", "bucket_s": 60}
        )
        assert r.status_code == 400 and r.json()["kind"] == "request"
        r = client.get(
            "/metrics/query",
            params={"backend": "lgtm", "name": "up", "group_by": 'a") or (1', "bucket_s": 60},
        )
        assert r.status_code == 400 and "group_by" in r.json()["detail"]

    def test_metric_names_carry_the_otlp_name(self, client, monkeypatch):
        monkeypatch.setattr(
            tempo.TempoAdapter,
            "metric_names",
            lambda self, s, e: [{"name": "hermes_tool_duration_milliseconds_sum"}],
        )
        names = client.get("/metrics/names", params={"backend": "lgtm"}).json()["names"]
        assert names == [
            {"name": "hermes_tool_duration_milliseconds_sum", "otlp_name": "hermes.tool.duration"}
        ]

    def test_logs_search_passes_span_id(self, client, monkeypatch):
        seen = {}

        def fake(self, f, start_s, end_s, limit):
            seen["f"] = f
            return []

        monkeypatch.setattr(tempo.TempoAdapter, "logs_search", fake)
        client.get("/logs/search", params={"backend": "lgtm", "span_id": "abc123"})
        assert seen["f"].span_id == "abc123"
        assert '| span_id="abc123"' in _loki.logql_for(LogFilter(span_id="abc123"))


# ── caches ─────────────────────────────────────────────────────────────


class TestCaches:
    def test_adapter_instances_are_reused_until_the_config_changes(self, tmp_path, monkeypatch):
        cfg = tmp_path / "hermes_otel.yaml"
        cfg.write_text("backends:\n  - type: jaeger\n    name: jg\n", encoding="utf-8")
        monkeypatch.setattr(backends, "resolve_config_path", lambda: cfg)
        a, *_ = backends.resolve_adapter("jg")
        b, *_ = backends.resolve_adapter("jg")
        assert a is b
        # An edit (a new identity: size changes) yields a fresh instance.
        cfg.write_text(
            "backends:\n  - type: jaeger\n    name: jg\n    query_port: 16687\n", encoding="utf-8"
        )
        os.utime(cfg, (os.path.getmtime(cfg) + 5, os.path.getmtime(cfg) + 5))
        c, *_ = backends.resolve_adapter("jg")
        assert c is not a and c.query_url.endswith(":16687")
        backends.clear_caches()
        d, *_ = backends.resolve_adapter("jg")
        assert d is not c

    def test_config_is_parsed_once_per_file_identity(self, tmp_path, monkeypatch):
        cfg = tmp_path / "hermes_otel.yaml"
        cfg.write_text("project_name: p1\nbackends: []\n", encoding="utf-8")
        monkeypatch.setattr(backends, "resolve_config_path", lambda: cfg)
        import yaml

        calls = []
        real = yaml.safe_load

        def counting(stream):
            calls.append(1)
            return real(stream)

        monkeypatch.setattr(yaml, "safe_load", counting)
        assert backends._load_raw_config()[1]["project_name"] == "p1"
        assert backends._load_raw_config()[1]["project_name"] == "p1"
        assert len(calls) == 1


# ── per-adapter behaviour ──────────────────────────────────────────────


class TestTempo:
    def _adapter(self):
        return tempo.TempoAdapter({"type": "lgtm", "endpoint": "http://localhost:4318/v1/traces"})

    def test_predicates_include_prefix_duration_and_status(self):
        q = self._adapter()._build_traceql(
            StructuredFilter(name_prefix="tool.", min_duration_ms=250, status="ok", service="s")
        )
        assert q.startswith(
            '{ nestedSetParent < 0 && resource.service.name = "s" && name =~ "^tool\\\\..*" && status = ok && duration >= 250ms }'
        )  # Tempo anchors the regex at both ends, so the prefix carries ``.*``
        assert "| select(" in q

    def test_a_4xx_retries_without_select_but_with_every_predicate(self, monkeypatch):
        urls = []

        def fake(url, headers=None, timeout=None):
            urls.append(url)
            if len(urls) == 1:
                raise BackendError(502, "Backend returned 400: parse error at select")
            return {"traces": []}

        monkeypatch.setattr(tempo, "http_get_json", fake)
        self._adapter().search(StructuredFilter(status="error", name_prefix="agent"), 0, 10, 5)
        assert len(urls) == 2
        second = _urlparse.unquote_plus(urls[1])
        assert "status = error" in second and 'name =~ "^agent.*"' in second
        assert "select(" not in second

    def test_unreachable_is_not_retried(self, monkeypatch):
        calls = []

        def fake(url, headers=None, timeout=None):
            calls.append(url)
            raise BackendError(502, "Backend unreachable: refused")

        monkeypatch.setattr(tempo, "http_get_json", fake)
        with pytest.raises(BackendError, match="unreachable"):
            self._adapter().search(StructuredFilter(), 0, 10, 5)
        assert len(calls) == 1

    def test_page_cursor_and_service_stats(self, monkeypatch):
        traces = [
            {
                "traceID": t,
                "rootTraceName": "agent",
                "startTimeUnixNano": str(s),
                "serviceStats": {"hermes-agent": {"spanCount": 7}},
                "spanSets": [{"spans": [{"name": "agent"}]}],
            }
            for t, s in (("a", 30 * NS), ("b", 20 * NS), ("c", 10 * NS))
        ]
        seen = {}

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {"traces": traces, "metrics": {"inspectedTraces": 3}}

        monkeypatch.setattr(tempo, "http_get_json", fake)
        page = self._adapter().search(StructuredFilter(before_ns=25 * NS), 0, 100, 1)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert (q["limit"], q["end"]) == (str(tempo._DEFAULT_SEARCH_FETCH), "26")
        # ids come back padded to 32 hex digits (Tempo drops leading zeros)
        assert [t["traceID"] for t in page["traces"]] == ["b".zfill(32)]
        assert page["has_more"] is True and page["next_before_ns"] == 20 * NS
        assert page["traces"][0]["spanCount"] == 7 and page["metrics"] == {"inspectedTraces": 3}
        assert "raw" not in page

    def test_free_text_keeps_the_root_in_its_own_span_set(self, monkeypatch):
        seen = {}

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {"traces": []}

        monkeypatch.setattr(tempo, "http_get_json", fake)
        self._adapter().search(StructuredFilter(free_text="B1-x", status="ok"), 0, 10, 5)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        # the root set carries the structured filters, the text is a second set
        assert q["q"].startswith(
            '{ nestedSetParent < 0 && status = ok } && { (span.input.value =~ ".*B1-x.*" || span.output.value =~ ".*B1-x.*") }'
        )
        assert q["spss"] == "1"
        # widened to every span: one set, no spss cap
        self._adapter().search(StructuredFilter(free_text="B1-x", roots_only=False), 0, 10, 5)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert q["q"].startswith("{ (span.input.value =~") and q["spss"] == "1"

    def test_widened_search_names_the_card_after_the_matched_span(self, monkeypatch):
        trace = {
            "traceID": "ab",
            "rootTraceName": "agent",
            "startTimeUnixNano": "1000000000",
            "spanSets": [
                {
                    "spans": [
                        {"attributes": [{"key": "name", "value": {"stringValue": "tool.terminal"}}]}
                    ]
                }
            ],
        }
        monkeypatch.setattr(tempo, "http_get_json", lambda *a, **k: {"traces": [trace]})
        page = self._adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False), 0, 10, 5
        )
        assert page["traces"][0]["rootTraceName"] == "tool.terminal"
        # Tempo 3 puts the selected ``name`` on the span itself
        trace["spanSets"] = [{"spans": [{"name": "tool.read_file", "attributes": []}]}]
        trace["rootTraceName"] = "agent"
        page = self._adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False), 0, 10, 5
        )
        assert page["traces"][0]["rootTraceName"] == "tool.read_file"
        # roots-only keeps the root's name
        trace["rootTraceName"] = "agent"
        page = self._adapter().search(StructuredFilter(name_prefix="agent"), 0, 10, 5)
        assert page["traces"][0]["rootTraceName"] == "agent"

    def test_search_over_fetches_and_cuts_the_newest_rows(self, monkeypatch):
        # Tempo stops at ``limit`` matches in block order, not the newest
        # (Tempo 3.0.3, live): ask for ``search_fetch`` rows and cut here.
        seen = {}
        rows = [
            {"traceID": t, "rootTraceName": "agent", "startTimeUnixNano": str(s)}
            for t, s in (("old", 10 * NS), ("new", 30 * NS), ("mid", 20 * NS))
        ]

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {"traces": rows}

        monkeypatch.setattr(tempo, "http_get_json", fake)
        page = self._adapter().search(StructuredFilter(), 0, 100, 2)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert q["limit"] == str(tempo._DEFAULT_SEARCH_FETCH)
        assert [t["traceID"] for t in page["traces"]] == [
            "new",
            "mid",
        ]  # non-hex ids stay as they are
        assert page["has_more"] is True and page["next_before_ns"] == 20 * NS
        # configurable per entry, never below limit + 1
        a = tempo.TempoAdapter(
            {"type": "tempo", "endpoint": "http://localhost:4318/v1/traces", "search_fetch": 2}
        )
        a.search(StructuredFilter(), 0, 100, 5)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert q["limit"] == "6"

    def test_raw_query_bounds_duration_client_side_and_sends_no_min_duration(self, monkeypatch):
        # Tempo ignores ``minDuration`` next to a TraceQL ``q`` (verified live on
        # grafana/otel-lgtm 0.34): the bound is applied to the rows instead.
        seen = {}
        rows = [
            {
                "traceID": "a",
                "rootTraceName": "agent",
                "startTimeUnixNano": str(30 * NS),
                "durationMs": 12110,
            },
            {
                "traceID": "b",
                "rootTraceName": "agent",
                "startTimeUnixNano": str(20 * NS),
                "durationMs": 1938,
            },
            {"traceID": "c", "rootTraceName": "agent", "startTimeUnixNano": str(10 * NS)},
        ]

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {"traces": rows}

        monkeypatch.setattr(tempo, "http_get_json", fake)
        page = self._adapter().search(StructuredFilter(raw="{}", min_duration_ms=5000), 0, 100, 50)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert q["q"].startswith("{}") and "minDuration" not in q
        assert [t["traceID"] for t in page["traces"]] == ["a".zfill(32)]
        # without a raw query the bound stays a predicate and nothing is dropped here
        page = self._adapter().search(StructuredFilter(min_duration_ms=5000), 0, 100, 50)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert "duration >= 5000ms" in q["q"] and len(page["traces"]) == 3

    def test_raw_query_reports_the_structured_fields_as_ignored(self):
        from hermes_otel.dashboard.backends.base import split_applied_filters

        a = self._adapter()
        f = StructuredFilter(
            raw="{ span.foo = 1 }",
            min_duration_ms=10,
            status="error",
            name_prefix="tool.",
            attr_equals={"tool.name": "terminal"},
            free_text="x",
        )
        applied, ignored = split_applied_filters(a, f)
        assert applied == ["min_duration", "raw"]
        assert ignored == ["name", "tool", "status_error", "free_text", "roots_only"]
        # the declaration itself is unchanged for ordinary searches
        applied, ignored = split_applied_filters(
            a, StructuredFilter(status="error", name_prefix="tool.")
        )
        assert applied == ["name", "status_error", "roots_only"] and ignored == []

    def test_trace_url_is_grafana_explore_only_with_ui_url(self):
        assert self._adapter().trace_url("abc") is None
        a = tempo.TempoAdapter(
            {
                "type": "lgtm",
                "endpoint": "http://localhost:4318/v1/traces",
                "ui_url": "http://localhost:3001/explore/",
            }
        )
        url = a.trace_url("0ae1")
        assert url.startswith("http://localhost:3001/explore?schemaVersion=1&panes=")
        panes = json.loads(dict(_urlparse.parse_qsl(_urlparse.urlparse(url).query))["panes"])
        assert panes["a"]["queries"][0] == {
            "refId": "A",
            "datasource": {"type": "tempo", "uid": "tempo"},
            "queryType": "traceql",
            "query": "0ae1",
        }

    def test_prometheus_last_on_a_counter_is_an_honest_sum(self):
        assert _prometheus.effective_agg("c_total", "last") == "sum"
        assert _prometheus.effective_agg("g", "last") == "last"
        with pytest.raises(BackendError) as exc:
            _prometheus.promql_for("up) or vector(1", 60, None, "sum")
        assert exc.value.kind == "request"

    def test_loki_windows_are_clamped_to_thirty_days(self, monkeypatch):
        seen = {}

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {"data": {"result": []}}

        monkeypatch.setattr(_loki, "http_get_json", fake)
        year = 365 * 86400
        _loki.loggers("http://l:3100", 0, year)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert f"[{30 * 86400}s]" in q["query"]
        _loki.logs_search("http://l:3100", LogFilter(), 0, year, 10)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert int(q["end"]) - int(q["start"]) == 30 * 86400 * NS


class TestOpenObserve:
    def _adapter(self):
        return openobserve.OpenObserveAdapter(
            {
                "type": "openobserve",
                "endpoint": "http://localhost:5080/api/default/v1/traces",
                "user": "u",
                "password": "p",
            }
        )

    def test_trace_url_opens_the_trace_details_page(self):
        url = self._adapter().trace_url("abc123")
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(url).query))
        assert url.startswith("http://localhost:5080/web/traces/trace-details?")
        assert (q["org_identifier"], q["stream"], q["trace_id"]) == ("default", "default", "abc123")
        assert int(q["from"]) < int(q["to"])

    def test_an_empty_root_query_is_the_answer(self, monkeypatch):
        sqls = []

        def fake(url, body, headers=None, timeout=None):
            sqls.append(body["query"]["sql"])
            return {"hits": []}

        monkeypatch.setattr(openobserve, "http_post_json", fake)
        out = self._adapter().search(StructuredFilter(name_prefix="api."), 0, 10, 5)
        assert out["traces"] == [] and len(sqls) == 1
        assert "operation_name LIKE 'api.%'" in sqls[0] and "reference_parent_span_id" in sqls[0]

    def test_a_rejected_column_falls_through_and_roots_are_kept_client_side(self, monkeypatch):
        sqls = []
        rows = [
            {"trace_id": "t1", "span_id": "r", "operation_name": "agent", "start_time": 20 * NS},
            {
                "trace_id": "t2",
                "span_id": "c",
                "operation_name": "api.m",
                "start_time": 10 * NS,
                "reference": '[{"refType":"CHILD_OF","spanId":"zz"}]',
            },
        ]

        def fake(url, body, headers=None, timeout=None):
            sql = body["query"]["sql"]
            sqls.append(sql)
            if "GROUP BY" in sql:
                return {"hits": []}
            if "reference" in sql:
                raise BackendError(502, "Backend returned 400: unknown field reference")
            return {"hits": rows}

        monkeypatch.setattr(openobserve, "http_post_json", fake)
        out = self._adapter().search(StructuredFilter(), 0, 10, 5)
        assert [t["traceID"] for t in out["traces"]] == ["t1"]
        assert len([s for s in sqls if "GROUP BY" not in s]) == 3

    def test_unreachable_stops_the_attempt_chain(self, monkeypatch):
        calls = []

        def fake(url, body, headers=None, timeout=None):
            calls.append(1)
            raise BackendError(502, "Backend unreachable: refused")

        monkeypatch.setattr(openobserve, "http_post_json", fake)
        with pytest.raises(BackendError, match="unreachable"):
            self._adapter().search(StructuredFilter(), 0, 10, 5)
        assert len(calls) == 1

    def test_like_patterns_escape_underscore_and_percent(self):
        where = self._adapter()._build_where(StructuredFilter(name_regex="tool_x%"))
        assert "operation_name LIKE '%tool\\_x\\%%'" in where

    def test_trace_detail_window_and_not_found(self, monkeypatch):
        seen = {}

        def fake(url, body, headers=None, timeout=None):
            seen["body"] = body
            return {"hits": []}

        monkeypatch.setattr(openobserve, "http_post_json", fake)
        a = self._adapter()
        with pytest.raises(BackendError) as exc:
            a.get_trace("abc")
        assert exc.value.kind == "not_found"
        q = seen["body"]["query"]
        assert q["end_time"] - q["start_time"] >= 90 * 86400 * 1_000_000

    def test_metric_samples_keep_the_newest_and_say_when_cut(self, monkeypatch):
        rows = [
            {"_timestamp": 1_700_000_000_000_000 + i * 1_000_000, "value": i, "__name__": "m"}
            for i in range(3)
        ]
        with patch.object(openobserve.OpenObserveAdapter, "_search", return_value=rows) as m:
            out = self._adapter().metrics_query("m", 1_700_000_000, 1_700_000_060, 60)
        assert "ORDER BY _timestamp DESC" in m.call_args[0][0]
        assert out["truncated"] is False and out["cumulative"] is False
        assert sum(v for v in out["series"]["_"] if v) == 3.0

    def test_metric_names_label_the_lifetime_count(self, monkeypatch):
        monkeypatch.setattr(
            openobserve,
            "http_get_json",
            lambda *a, **k: {"list": [{"name": "hermes_token_usage", "stats": {"doc_num": 9}}]},
        )
        assert self._adapter().metric_names(0, 1) == [
            {"name": "hermes_token_usage", "count": 9, "count_scope": "stream"}
        ]


class TestJaeger:
    def _adapter(self):
        return jaeger.JaegerAdapter({"type": "jaeger", "endpoint": "http://localhost:16686"})

    @staticmethod
    def _trace(tid, root_tags, child_tags, root_name="agent", start=1_000_000):
        def tag(k, v):
            return {"key": k, "type": "string", "value": v}

        return {
            "traceID": tid,
            "spans": [
                {
                    "traceID": tid,
                    "spanID": "root",
                    "operationName": root_name,
                    "startTime": start,
                    "duration": 5000,
                    "references": [],
                    "tags": [tag(k, v) for k, v in root_tags.items()],
                    "processID": "p1",
                },
                {
                    "traceID": tid,
                    "spanID": "child",
                    "operationName": "tool.terminal",
                    "startTime": start + 10,
                    "duration": 100,
                    "references": [{"refType": "CHILD_OF", "traceID": tid, "spanID": "root"}],
                    "tags": [tag(k, v) for k, v in child_tags.items()],
                    "processID": "p1",
                },
            ],
            "processes": {"p1": {"serviceName": "hermes-agent", "tags": []}},
        }

    def test_roots_only_requires_the_root_itself_to_match(self, monkeypatch):
        data = {
            "data": [
                self._trace("t-root-has-it", {"tool.name": "terminal"}, {}),
                self._trace("t-only-child-has-it", {}, {"tool.name": "terminal"}),
            ]
        }
        monkeypatch.setattr(jaeger, "http_get_json", lambda *a, **k: data)
        f = StructuredFilter(attr_equals={"tool.name": "terminal"}, roots_only=True)
        out = self._adapter().search(f, 0, 10, 10)
        assert [t["traceID"] for t in out["traces"]] == ["t-root-has-it"]
        widened = self._adapter().search(
            StructuredFilter(attr_equals={"tool.name": "terminal"}, roots_only=False), 0, 10, 10
        )
        assert len(widened["traces"]) == 2

    def test_widened_search_cards_are_the_matched_spans(self, monkeypatch):
        data = {
            "data": [
                {
                    "traceID": "t1",
                    "spans": [
                        {
                            "spanID": "r",
                            "operationName": "agent",
                            "startTime": 1_000_000,
                            "duration": 9_000_000,
                            "tags": [],
                            "references": [],
                        },
                        {
                            "spanID": "c",
                            "operationName": "tool.terminal",
                            "startTime": 2_000_000,
                            "duration": 50_000,
                            "tags": [{"key": "tool.name", "type": "string", "value": "terminal"}],
                            "references": [{"refType": "CHILD_OF", "spanID": "r", "traceID": "t1"}],
                        },
                    ],
                    "processes": {},
                }
            ]
        }
        monkeypatch.setattr(jaeger, "http_get_json", lambda *a, **k: data)
        page = self._adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False), 0, 10, 10
        )
        row = page["traces"][0]
        assert row["rootTraceName"] == "tool.terminal" and row["durationMs"] == 50
        assert row["spanSets"][0]["spans"][0]["name"] == "tool.terminal"
        # roots-only keeps the root as the card
        page = self._adapter().search(StructuredFilter(name_prefix="agent"), 0, 10, 10)
        assert page["traces"][0]["rootTraceName"] == "agent"

    def test_name_prefix_is_checked_on_the_rows_and_cursor_bounds_the_query(self, monkeypatch):
        seen = {}

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {
                "data": [
                    self._trace("cron-trace", {}, {}, root_name="cron", start=3_000_000),
                    self._trace("agent-trace", {}, {}, root_name="agent", start=2_000_000),
                ]
            }

        monkeypatch.setattr(jaeger, "http_get_json", fake)
        out = self._adapter().search(
            StructuredFilter(name_prefix="agent", before_ns=2_500_000_000), 0, 10, 5
        )
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert (q["limit"], q["end"], q["service"]) == ("6", "2500000", "hermes-agent")
        assert [t["traceID"] for t in out["traces"]] == ["agent-trace"]

    def test_status_comes_from_the_otel_tags_and_bad_numbers_do_not_crash(self, monkeypatch):
        t = self._trace("t", {"otel.status_code": "ERROR", "otel.status_description": "boom"}, {})
        t["spans"][0]["startTime"] = "not-a-number"
        monkeypatch.setattr(jaeger, "http_get_json", lambda *a, **k: {"data": [t]})
        detail = self._adapter().get_trace("t")
        root = detail["batches"][0]["scopeSpans"][0]["spans"][0]
        assert root["status"] == {"code": 2, "message": "boom"}
        assert root["startTimeUnixNano"] == "0" and detail["span_count"] == 2
        card = self._adapter().search(StructuredFilter(), 0, 10, 5)["traces"][0]
        attrs = {a["key"]: a["value"] for a in card["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["status"] == {"stringValue": "error"}

    # ── Jaeger v2: the v3 query API (#245) ──────────────────────────
    @staticmethod
    def _v3_result(*traces):
        """OTLP JSON as ``/api/v3/traces`` answers it: one resourceSpans per
        trace, hex ids, ``status.code`` 1/2, attributes as AnyValues."""
        rs = []
        for tid, spans in traces:
            rs.append(
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "hermes-agent"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": tid,
                                    "spanId": sid,
                                    "parentSpanId": parent,
                                    "name": name,
                                    "startTimeUnixNano": str(start),
                                    "endTimeUnixNano": str(start + dur),
                                    "attributes": [
                                        {"key": k, "value": {"stringValue": v}}
                                        for k, v in attrs.items()
                                    ],
                                    "status": status,
                                }
                                for sid, parent, name, start, dur, attrs, status in spans
                            ]
                        }
                    ],
                }
            )
        return {"result": {"resourceSpans": rs}}

    def _v3_fake(self, monkeypatch, search_result, trace_result=None):
        calls = []

        def fake(url, headers=None, timeout=None):
            calls.append(url)
            if url.endswith("/api/services"):
                # Jaeger v2: the classic routes are 404
                raise BackendError(404, "Backend returned 404: 404 page not found", "not_found")
            if url.endswith("/api/v3/services"):
                return {"services": ["jaeger", "hermes-agent"]}
            if "/api/v3/traces?" in url:
                if isinstance(search_result, Exception):
                    raise search_result
                return search_result
            if "/api/v3/traces/" in url:
                if isinstance(trace_result, Exception):
                    raise trace_result
                return trace_result
            raise AssertionError(f"unexpected v1 call {url}")

        monkeypatch.setattr(jaeger, "http_get_json", fake)
        monkeypatch.setattr(jaeger, "_API_PROBE_CACHE", {})
        return calls

    def test_v3_is_probed_once_and_search_speaks_the_v3_grammar(self, monkeypatch):
        t_root = (
            "a" * 32,
            [
                (
                    "r1",
                    None,
                    "agent",
                    2_000_000_000_000,
                    5_000_000,
                    {"tool.name": "terminal"},
                    {"code": 1},
                ),
                (
                    "c1",
                    "r1",
                    "tool.terminal",
                    2_000_000_000_010,
                    100_000,
                    {"tool.name": "terminal"},
                    {"code": 1},
                ),
            ],
        )
        t_child = (
            "b" * 32,
            [
                (
                    "r2",
                    None,
                    "agent",
                    1_000_000_000_000,
                    5_000_000,
                    {},
                    {"code": 2, "message": "boom"},
                ),
                (
                    "c2",
                    "r2",
                    "tool.terminal",
                    1_000_000_000_010,
                    100_000,
                    {"tool.name": "terminal"},
                    {"code": 1},
                ),
            ],
        )
        calls = self._v3_fake(monkeypatch, self._v3_result(t_root, t_child))
        a = self._adapter()
        out = a.search(StructuredFilter(attr_equals={"tool.name": "terminal"}), 0, 3000, 5)
        assert calls[0].endswith("/api/services") and calls[1].endswith("/api/v3/services")
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(calls[2]).query))
        assert q["query.serviceName"] == "hermes-agent" and q["query.searchDepth"] == "6"
        assert q["query.startTimeMin"] == "1970-01-01T00:00:00.000000000Z"
        assert q["query.startTimeMax"] == "1970-01-01T00:50:00.000000000Z"
        assert json.loads(q["query.attributes"]) == {"tool.name": "terminal"}
        # roots-only re-checks the root: trace b matches only through its child
        assert [t["traceID"] for t in out["traces"]] == ["a" * 32]
        row = out["traces"][0]
        assert (
            row["spanCount"] == 2
            and row["durationMs"] == 5
            and row["rootServiceName"] == "hermes-agent"
        )
        # the probe is cached: a second search makes no services call
        a.search(StructuredFilter(status="error"), 0, 3000, 5)
        assert sum(u.endswith("/api/v3/services") for u in calls) == 1
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(calls[-1]).query))
        assert json.loads(q["query.attributes"]) == {"error": "true"}
        # status comes from the OTLP status on v3 rows
        out = a.search(StructuredFilter(), 0, 3000, 5)
        by_id = {t["traceID"]: t for t in out["traces"]}
        attrs = {
            x["key"]: x["value"] for x in by_id["b" * 32]["spanSets"][0]["spans"][0]["attributes"]
        }
        assert attrs["status"] == {"stringValue": "error"}

    def test_v3_empty_search_is_a_page_and_an_unknown_trace_is_not_found(self, monkeypatch):
        self._v3_fake(
            monkeypatch,
            BackendError(404, "Backend returned 404: No traces found", "not_found"),
            BackendError(404, "Backend returned 404: No traces found", "not_found"),
        )
        a = self._adapter()
        page = a.search(StructuredFilter(), 0, 3000, 5)
        assert page == {"traces": [], "next_before_ns": None, "has_more": False}
        with pytest.raises(BackendError) as exc:
            a.get_trace("f" * 32)
        assert exc.value.kind == "not_found"

    def test_v3_detail_passes_resource_spans_through(self, monkeypatch):
        t = (
            "c" * 32,
            [
                ("r1", None, "agent", 2_000_000_000_000, 5_000_000, {}, {"code": 1}),
                (
                    "c1",
                    "r1",
                    "api.x",
                    2_000_000_000_010,
                    100_000,
                    {"llm.model_name": "m"},
                    {"code": 1},
                ),
            ],
        )
        self._v3_fake(monkeypatch, {}, self._v3_result(t))
        det = self._adapter().get_trace("c" * 32)
        assert det["span_count"] == 2 and det["truncated"] is False
        spans = det["batches"][0]["scopeSpans"][0]["spans"]
        assert spans[1]["parentSpanId"] == "r1" and spans[1]["status"] == {"code": 1}
        assert self._adapter().trace_url("c" * 32) == "http://localhost:16686/trace/" + "c" * 32

    def test_query_api_pin_skips_the_probe_and_v1_stays_v1(self, monkeypatch):
        calls = []

        def fake(url, headers=None, timeout=None):
            calls.append(url)
            if url.endswith("/api/v3/services"):
                # Jaeger 1.x all-in-one serves this too (snake_case params):
                # the classic API must still win when it answers
                return {"services": ["hermes-agent"]}
            return {"data": []}

        monkeypatch.setattr(jaeger, "http_get_json", fake)
        monkeypatch.setattr(jaeger, "_API_PROBE_CACHE", {})
        pinned = jaeger.JaegerAdapter(
            {"type": "jaeger", "endpoint": "http://localhost:16686", "query_api": "v1"}
        )
        pinned.search(StructuredFilter(), 0, 10, 5)
        assert "/api/traces?" in calls[-1] and not any("v3" in u for u in calls)
        # auto on a v1 server: one classic probe answers, no v3 probe, cached
        a = self._adapter()
        a.search(StructuredFilter(), 0, 10, 5)
        a.search(StructuredFilter(), 0, 10, 5)
        assert sum(u.endswith("/api/services") for u in calls) == 1
        assert not any(u.endswith("/api/v3/services") for u in calls)
        assert all("/api/traces?" in u for u in calls if "?" in u)
        assert a.status()["query_api"] == "v1"
        # an unreachable server is reported, never guessed
        monkeypatch.setattr(jaeger, "_API_PROBE_CACHE", {})
        monkeypatch.setattr(
            jaeger,
            "http_get_json",
            lambda *a, **k: (_ for _ in ()).throw(
                BackendError(502, "Backend unreachable", "backend")
            ),
        )
        with pytest.raises(BackendError, match="unreachable"):
            self._adapter().search(StructuredFilter(), 0, 10, 5)

    def test_default_service_follows_the_plugin_resource(self, monkeypatch):
        monkeypatch.setattr(
            backends, "top_level_config", lambda: {"resource_attributes": {"service.name": "bot"}}
        )
        assert self._adapter().default_service == "bot"
        assert (
            jaeger.JaegerAdapter({"type": "jaeger", "service_name": "svc"}).default_service == "svc"
        )


class TestSigNoz:
    def _adapter(self):
        return signoz.SigNozAdapter(
            {"type": "signoz", "endpoint": "http://localhost:3301", "api_key": "k"}
        )

    def test_search_parses_rows_into_cards_and_pages(self, monkeypatch):
        rows = [
            {
                "traceID": "t1",
                "spanID": "s1",
                "name": "agent",
                "serviceName": "hermes-agent",
                "durationNano": 2_500_000_000,
                "timestamp": "2026-10-05T10:00:03Z",
                "hasError": False,
                "llm.model_name": "gpt-4o-mini",
                "gen_ai.usage.total_tokens": 42,
                "hermes.session_id": "s-1",
            },
            {
                "traceID": "t1",
                "spanID": "s2",
                "name": "api.m",
                "serviceName": "hermes-agent",
                "durationNano": 1_000,
                "timestamp": "2026-10-05T10:00:02Z",
            },
            {
                "traceID": "t2",
                "spanID": "s3",
                "name": "agent",
                "serviceName": "hermes-agent",
                "durationNano": 1_000_000,
                "timestamp": "2026-10-05T10:00:01Z",
                "hasError": True,
            },
        ]
        seen = {}

        def fake(url, body, headers=None, timeout=None):
            seen["body"] = body
            return {"data": {"result": [{"list": [{"data": r} for r in rows]}]}}

        monkeypatch.setattr(signoz, "http_post_json", fake)
        out = self._adapter().search(StructuredFilter(name_prefix="agent"), 0, 10, 1)
        q = seen["body"]["compositeQuery"]["builderQueries"]["A"]
        assert q["limit"] == 2  # one beyond the page in roots-only mode
        assert {"key": "llm.model_name", "type": "tag", "dataType": "string"} in q["selectColumns"]
        items = {i["key"]["key"]: i for i in q["filters"]["items"]}
        assert items["name"]["op"] == "regex" and items["name"]["value"] == "^agent"
        assert [t["traceID"] for t in out["traces"]] == ["t1"]
        assert out["has_more"] is True and out["next_before_ns"]
        attrs = {
            a["key"]: a["value"] for a in out["traces"][0]["spanSets"][0]["spans"][0]["attributes"]
        }
        assert attrs["llm.model_name"] == {"stringValue": "gpt-4o-mini"}
        assert attrs["gen_ai.usage.total_tokens"] == {"intValue": "42"}
        assert attrs["hermes.session_id"] == {"stringValue": "s-1"}
        assert out["traces"][0]["durationMs"] == 2500

    def test_status_ok_is_sent_and_rows_limit_widens_without_roots(self):
        a = self._adapter()
        body = a._build_query_body(StructuredFilter(status="ok", roots_only=False), 0, 1, 10)
        q = body["compositeQuery"]["builderQueries"]["A"]
        items = {i["key"]["key"]: i for i in q["filters"]["items"]}
        assert items["hasError"]["value"] is False and q["limit"] == 41

    def test_get_trace_does_not_retry_unreachable_and_finds_nothing(self, monkeypatch):
        calls = []

        # GET /api/v1/traces/{id} is the span list on every build seen (v0.119
        # answers the SPA page to the POST, #297); an unreachable GET is final.
        def fake_get(url, headers=None, timeout=None):
            calls.append("get")
            raise BackendError(502, "Backend unreachable: refused")

        monkeypatch.setattr(signoz, "http_get_json", fake_get)
        monkeypatch.setattr(signoz, "http_post_json", lambda *a, **k: calls.append("post"))
        with pytest.raises(BackendError, match="unreachable"):
            self._adapter().get_trace("t")
        assert calls == ["get"]

        # A GET refused as a request falls back to the POST form once.
        calls.clear()

        def rejected(url, headers=None, timeout=None):
            calls.append("get")
            raise BackendError(502, "Backend returned 405: method not allowed")

        monkeypatch.setattr(signoz, "http_get_json", rejected)
        monkeypatch.setattr(
            signoz, "http_post_json", lambda *a, **k: (calls.append("post"), {"spans": []})[1]
        )
        with pytest.raises(BackendError) as exc:
            self._adapter().get_trace("t")
        assert exc.value.kind == "not_found" and calls == ["get", "post"]

    def test_catalog_is_fetched_once_per_instance_and_counters_are_cumulative(self, monkeypatch):
        gets = []
        catalog = {
            "data": {"attributeKeys": [{"key": "hermes.token.usage", "type": "Sum"}]},
        }

        def fake_get(url, headers=None, timeout=None):
            gets.append(url)
            return catalog

        monkeypatch.setattr(signoz, "http_get_json", fake_get)
        monkeypatch.setattr(
            signoz, "http_post_json", lambda *a, **k: {"data": {"result": [{"series": []}]}}
        )
        a = self._adapter()
        a.metric_names(0, 1)
        out = a.metrics_query("hermes.token.usage", 0, 60, 60)
        a.metrics_query("hermes.token.usage", 0, 60, 60)
        # one autocomplete search per plugin namespace, then cached
        assert len(gets) == len(signoz._CATALOG_NAMESPACES) and out["cumulative"] is True

    def test_status_message_survives_the_row_shape(self):
        cols = ["__time", "SpanId", "TraceId", "ServiceName", "Name", "HasError", "StatusMessage"]
        otlp = signoz._signoz_trace_to_otlp(
            [{"columns": cols, "events": [[1790463141232, "s", "t", "svc", "agent", True, "boom"]]}]
        )
        span = otlp["batches"][0]["scopeSpans"][0]["spans"][0]
        assert span["status"] == {"code": 2, "message": "boom"}


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


class TestPhoenix:
    def _adapter(self):
        return phoenix.PhoenixAdapter({"type": "phoenix", "endpoint": "http://localhost:6006"})

    def test_filter_condition_uses_the_documented_fields(self):
        fc = self._adapter()._build_filter_condition(
            StructuredFilter(name_prefix="tool.", status="error", min_duration_ms=10, raw="x == 1")
        )
        assert fc == "(x == 1) and 'tool.' in name and latency_ms >= 10 and status_code == 'ERROR'"

    def test_search_enforces_the_prefix_and_pages(self, monkeypatch):
        a = self._adapter()
        a._project_id_cache = "P1"
        seen = {}

        def node(name, tid, start):
            return {
                "spanId": tid,
                "name": name,
                "latencyMs": 1,
                "startTime": start,
                "parentId": None,
                "attributes": "{}",
                "context": {"traceId": tid, "spanId": tid},
                "trace": {"numSpans": 2},
            }

        def fake(url, body, headers=None, timeout=None):
            seen["vars"] = body.get("variables")
            spans = [
                node("tool.x", "t1", "2026-10-05T10:00:03+00:00"),
                node("agent", "t2", "2026-10-05T10:00:02+00:00"),
                node("tool.y", "t3", "2026-10-05T10:00:01+00:00"),
            ]
            return {
                "data": {"node": {"name": "p", "spans": {"edges": [{"node": s} for s in spans]}}}
            }

        monkeypatch.setattr(phoenix, "http_post_json", fake)
        out = a.search(StructuredFilter(name_prefix="tool."), 0, 1_800_000_000, 1)
        assert seen["vars"]["first"] == 2 and "'tool.' in name" in seen["vars"]["filterCondition"]
        assert [t["traceID"] for t in out["traces"]] == ["t1"] and out["has_more"] is True

    # ── Phoenix 20: ``rootSpansOnly`` is gone, roots are a filter predicate ──
    @staticmethod
    def _gql_fake(monkeypatch, spans_args, spans):
        """A Phoenix that answers the project list, the schema introspection
        (``spans_args``: the argument names of ``Project.spans``) and the
        span search (``spans``: node dicts)."""
        seen = {"bodies": []}

        def fake(url, body, headers=None, timeout=None):
            seen["bodies"].append(body)
            q = body.get("query", "")
            if "projects(" in q:
                return {
                    "data": {
                        "projects": {
                            "edges": [
                                {"node": {"id": "P1", "name": "hermes-minimal", "hasTraces": True}}
                            ]
                        }
                    }
                }
            if "__type" in q:
                return {
                    "data": {
                        "__type": {
                            "fields": [{"name": "spans", "args": [{"name": a} for a in spans_args]}]
                        }
                    }
                }
            return {
                "data": {
                    "node": {
                        "name": "hermes-minimal",
                        "spans": {"edges": [{"node": sp} for sp in spans]},
                    }
                }
            }

        monkeypatch.setattr(phoenix, "http_post_json", fake)
        monkeypatch.setattr(phoenix, "_ROOT_ARGS_CACHE", {})
        return seen

    @staticmethod
    def _node(tid, sid, name, start_ms, parent=None):
        return {
            "spanId": sid,
            "name": name,
            "latencyMs": 5,
            "statusCode": "OK",
            "startTime": f"2026-10-09T20:18:{start_ms:02d}.000+00:00",
            "endTime": f"2026-10-09T20:18:{start_ms + 1:02d}.000+00:00",
            "parentId": parent,
            "attributes": "{}",
            "context": {"traceId": tid, "spanId": sid},
            "trace": {"numSpans": 3},
        }

    def test_without_root_args_roots_become_a_parent_id_predicate(self, monkeypatch):
        seen = self._gql_fake(
            monkeypatch,
            ["first", "timeRange", "filterCondition", "sort", "traceFilterCondition"],
            [self._node("t1", "r1", "agent", 10)],
        )
        a = phoenix.PhoenixAdapter(
            {
                "type": "phoenix",
                "endpoint": "http://localhost:6006",
                "project_name": "hermes-minimal",
            }
        )
        out = a.search(StructuredFilter(status="error"), 0, 10, 5)
        search = seen["bodies"][-1]
        assert (
            "rootSpansOnly" not in search["query"] and "orphanSpanAsRootSpan" not in search["query"]
        )
        assert "rootsOnly" not in search["variables"]
        assert (
            search["variables"]["filterCondition"] == "parent_id is None and status_code == 'ERROR'"
        )
        assert [t["traceID"] for t in out["traces"]] == ["t1"]
        # a widened search has no predicate of ours and the schema is not probed again
        a.search(StructuredFilter(name_prefix="tool.", roots_only=False), 0, 10, 5)
        assert seen["bodies"][-1]["variables"]["filterCondition"] == "'tool.' in name"
        assert sum("__type" in b.get("query", "") for b in seen["bodies"]) == 1

    def test_with_root_args_the_legacy_variables_are_sent(self, monkeypatch):
        seen = self._gql_fake(
            monkeypatch,
            ["first", "rootSpansOnly", "orphanSpanAsRootSpan", "filterCondition", "sort"],
            [self._node("t1", "r1", "agent", 10)],
        )
        a = phoenix.PhoenixAdapter(
            {
                "type": "phoenix",
                "endpoint": "http://localhost:6006",
                "project_name": "hermes-minimal",
            }
        )
        a.search(StructuredFilter(status="error"), 0, 10, 5)
        search = seen["bodies"][-1]
        assert "rootSpansOnly: $rootsOnly" in search["query"]
        assert (
            search["variables"]["rootsOnly"] is True
            and search["variables"]["orphanAsRoot"] is False
        )
        assert search["variables"]["filterCondition"] == "status_code == 'ERROR'"

    def test_widened_search_is_one_card_per_trace_and_says_more_when_full(self, monkeypatch):
        # two tool spans of one trace, newest first, plus one of another
        spans = [
            self._node("t1", "s3", "tool.terminal", 30, parent="r1"),
            self._node("t1", "s2", "tool.terminal", 20, parent="r1"),
            self._node("t2", "s9", "tool.terminal", 15, parent="r2"),
            self._node("t1", "s1", "tool.terminal", 10, parent="r1"),
            self._node("t3", "s8", "tool.terminal", 5, parent="r3"),
        ]
        seen = self._gql_fake(monkeypatch, ["first", "filterCondition", "sort"], spans)
        a = phoenix.PhoenixAdapter(
            {
                "type": "phoenix",
                "endpoint": "http://localhost:6006",
                "project_name": "hermes-minimal",
            }
        )
        out = a.search(StructuredFilter(name_prefix="tool.", roots_only=False), 0, 100, 1)
        assert seen["bodies"][-1]["variables"]["first"] == 5  # 1 * 4 + 1
        assert [t["traceID"] for t in out["traces"]] == ["t1"]
        assert out["traces"][0]["spanSets"][0]["spans"][0]["name"] == "tool.terminal"
        assert out["has_more"] is True
        # the cursor sits below t1's OLDEST fetched span (s1 at :10), not the shown one (:30)
        assert out["next_before_ns"] == phoenix._iso_to_ns("2026-10-09T20:18:10.000+00:00")

    def test_detail_reports_truncation_and_the_whole_count(self, monkeypatch):
        a = self._adapter()
        a._project_id_cache = "P1"

        def fake(url, body, headers=None, timeout=None):
            assert body["variables"]["first"] == phoenix._DETAIL_SPAN_CAP
            return {
                "data": {
                    "node": {
                        "name": "p",
                        "trace": {
                            "numSpans": 900,
                            "spans": {
                                "pageInfo": {"hasNextPage": True},
                                "edges": [
                                    {
                                        "node": {
                                            "spanId": "s",
                                            "name": "agent",
                                            "attributes": "{}",
                                            "context": {"traceId": "t", "spanId": "s"},
                                        }
                                    }
                                ],
                            },
                        },
                    }
                }
            }

        monkeypatch.setattr(phoenix, "http_post_json", fake)
        out = a.get_trace("t")
        assert out["truncated"] is True and out["span_count"] == 900


class TestLangfuse:
    def _adapter(self):
        return langfuse.LangfuseAdapter(
            {
                "type": "langfuse",
                "endpoint": "http://localhost:3000",
                "public_key": "pk",
                "secret_key": "sk",
            }
        )

    def test_card_reads_the_root_attributes_from_the_list_metadata(self, monkeypatch):
        item = {
            "id": "t1",
            "name": "agent",
            "timestamp": "2026-10-07T01:13:00.607Z",
            "latency": 11.052,
            "totalCost": 0,
            "observations": ["a", "b"],
            "input": "Run echo",
            "output": "ok",
            "metadata": {
                "attributes": {
                    "llm.model_name": "nvidia/nemotron-3-super-120b-a12b",
                    "gen_ai.usage.total_tokens": "29149",
                    "hermes.session_id": "s1",
                }
            },
        }
        monkeypatch.setattr(langfuse, "http_get_json", lambda *a, **k: {"data": [item]})
        page = self._adapter().search(StructuredFilter(), 0, 10, 5)
        attrs = {
            a["key"]: a["value"] for a in page["traces"][0]["spanSets"][0]["spans"][0]["attributes"]
        }
        assert attrs["llm.model_name"] == {"stringValue": "nvidia/nemotron-3-super-120b-a12b"}
        assert attrs["gen_ai.usage.total_tokens"] == {"stringValue": "29149"}
        assert attrs["hermes.session_id"] == {"stringValue": "s1"}
        assert attrs["input.value"] == {"stringValue": "Run echo"}

    def test_detail_restores_the_tool_prefix_langfuse_drops(self, monkeypatch):
        data = {
            "id": "t1",
            "name": "agent",
            "timestamp": "2026-10-07T01:13:00.607Z",
            "observations": [
                {
                    "id": "r",
                    "type": "AGENT",
                    "name": "agent",
                    "startTime": "2026-10-07T01:13:00.607Z",
                    "endTime": "2026-10-07T01:13:11.000Z",
                },
                {
                    "id": "c",
                    "type": "TOOL",
                    "name": "terminal",
                    "parentObservationId": "r",
                    "startTime": "2026-10-07T01:13:04.411Z",
                    "endTime": "2026-10-07T01:13:05.696Z",
                },
            ],
        }
        monkeypatch.setattr(langfuse, "http_get_json", lambda *a, **k: data)
        detail = self._adapter().get_trace("t1")
        names = [
            sp["name"] for b in detail["batches"] for ss in b["scopeSpans"] for sp in ss["spans"]
        ]
        assert names == ["agent", "tool.terminal"]

    # ── Langfuse v4 (events_only): v2 observations + v2 metrics (#246) ──
    @staticmethod
    def _v4_obs(tid, oid, typ, name, start, latency_s, parent=None, root=False, level="DEFAULT"):
        return {
            "id": oid,
            "traceId": tid,
            "parentObservationId": parent,
            "isRootObservation": root,
            "type": typ,
            "name": name,
            "startTime": start,
            "endTime": start,
            "latency": latency_s,
            "level": level,
            "statusMessage": None,
            "sessionId": "sess-1",
            "environment": "default",
        }

    def _v4_fake(self, monkeypatch, observations, metrics_rows):
        seen = []

        def fake(url, headers=None, timeout=None):
            seen.append(url)
            if "/api/public/traces" in url:
                raise BackendError(
                    404,
                    "Backend returned 404: This endpoint is not available on deployments running in Langfuse v4 events_only mode.",
                    "not_found",
                )
            if "/api/public/v2/metrics" in url:
                return {"data": metrics_rows}
            if "/api/public/v2/observations" in url:
                q = dict(_urlparse.parse_qsl(_urlparse.urlparse(url).query))
                rows = observations
                if q.get("traceId"):
                    rows = [o for o in rows if o["traceId"] == q["traceId"]]
                if q.get("type"):
                    rows = [o for o in rows if o["type"] == q["type"]]
                if q.get("sessionId"):
                    rows = [o for o in rows if o["sessionId"] == q["sessionId"]]
                if q.get("level"):
                    rows = [o for o in rows if o["level"] == q["level"]]
                return {"data": rows, "meta": {}}
            if "/api/public/projects" in url:
                return {"data": [{"id": "test-project"}]}
            raise AssertionError(url)

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        monkeypatch.setattr(langfuse, "_API_CACHE", {})
        return seen

    def _v4_adapter(self):
        return langfuse.LangfuseAdapter(
            {
                "type": "langfuse",
                "endpoint": "http://localhost:3002",
                "public_key": "pk",
                "secret_key": "sk",
            }
        )

    def test_v4_is_detected_once_and_cards_join_the_metrics(self, monkeypatch):
        obs = [
            self._v4_obs(
                "t1", "r1", "AGENT", "agent", "2026-10-09T20:53:10.000Z", 19.094, root=True
            ),
            self._v4_obs(
                "t1", "g1", "GENERATION", "api.m", "2026-10-09T20:53:11.000Z", 1.1, parent="r1"
            ),
            self._v4_obs(
                "t1", "x1", "TOOL", "terminal", "2026-10-09T20:53:12.000Z", 1.0, parent="r1"
            ),
            self._v4_obs("t2", "r2", "AGENT", "agent", "2026-10-09T20:52:00.000Z", 2.0, root=True),
            self._v4_obs(
                "t2", "a2", "AGENT", "sub-agent", "2026-10-09T20:52:01.000Z", 1.0, parent="r2"
            ),
        ]
        metrics = [
            {
                "traceId": "t1",
                "providedModelName": "nvidia/m",
                "sum_totalTokens": 14183,
                "sum_inputTokens": 13985,
                "sum_outputTokens": 198,
                "sum_totalCost": 0,
                "count_count": 3,
            },
            {
                "traceId": "t1",
                "providedModelName": None,
                "sum_totalTokens": 0,
                "sum_inputTokens": 0,
                "sum_outputTokens": 0,
                "sum_totalCost": 0,
                "count_count": 2,
            },
            {
                "traceId": "t2",
                "providedModelName": None,
                "sum_totalTokens": 0,
                "sum_inputTokens": 0,
                "sum_outputTokens": 0,
                "sum_totalCost": 0.5,
                "count_count": 2,
            },
        ]
        seen = self._v4_fake(monkeypatch, obs, metrics)
        a = self._v4_adapter()
        out = a.search(StructuredFilter(), 1_790_000_000, 1_790_500_000, 10)
        assert sum("/api/public/traces" in u for u in seen) == 1  # probed once
        assert a.status()["query_api"] == "v4"
        rows = {t["traceID"]: t for t in out["traces"]}
        assert list(rows) == ["t1", "t2"]  # newest first, the nested AGENT is not a turn
        t1 = rows["t1"]
        attrs = {
            x["key"]: list(x["value"].values())[0]
            for x in t1["spanSets"][0]["spans"][0]["attributes"]
        }
        assert t1["durationMs"] == 19094 and t1["spanCount"] == 5
        assert (
            attrs["gen_ai.usage.total_tokens"] == "14183" and attrs["llm.model_name"] == "nvidia/m"
        )
        assert attrs["hermes.session_id"] == "sess-1"
        t2_attrs = {
            x["key"]: list(x["value"].values())[0]
            for x in rows["t2"]["spanSets"][0]["spans"][0]["attributes"]
        }
        assert "gen_ai.usage.total_tokens" not in t2_attrs and t2_attrs["hermes.cost.usage"] == 0.5
        # the list sent the root type and the window
        q = dict(
            _urlparse.parse_qsl(
                _urlparse.urlparse([u for u in seen if "v2/observations" in u][0]).query
            )
        )
        assert q["type"] == "AGENT" and q["fromStartTime"].endswith("Z") and q["limit"] == "100"
        # per-request filter support: session is server-side on v4, model never
        from hermes_otel.dashboard.backends.base import split_applied_filters

        applied, ignored = split_applied_filters(
            a, StructuredFilter(attr_equals={"hermes.session_id": "sess-1", "llm.model_name": "x"})
        )
        assert applied == ["session", "roots_only"] and ignored == ["model"]
        # a second search makes no probe
        a.search(StructuredFilter(attr_equals={"hermes.session_id": "sess-1"}), 0, 10, 5)
        assert sum("/api/public/traces" in u for u in seen) == 1
        assert "sessionId=sess-1" in seen[-2] or "sessionId=sess-1" in seen[-1]

    def test_v4_kind_filter_lists_the_typed_observations_one_card_per_trace(self, monkeypatch):
        obs = [
            self._v4_obs(
                "t1", "x2", "TOOL", "terminal", "2026-10-09T20:53:13.000Z", 1.0, parent="r1"
            ),
            self._v4_obs(
                "t1", "x1", "TOOL", "terminal", "2026-10-09T20:53:12.000Z", 1.0, parent="r1"
            ),
            self._v4_obs(
                "t3", "x9", "TOOL", "read_file", "2026-10-09T20:50:00.000Z", 1.0, parent="r3"
            ),
        ]
        self._v4_fake(monkeypatch, obs, [])
        out = self._v4_adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False),
            1_790_000_000,
            1_790_500_000,
            10,
        )
        assert [(t["traceID"], t["rootTraceName"]) for t in out["traces"]] == [
            ("t1", "tool.terminal"),
            ("t3", "tool.read_file"),
        ]

    def test_v4_detail_keeps_the_real_root_and_carries_the_totals(self, monkeypatch):
        obs = [
            self._v4_obs("t1", "r1", "AGENT", "agent", "2026-10-09T20:53:10.000Z", 19.0, root=True),
            self._v4_obs(
                "t1", "g1", "GENERATION", "api.m", "2026-10-09T20:53:11.000Z", 1.1, parent="r1"
            ),
            self._v4_obs(
                "t1",
                "x1",
                "TOOL",
                "terminal",
                "2026-10-09T20:53:12.000Z",
                1.0,
                parent="r1",
                level="ERROR",
            ),
        ]
        metrics = [
            {
                "traceId": "t1",
                "providedModelName": "nvidia/m",
                "sum_totalTokens": 100,
                "sum_inputTokens": 90,
                "sum_outputTokens": 10,
                "sum_totalCost": 0,
                "count_count": 3,
            }
        ]
        self._v4_fake(monkeypatch, obs, metrics)
        det = self._v4_adapter().get_trace("t1")
        spans = det["batches"][0]["scopeSpans"][0]["spans"]
        assert det["span_count"] == 3 and [sp["name"] for sp in spans] == [
            "agent",
            "api.m",
            "tool.terminal",
        ]
        root = spans[0]
        assert root["parentSpanId"] is None and not any(
            a["key"] == "synthetic" for a in root["attributes"]
        )
        root_attrs = {a["key"]: list(a["value"].values())[0] for a in root["attributes"]}
        assert (
            root_attrs["gen_ai.usage.total_tokens"] == "100"
            and root_attrs["llm.model_name"] == "nvidia/m"
        )
        assert spans[2]["status"]["code"] == 2 and spans[1]["parentSpanId"] == "r1"
        with pytest.raises(BackendError) as exc:
            self._v4_adapter().get_trace("missing")
        assert exc.value.kind == "not_found"

    def test_query_api_pin_skips_the_probe(self, monkeypatch):
        calls = []

        def fake(url, headers=None, timeout=None):
            calls.append(url)
            return {"data": []}

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        monkeypatch.setattr(langfuse, "_API_CACHE", {})
        a = langfuse.LangfuseAdapter(
            {
                "type": "langfuse",
                "endpoint": "http://localhost:3002",
                "public_key": "pk",
                "secret_key": "sk",
                "query_api": "v4",
            }
        )
        a.search(StructuredFilter(), 0, 10, 5)
        assert all(
            "/api/public/v2/observations" in u or "/api/public/v2/metrics" in u for u in calls
        )
        assert a.status()["query_api"] == "v4"

    def test_page_size_is_capped_at_the_api_maximum(self, monkeypatch):
        urls = []

        def fake(url, headers=None, timeout=None):
            urls.append(url)
            return {"data": []}

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        self._adapter().search(StructuredFilter(), 0, 10, 200)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(urls[0]).query))
        assert q["limit"] == "100"

    def test_kind_filter_widens_to_typed_observations(self, monkeypatch):
        urls = []
        obs = {
            "data": [
                {
                    "id": "o1",
                    "traceId": "t1",
                    "type": "TOOL",
                    "name": "terminal",
                    "startTime": "2026-10-07T01:13:07.640Z",
                    "latency": 0.054,
                    "input": {"command": "echo hi"},
                    "output": "hi",
                    "metadata": {"attributes": {"tool.name": "terminal"}},
                },
                {
                    "id": "o2",
                    "traceId": "t1",
                    "type": "TOOL",
                    "name": "terminal",
                    "startTime": "2026-10-07T01:13:06.879Z",
                    "latency": 0.03,
                },
            ]
        }

        def fake(url, headers=None, timeout=None):
            urls.append(url)
            return obs

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        page = self._adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False), 1791335492, 1791335683, 5
        )
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(urls[0]).query))
        assert "/api/public/observations?" in urls[0] and q["type"] == "TOOL"
        assert (q["fromStartTime"], q["toStartTime"]) == (
            "2026-10-07T01:11:32Z",
            "2026-10-07T01:14:43Z",
        )
        # one card per trace, named like the exporter's span, with the tool's I/O
        assert len(page["traces"]) == 1
        row = page["traces"][0]
        assert row["rootTraceName"] == "tool.terminal" and row["durationMs"] == 54
        attrs = {a["key"]: a["value"] for a in row["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["tool.name"] == {"stringValue": "terminal"}
        assert attrs["input.value"] == {"stringValue": '{"command": "echo hi"}'}

    def test_raw_page_and_limit_cannot_override_the_route(self):
        p = self._adapter()._list_params(
            StructuredFilter(raw="page=9 limit=5000 userId=u"), 0, 10, 7
        )
        assert (p["page"], p["limit"], p["userId"]) == (1, 7, "u")

    def test_duration_filter_walks_pages_and_latency_is_seconds(self, monkeypatch):
        pages = []

        def fake(url, headers=None, timeout=None):
            q = dict(_urlparse.parse_qsl(_urlparse.urlparse(url).query))
            pages.append(q["page"])
            page = int(q["page"])
            items = [
                {
                    "id": f"p{page}-{i}",
                    "name": "agent",
                    "timestamp": f"2026-10-05T10:0{page}:0{i}.000Z",
                    "latency": 0.5 if i else 3.0,  # seconds: one slow trace per page
                }
                for i in range(3)
            ]
            return {"data": items}

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        out = self._adapter().search(StructuredFilter(min_duration_ms=1000), 0, 10, 2)
        assert pages == ["1", "2", "3"]
        assert [t["durationMs"] for t in out["traces"]] == [3000, 3000]
        assert out["has_more"] is True

    def test_a_wrong_key_on_the_projects_call_is_an_auth_error(self, monkeypatch):
        def fake(url, headers=None, timeout=None):
            raise BackendError(503, "Backend rejected the credentials (401)", "auth")

        monkeypatch.setattr(langfuse, "http_get_json", fake)
        with pytest.raises(BackendError) as exc:
            self._adapter().trace_url("t")
        assert exc.value.kind == "auth"
