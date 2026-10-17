"""The adapter contract of milestone 8 (#290, #292–#299): declared filter
support, cursor paging, the error taxonomy, the caches, and the response
parsing of the adapters that only had request-shape tests."""

from __future__ import annotations

import io
import os
from pathlib import Path
from unittest.mock import patch
from urllib import error as _urlerror

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from helpers.dashboard import (  # noqa: F401
    CONTRACT_CFG as CFG,
)
from helpers.dashboard import (
    NS,
)
from helpers.dashboard import (
    down as _down,
)
from helpers.dashboard import (
    http_error as _http_error,
)
from helpers.dashboard import (
    row as _row,
)

from hermes_otel.dashboard import backends, plugin_api, route_helpers
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
    split_applied_filters,
)
from hermes_otel.dashboard.backends.metrics_math import otlp_metric_name
from hermes_otel.dashboard.backends.paging import strictly_older_traces, trace_page

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

    def test_effective_filter_support_is_the_declaration_plus_the_instance_override(self):
        class Narrow(base.BackendAdapter):
            handles = ("narrow",)
            filter_support = {k: "server" for k in FILTER_KEYS}

            def filter_support_for(self, f):
                # a raw query replaces the predicates (#296); a bogus level is ignored
                return {"model": "none", "session": "bogus"} if f.raw else {}

        support = base.effective_filter_support(Narrow({"type": "narrow"}), StructuredFilter())
        assert support == {k: "server" for k in FILTER_KEYS}
        narrowed = base.effective_filter_support(
            Narrow({"type": "narrow"}), StructuredFilter(raw="x")
        )
        assert narrowed["model"] == "none" and narrowed["session"] == "server"
        # a class declares only
        assert base.effective_filter_support(Narrow, StructuredFilter(raw="x"))["model"] == "server"
        assert Narrow({"type": "narrow"}).status()["filters"] == support


class TestFirstAccepted:
    """One rule for "the backend refused this request shape, try the next one"
    (Tempo's ``select()``, SigNoz's GET, OpenObserve's parent column)."""

    @staticmethod
    def _refused(status: int):
        def attempt():
            raise BackendError(502, f"refused {status}", "backend", http_status=status)

        return attempt

    def test_the_first_accepted_answer_wins(self):
        seen = []

        def ok():
            seen.append("ok")
            return {"hits": 1}

        assert base.first_accepted(self._refused(400), ok, self._refused(422)) == {"hits": 1}
        assert seen == ["ok"]  # later spellings are never sent

    def test_every_spelling_refused_raises_the_last_refusal(self):
        with pytest.raises(BackendError) as e:
            base.first_accepted(self._refused(400), self._refused(422))
        assert e.value.http_status == 422

    def test_anything_but_a_refusal_is_raised_at_once(self):
        never = []
        for exc in (
            BackendError(502, "Backend unreachable"),  # no http status
            BackendError(502, "boom", "backend", http_status=500),
            BackendError(503, "key", "auth", http_status=401),
            BackendError(404, "gone", "not_found", http_status=404),
        ):

            def first(exc=exc):
                raise exc

            with pytest.raises(BackendError) as e:
                base.first_accepted(first, lambda: never.append(1))
            assert e.value is exc
        assert never == []

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
        monkeypatch.setenv("HERMES_OTEL_CONFIG", str(cfg))
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
        monkeypatch.setenv("HERMES_OTEL_CONFIG", str(cfg))
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


