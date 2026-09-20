"""The dashboard's FastAPI routes, driven through a TestClient over a temp live store (#189).

The Hermes dashboard mounts ``plugin_api.router`` at ``/api/plugins/hermes_otel``.
Here the router is mounted bare and ``_get_live_store`` is pointed at a store
in ``tmp_path`` filled with a captured turn.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_otel.live_store import LiveStore

_DASHBOARD = Path(__file__).resolve().parent.parent.parent / "hermes_otel" / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))
import plugin_api  # noqa: E402

NOW_NS = time.time_ns()
MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning-nvfp4"
T1 = "daaf7828a1b2c3d4e5f60718293a4b5c"
T2 = "68947a9f2faf8802aaaaaaaaaaaaaaaa"


def _span(
    trace,
    name,
    sid,
    parent,
    attrs=None,
    start=NOW_NS - 5_000_000_000,
    end=NOW_NS - 1_000_000_000,
    status="OK",
):
    return {
        "trace_id": trace,
        "span_id": sid,
        "parent_span_id": parent,
        "name": name,
        "start_time_unix_nano": start,
        "end_time_unix_nano": end,
        "duration_ms": (end - start) / 1e6,
        "status": status,
        "attributes": attrs or {},
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    store = LiveStore(db_path=str(tmp_path / "live.db"))
    sess = {"hermes.session_id": "sess-1", "gen_ai.request.model": MODEL}
    # Turn 1: three API calls, a failing tool, root total 42,638.
    store.add_span(
        _span(T1, f"api.{MODEL}", "a1", "l1", {"gen_ai.usage.total_tokens": 13989, **sess})
    )
    store.add_span(_span(T1, "tool.terminal", "t1", "l1", sess, status="ERROR"))
    store.add_span(
        _span(T1, f"api.{MODEL}", "a2", "l1", {"gen_ai.usage.total_tokens": 14268, **sess})
    )
    store.add_span(
        _span(T1, f"api.{MODEL}", "a3", "l1", {"gen_ai.usage.total_tokens": 14381, **sess})
    )
    store.add_span(_span(T1, f"llm.{MODEL}", "l1", "root", sess))
    store.add_span(
        _span(
            T1,
            "agent",
            "root",
            None,
            {"gen_ai.usage.total_tokens": 42638, "input.value": "list the files", **sess},
        )
    )
    # Turn 2: one call, same session, newer.
    store.add_span(
        _span(
            T2,
            f"api.{MODEL}",
            "b1",
            "root2",
            {"gen_ai.usage.total_tokens": 13828, **sess},
            NOW_NS - 900_000_000,
            NOW_NS - 500_000_000,
        )
    )
    store.add_span(
        _span(
            T2,
            "agent",
            "root2",
            None,
            {"gen_ai.usage.total_tokens": 13828, **sess},
            NOW_NS - 900_000_000,
            NOW_NS - 500_000_000,
        )
    )
    for i in range(6):
        store.add_metric(
            "token_usage",
            100 + i,
            {"token_type": "input" if i % 2 else "output"},
            NOW_NS - i * 20_000_000_000,
        )
    store.add_metric("tool_duration", 2.4, {"tool_name": "terminal"}, NOW_NS - 1_000_000_000)
    store.add_log(
        {
            "level": "INFO",
            "logger": "agent.loop",
            "body": "API call #1",
            "time_unix_nano": NOW_NS - 4_000_000_000,
            "trace_id": T1,
            "session_id": "sess-1",
        }
    )
    store.add_log(
        {
            "level": "ERROR",
            "logger": "tools.terminal",
            "body": "No such file",
            "time_unix_nano": NOW_NS - 2_000_000_000,
            "trace_id": T1,
            "session_id": "sess-1",
        }
    )
    monkeypatch.setattr(plugin_api, "_get_live_store", lambda: store)
    app = FastAPI()
    app.include_router(plugin_api.router)
    with TestClient(app) as c:
        yield c
    store.close()


class TestTraces:
    def test_list_is_newest_first_with_totals_counted_once(self, client):
        r = client.get("/live/traces").json()
        assert r["live"] and r["total"] == 2
        first, second = r["traces"]
        assert first["traceId"] == T2 and second["traceId"] == T1
        assert second["tokens"] == 42638 and second["spanCount"] == 6 and second["error"] is True
        assert second["model"] == MODEL and second["session"] == "sess-1"
        assert first["cost"] is None  # nothing carries a cost: no "$0"

    def test_filters(self, client):
        assert client.get("/live/traces", params={"status": "error"}).json()["total"] == 1
        assert (
            client.get("/live/traces", params={"kind": "tool"}).json()["traces"][0]["traceId"] == T1
        )
        assert client.get("/live/traces", params={"text": "list the files"}).json()["total"] == 1
        assert client.get("/live/traces", params={"session": "nope"}).json()["total"] == 0
        assert client.get("/live/traces", params={"name": "tool.term"}).json()["total"] == 1
        page = client.get("/live/traces", params={"limit": 1, "offset": 1}).json()
        assert page["total"] == 2 and [t["traceId"] for t in page["traces"]] == [T1]

    def test_lookback_excludes_old_turns(self, client):
        # Both turns are within the last minute; a 1-second window from 'now' misses them.
        r = client.get(
            "/live/traces", params={"start_s": int(time.time()) - 0, "end_s": int(time.time()) + 1}
        ).json()
        assert r["total"] == 0

    def test_detail_and_404(self, client):
        r = client.get(f"/live/traces/{T1}").json()
        assert r["trace"]["spanCount"] == 6 and len(r["spans"]) == 6
        assert client.get("/live/traces/" + "0" * 32).status_code == 404
        assert client.get("/live/traces/not%20valid").status_code == 400


class TestSessions:
    def test_one_row_per_session(self, client):
        r = client.get("/live/sessions").json()
        (s,) = r["sessions"]
        assert s["session"] == "sess-1" and s["turns"] == 2 and s["errors"] == 1
        assert s["tokens"] == 42638 + 13828 and s["toolCalls"] == 1
        assert s["traceIds"] == [T2, T1]


class TestMetrics:
    def test_names_and_buckets(self, client):
        names = {n["name"]: n["count"] for n in client.get("/live/metrics/names").json()["names"]}
        assert names == {"token_usage": 6, "tool_duration": 1}
        r = client.get(
            "/live/metrics/query",
            params={
                "name": "token_usage",
                "group_by": "token_type",
                "bucket_s": 60,
                "lookback_hours": 1,
            },
        ).json()
        assert set(r["series"]) == {"input", "output"} and r["points"] == 6
        assert len(r["buckets"]) == len(r["series"]["input"]) == 61
        total = sum(v for s in r["series"].values() for v in s if v is not None)
        assert total == sum(100 + i for i in range(6))
        avg = client.get(
            "/live/metrics/query", params={"name": "tool_duration", "agg": "avg", "bucket_s": 3600}
        ).json()
        assert [v for v in avg["series"]["_"] if v is not None] == [2.4]

    def test_bad_agg_is_rejected(self, client):
        assert (
            client.get("/live/metrics/query", params={"name": "x", "agg": "median"}).status_code
            == 422
        )


class TestLogs:
    def test_search_filters(self, client):
        r = client.get("/live/logs/search", params={"trace_id": T1}).json()
        assert [l["level"] for l in r["logs"]] == ["ERROR", "INFO"]  # newest first
        assert len(client.get("/live/logs/search", params={"min_level": 40}).json()["logs"]) == 1
        assert (
            len(client.get("/live/logs/search", params={"logger": "agent.loop"}).json()["logs"])
            == 1
        )
        assert len(client.get("/live/logs/search", params={"text": "No such"}).json()["logs"]) == 1
        assert (
            len(client.get("/live/logs/search", params={"session": "sess-1"}).json()["logs"]) == 2
        )
        assert {l["logger"] for l in client.get("/live/loggers").json()["loggers"]} == {
            "agent.loop",
            "tools.terminal",
        }


class TestCursorEndpointsStillWork:
    def test_spans_and_status(self, client):
        st = client.get("/live/status").json()
        assert st["live"] and st["spans"] == 8 and st["metrics"] == 7 and st["logs"] == 2
        r = client.get("/live/spans", params={"since": 0, "limit": 3}).json()
        assert len(r["spans"]) == 3 and r["cursor"] == 17


def test_search_traces_without_backend_is_503(monkeypatch):
    monkeypatch.setattr(plugin_api, "resolve_adapter", lambda name=None: (None, [], None, None))
    app = FastAPI()
    app.include_router(plugin_api.router)
    with TestClient(app) as c:
        assert c.get("/traces/search").status_code == 503
        assert c.get("/traces/search", params={"lookback_hours": 9000}).status_code == 422


class TestSearchBarFilters:
    """The search bar's fields reach both sources (#183)."""

    def test_live_model_tool_and_duration_filters(self, client):
        assert client.get("/live/traces", params={"tool": "terminal"}).json()["total"] == 1
        assert client.get("/live/traces", params={"tool": "write_file"}).json()["total"] == 0
        assert client.get("/live/traces", params={"model": "nemotron-3-nano"}).json()["total"] == 2
        assert client.get("/live/traces", params={"model": "gpt-4"}).json()["total"] == 0
        # turn 1's spans last 4 s, turn 2's 0.4 s
        assert client.get("/live/traces", params={"min_duration_ms": 3000}).json()["total"] == 1
        assert client.get("/live/traces", params={"min_duration_ms": 100}).json()["total"] == 2
        assert client.get("/live/traces", params={"min_duration_ms": 10000}).json()["total"] == 0

    def test_backend_search_passes_attribute_filters(self, monkeypatch):
        seen = {}

        class Adapter:
            supports_metrics = False
            supports_logs = False
            cfg = {"type": "phoenix", "name": "phx"}

            def search(self, f, start_s, end_s, limit):
                seen["f"] = f
                return {"traces": []}

        monkeypatch.setattr(
            plugin_api, "resolve_adapter", lambda name=None: (Adapter(), [], None, None)
        )
        app = FastAPI()
        app.include_router(plugin_api.router)
        with TestClient(app) as c:
            r = c.get(
                "/traces/search",
                params={
                    "model": "m1",
                    "session": "s1",
                    "tool": "terminal",
                    "status": "error",
                    "min_duration_ms": 250,
                    "free_text": "hello",
                },
            )
        assert r.status_code == 200
        f = seen["f"]
        assert f.attr_equals == {
            "llm.model_name": "m1",
            "hermes.session_id": "s1",
            "tool.name": "terminal",
        }
        assert f.status == "error" and f.min_duration_ms == 250 and f.free_text == "hello"
        assert f.roots_only is False  # a tool filter matches tool spans