class TestApiLeftovers:
    def test_epoch_window_start_is_a_value_not_unset(self):
        start_ns, end_ns = route_helpers.window_ns(1.0, 0, 100)
        assert (start_ns, end_ns) == (0, 100 * 10**9)
        assert route_helpers.window_s(1.0, 0, 100) == (0, 100)
        with pytest.raises(Exception, match="start_s must not be after end_s"):
            route_helpers.window_s(1.0, 200, 100)

    def test_query_host_override_rewrites_every_host(self, monkeypatch):
        from hermes_otel.dashboard.backends.base import rewrite_host_for_docker

        monkeypatch.delenv("HERMES_OTEL_QUERY_HOST", raising=False)
        monkeypatch.setattr("hermes_otel.dashboard.backends.base._IN_DOCKER", False)
        assert rewrite_host_for_docker("localhost") == "localhost"
        monkeypatch.setenv("HERMES_OTEL_QUERY_HOST", "10.0.0.5")
        assert rewrite_host_for_docker("localhost") == "10.0.0.5"
        assert rewrite_host_for_docker("phoenix.lan") == "10.0.0.5"

    def test_stale_live_store_readers_are_evicted(self, tmp_path, monkeypatch):
        from hermes_otel import live_store as ls

        monkeypatch.setattr(ls, "_LIVE_STORE", None)
        monkeypatch.setattr(ls, "_STORES_BY_PATH", {})
        monkeypatch.setattr(ls, "MAX_READERS", 2)
        paths = [str(tmp_path / f"p{i}" / "hermes_otel_live.db") for i in range(4)]
        for p_ in paths:
            Path(p_).parent.mkdir()
        stores = []
        for p_ in paths[:3]:
            monkeypatch.setenv("HERMES_OTEL_LIVE_DB", p_)
            stores.append(ls.get_live_store_for_home(create=True))
        # three opened, the cap is two beyond the current: the oldest went
        assert paths[0] not in ls._STORES_BY_PATH and paths[2] in ls._STORES_BY_PATH
        # a file that disappeared is dropped on the next call for another path
        for name in ("hermes_otel_live.db", "hermes_otel_live.db-wal", "hermes_otel_live.db-shm"):
            f = Path(paths[1]).parent / name
            if f.exists():
                f.unlink()
        monkeypatch.setenv("HERMES_OTEL_LIVE_DB", paths[3])
        ls.get_live_store_for_home(create=True)
        assert paths[1] not in ls._STORES_BY_PATH and paths[3] in ls._STORES_BY_PATH

    def test_status_reports_the_resolved_phoenix_project_after_a_search(self, client, monkeypatch):
        def fake(url, body, headers=None, timeout=None):
            q = body.get("query", "")
            if "projects(" in q:
                return {
                    "data": {
                        "projects": {
                            "edges": [
                                {"node": {"id": "P0", "name": "default", "hasTraces": False}},
                                {"node": {"id": "P1", "name": "hermes-minimal", "hasTraces": True}},
                            ]
                        }
                    }
                }
            if "__type" in q:
                return {
                    "data": {"__type": {"fields": [{"name": "spans", "args": [{"name": "first"}]}]}}
                }
            return {"data": {"node": {"name": "hermes-minimal", "spans": {"edges": []}}}}

        monkeypatch.setattr(phoenix, "http_post_json", fake)
        monkeypatch.setattr(phoenix, "_ROOT_ARGS_CACHE", {})
        before = client.get("/status", params={"backend": "phx"}).json()
        assert before["project_resolved"] is None
        assert (
            client.get("/traces/search", params={"backend": "phx", "lookback_hours": 1}).status_code
            == 200
        )
        after = client.get("/status", params={"backend": "phx"}).json()
        assert after["project_resolved"] == "hermes-minimal" and after["project_fallback"] is True


# ── every adapter, one set of invariants (#406) ────────────────────────


def _every_adapter():
    return [pytest.param(cls, id=sorted(cls.handles)[0]) for cls in backends.adapters()]


class TestEveryAdapter:
    """What holds for every registered adapter, without a backend behind it."""

    @pytest.mark.parametrize("cls", _every_adapter())
    def test_declares_known_filters_at_known_levels(self, cls):
        assert cls.handles and all(t == t.lower() for t in cls.handles)
        assert set(cls.filter_support) <= set(FILTER_KEYS)
        assert set(cls.filter_support.values()) <= set(base.FILTER_LEVELS)

    @pytest.mark.parametrize("cls", _every_adapter())
    def test_status_is_a_pure_read_with_the_shared_shape(self, cls, monkeypatch):
        from helpers.dashboard import fake_http, generic_adapter

        http = fake_http(
            monkeypatch, {}, default=AssertionError("status must not call the backend")
        )
        a = generic_adapter(cls)
        st = a.status()
        assert http.calls == []
        assert st["type"] in cls.handles and isinstance(st["name"], str)
        assert isinstance(st["metrics"], bool) and isinstance(st["logs"], bool)
        assert set(st["filters"]) == set(FILTER_KEYS)
        assert isinstance(st.get("query_url"), str) and st["query_url"].startswith("http")

    @pytest.mark.parametrize("cls", _every_adapter())
    def test_trace_url_is_a_link_or_none_even_with_the_backend_down(self, cls, monkeypatch):
        """A link needs no backend: an unreachable one yields ``None``, never an
        error (#403: Phoenix used to resolve its project over HTTP here)."""
        from helpers.dashboard import fake_http, generic_adapter

        fake_http(monkeypatch, {}, default=_down)
        url = generic_adapter(cls).trace_url("0" * 32)
        assert url is None or url.startswith("http")

    @pytest.mark.parametrize("cls", _every_adapter())
    def test_an_unreachable_backend_is_a_backend_error(self, cls, monkeypatch):
        from helpers.dashboard import fake_http, generic_adapter

        fake_http(monkeypatch, {}, default=_down)
        a = generic_adapter(cls)
        with pytest.raises(BackendError) as ei:
            a.search(StructuredFilter(), 0, 10, 5)
        assert error_kind(ei.value) in {"unreachable", "backend", "config", "auth", "not_found"}
        with pytest.raises(BackendError):
            a.get_trace("0" * 32)