class TestSettingsRoute:
    """``/settings`` serves the settings report; secrets stay masked unless asked."""

    @pytest.fixture()
    def settings_client(self, tmp_path, monkeypatch):
        pytest.importorskip("yaml")
        from hermes_otel import plugin_config as pc

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv(pc.CONFIG_PATH_ENV, raising=False)
        monkeypatch.setattr(pc, "DURABLE_CONFIG_PATH", tmp_path / "hermes_otel.yaml")
        monkeypatch.setattr(pc, "DEFAULT_CONFIG_PATH", tmp_path / "legacy.yaml")
        (tmp_path / "hermes_otel.yaml").write_text(
            "project_name: demo\nbackends:\n  - type: langfuse\n    secret_key: sk-live\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("HERMES_OTEL_CAPTURE_FULL_PROMPTS", "true")
        app = FastAPI()
        app.include_router(plugin_api.router)
        return TestClient(app)

    def test_settings_shape_and_masking(self, settings_client):
        r = settings_client.get("/settings")
        assert r.status_code == 200
        body = r.json()
        assert body["config"]["path"].endswith("hermes_otel.yaml")
        assert body["config"]["path_source"] == "durable"
        assert body["counts"]["env"] == 1 and body["counts"]["file"] == 2
        by_key = {f["key"]: f for f in body["fields"]}
        assert by_key["capture_full_prompts"]["source"] == "env"
        assert by_key["project_name"]["source"] == "file"
        assert "sk-live" not in r.text
        assert body["capture_summary"]["mode"] == "full"
        assert any(
            e["name"] == "HERMES_OTEL_CAPTURE_FULL_PROMPTS" and e["set"] for e in body["env"]
        )
        assert body["effective_yaml"].startswith("# hermes-otel effective configuration")
        assert body["process"]["role"] == "dashboard"

    def test_reveal_shows_secrets(self, settings_client):
        r = settings_client.get("/settings?reveal=true")
        assert r.status_code == 200
        assert "sk-live" in r.json()["config"]["raw"]
