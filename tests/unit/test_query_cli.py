"""The terminal query tool behind hermes_otel:observability (#215).

A live store built in tmp_path with one synthetic turn shaped like the README
tree (agent → skill, llm → api → tool, approval, subagent → agent → llm → api),
an older cron turn, a metric and a log record; then every command through
``main([...])`` with captured output.
"""

from __future__ import annotations

import importlib
import io
import json
import runpy
import sys
import time
from pathlib import Path

import pytest

from hermes_otel import query_cli
from hermes_otel.live_store import LiveStore
from hermes_otel.query_cli import (
    CliError,
    build_tree,
    compute_stats,
    fmt_cost,
    fmt_ms,
    fmt_num,
    main,
    one_line,
    parse_since,
    render_tree,
    span_summary,
    spans_from_otlp,
    trace_row_from_card,
)

NOW = time.time_ns()
S = 1_000_000_000
T1 = "a" * 32
T2 = "b" * 32
MODEL = "openai/gpt-5"


def _span(trace, name, sid, parent, start_off, dur_ms, attrs=None, status="OK"):
    start = NOW - start_off * S
    end = start + int(dur_ms * 1e6)
    return {
        "trace_id": trace,
        "span_id": sid,
        "parent_span_id": parent,
        "name": name,
        "start_time_unix_nano": start,
        "end_time_unix_nano": end,
        "duration_ms": dur_ms,
        "status": status,
        "attributes": attrs or {},
    }


def _turn_spans():
    """The README tree, with the attributes the summaries read."""
    root_attrs = {
        "hermes.session_id": "sess-1",
        "hermes.session.kind": "cli",
        "hermes.turn.number": 3,
        "hermes.turn.tool_count": 1,
        "hermes.turn.api_call_count": 3,
        "hermes.turn.final_status": "completed",
        "hermes.platform": "cli",
        "gen_ai.usage.total_tokens": 300,
        "hermes.cost.usage": 0.03,
        "input.value": "list the files here",
        "output.value": "Done: two files.",
    }
    return [
        _span(T1, "agent", "r1", None, 60, 40_000, root_attrs),
        _span(
            T1,
            "skill.observability",
            "sk1",
            "r1",
            59,
            39_000,
            {"hermes.skill.source": "skill_view"},
        ),
        _span(
            T1,
            "llm." + MODEL,
            "l1",
            "r1",
            58,
            38_000,
            {"llm.model_name": MODEL, "llm.provider": "openai", "llm.request.message_count": 3},
        ),
        _span(
            T1,
            "api." + MODEL,
            "a1",
            "l1",
            57,
            2_000,
            {
                "gen_ai.request.model": MODEL,
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 20,
                "gen_ai.usage.reasoning.output_tokens": 5,
                "gen_ai.usage.cache_read.input_tokens": 40,
                "hermes.cost.usage": 0.01,
                "gen_ai.response.finish_reasons": '["tool_calls"]',
                "llm.response.tool_calls": 1,
            },
        ),
        _span(
            T1,
            "tool.bash",
            "t1",
            "a1",
            55,
            300,
            {
                "tool.name": "bash",
                "hermes.tool.outcome": "completed",
                "hermes.tool.command": "ls -la",
            },
        ),
        _span(
            T1,
            "approval.rm",
            "ap1",
            "a1",
            54,
            1_000,
            {"hermes.approval.choice": "once", "hermes.approval.decided_by": "user"},
        ),
        _span(
            T1,
            "subagent.researcher",
            "sa1",
            "l1",
            50,
            10_000,
            {
                "hermes.subagent.role": "researcher",
                "hermes.subagent.status": "completed",
                "hermes.subagent.goal": "find the slow tool",
            },
        ),
        _span(
            T1,
            "agent",
            "r2",
            "sa1",
            49,
            9_000,
            {"hermes.session_id": "sess-1-child", "hermes.session.is_subagent": True},
        ),
        _span(T1, "llm." + MODEL, "l2", "r2", 48, 8_000, {"llm.model_name": MODEL}),
        _span(
            T1,
            "api." + MODEL,
            "a2",
            "l2",
            47,
            7_000,
            {
                "gen_ai.usage.input_tokens": 50,
                "gen_ai.usage.output_tokens": 10,
                "hermes.cost.usage": 0.005,
            },
        ),
        _span(
            T1,
            "api." + MODEL,
            "a3",
            "l1",
            30,
            5_000,
            {
                "gen_ai.usage.input_tokens": 120,
                "gen_ai.usage.output_tokens": 30,
                "exception.message": "boom",
            },
            status="ERROR",
        ),
    ]


def _cron_spans():
    return [
        _span(
            T2,
            "cron",
            "c1",
            None,
            3 * 86_400,
            2_000,
            {"hermes.session_id": "sess-cron", "hermes.cron.job_id": "j1"},
        ),
        _span(
            T2, "api." + MODEL, "c2", "c1", 3 * 86_400 - 1, 1_000, {"gen_ai.usage.input_tokens": 10}
        ),
    ]


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "live.db"
    store = LiveStore(db_path=str(path))
    for sp in _turn_spans() + _cron_spans():
        store.add_span(sp)
    store.add_metric(
        "hermes.token.usage",
        120,
        {"gen_ai.request.model": MODEL, "token_type": "input"},
        NOW - 57 * S,
    )
    store.add_metric(
        "hermes.token.usage",
        30,
        {"gen_ai.request.model": MODEL, "token_type": "output"},
        NOW - 57 * S,
    )
    store.add_metric(
        "hermes.token.usage",
        50,
        {"gen_ai.request.model": "other/model", "token_type": "input"},
        NOW - 47 * S,
    )
    store.add_log(
        {
            "level": "INFO",
            "logger": "hermes.tools",
            "body": "ran bash",
            "time_unix_nano": NOW - 55 * S,
            "trace_id": T1,
            "session_id": "sess-1",
        }
    )
    store.add_log(
        {
            "level": "ERROR",
            "logger": "hermes.api",
            "body": "boom",
            "time_unix_nano": NOW - 30 * S,
            "trace_id": T1,
        }
    )
    store.flush()
    store.close()
    return str(path)


def run(*argv, db=None):
    out, err = io.StringIO(), io.StringIO()
    args = list(argv)
    if db is not None and "--db" not in args:
        args += ["--db", db]
    code = main(args, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


# ── pure helpers ──────────────────────────────────────────────────────────


class TestFormatters:
    def test_parse_since_durations(self):
        assert parse_since("30m", now_s=10_000) == 10_000 - 1800
        assert parse_since("2h", now_s=10_000) == 10_000 - 7200
        assert parse_since("1.5d", now_s=200_000) == 200_000 - 129_600
        assert parse_since("1w", now_s=1_000_000) == 1_000_000 - 604_800
        assert parse_since(None) is None
        assert parse_since("") is None

    def test_parse_since_iso(self):
        assert parse_since("2026-09-20") == 1_789_862_400
        assert parse_since("2026-09-20T10:00") == 1_789_862_400 + 36_000

    def test_parse_since_rejects_garbage(self):
        with pytest.raises(CliError):
            parse_since("yesterday")

    def test_fmt(self):
        assert fmt_ms(812) == "812 ms"
        assert fmt_ms(4210) == "4.21 s"
        assert fmt_ms(65_000) == "1m 05s"
        assert fmt_ms(3_700_000) == "1h 01m"
        assert fmt_ms(None) == "-"
        assert fmt_num(28794) == "28,794"
        assert fmt_num(1.5) == "1.50"
        assert fmt_num(None) == "-"
        assert fmt_cost(0.0041) == "$0.0041"
        assert fmt_cost(1.239) == "$1.24"
        assert fmt_cost(0) == "$0"
        assert one_line("a\nb", 80) == "a⏎b"
        assert one_line("x" * 20, 10) == "x" * 9 + "…"


class TestTree:
    def test_build_tree_nests_by_parent_and_orders_by_start(self):
        roots = build_tree(_turn_spans())
        assert [r["name"] for r in roots] == ["agent"]
        kids = [k["name"] for k in roots[0]["children"]]
        assert kids == ["skill.observability", "llm." + MODEL]
        llm = roots[0]["children"][1]
        assert [k["name"] for k in llm["children"]] == [
            "api." + MODEL,
            "subagent.researcher",
            "api." + MODEL,
        ]
        api = llm["children"][0]
        assert [k["name"] for k in api["children"]] == ["tool.bash", "approval.rm"]
        # the sub-agent's own run nests under the subagent span
        sub = llm["children"][1]
        assert sub["children"][0]["name"] == "agent"
        assert sub["children"][0]["children"][0]["children"][0]["name"] == "api." + MODEL

    def test_orphans_become_roots(self):
        spans = [_span(T1, "agent", "r", None, 10, 100), _span(T1, "tool.x", "t", "missing", 9, 10)]
        assert [r["name"] for r in build_tree(spans)] == ["agent", "tool.x"]

    def test_render_tree_matches_readme_shape(self):
        out = io.StringIO()
        render_tree(_turn_spans(), out, width=160)
        text = out.getvalue()
        lines = text.splitlines()
        assert lines[0].startswith("agent ")
        assert "turn 3 · 1 tools · 3 api calls · 300 tok · $0.03 · completed · cli" in lines[0]
        assert lines[1].startswith("├── skill.observability")
        assert "skill_view" in lines[1]
        assert lines[2].startswith("└── llm." + MODEL)
        assert "openai · 3 msgs" in lines[2]
        assert lines[3].startswith("    ├── api." + MODEL)
        assert (
            "100 → 20 tok · 5 reasoning · 40 cached · $0.01 · tool_calls · 1 tool calls" in lines[3]
        )
        assert lines[4].startswith("    │   ├── tool.bash")
        assert "completed · ls -la" in lines[4]
        assert lines[5].startswith("    │   └── approval.rm")
        assert "once · by user" in lines[5]
        assert lines[6].startswith("    ├── subagent.researcher")
        assert "researcher · completed · find the slow tool" in lines[6]
        assert lines[7].startswith("    │   └── agent")
        assert lines[10].startswith("    └── api." + MODEL)
        assert "ERROR: boom" in lines[10]
        assert lines[-1].startswith("── 11 spans · 40.00 s · 300 tokens · $0.03 · 1 error")
        # the waterfall column: the root fills the bar, a short child does not
        assert "▇" * query_cli.TREE_BAR_WIDTH in lines[0]
        assert "▇" * query_cli.TREE_BAR_WIDTH not in lines[4]

    def test_render_tree_wraps_summary_when_narrow(self):
        out = io.StringIO()
        render_tree(_turn_spans(), out, width=60, bars=False)
        text = out.getvalue()
        assert "↳ turn 3" in text  # the summary moved to its own line
        assert "▇" not in text

    def test_render_tree_io_and_attrs(self):
        out = io.StringIO()
        render_tree(_turn_spans(), out, width=160, show_io=True, show_attrs=True, io_chars=8)
        text = out.getvalue()
        assert "in  │ list the… (+11 chars)" in text
        assert "out │ Done: tw… (+8 chars)" in text
        assert "hermes.tool.outcome = completed" in text

    def test_render_tree_empty(self):
        out = io.StringIO()
        render_tree([], out)
        assert "no spans" in out.getvalue()

    def test_span_summary_ignores_unknown_kinds(self):
        assert span_summary({"name": "mystery", "attributes": {"x": 1}}) == ""
        assert span_summary({"name": "tool.x", "status": "ERROR", "attributes": {}}) == "ERROR"


# ── commands against the live store ───────────────────────────────────────


class TestStatus:
    def test_reports_fill_and_no_backends(self, db, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(Path(db).parent))
        code, out, _ = run("status", db=db)
        assert code == 0
        assert "13 spans · 3 metric points · 2 logs" in out
        assert "config file   (none found" in out or "config file" in out

    def test_json(self, db, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(Path(db).parent))
        code, out, _ = run("status", "--json", db=db)
        info = json.loads(out)
        assert info["live_store"]["spans"] == 13
        assert info["live_store"]["exists"] is True
        assert isinstance(info["backends"], list)

    def test_missing_store(self, tmp_path):
        code, out, _ = run("status", db=str(tmp_path / "nope.db"))
        assert code == 0
        assert "missing" in out

    def test_lists_configured_backends(self, db, monkeypatch):
        class Cls:
            supports_metrics = True
            supports_logs = False

        class FakeBackends:
            @staticmethod
            def load_config():
                return (
                    Path("/cfg.yaml"),
                    [
                        {"type": "phoenix", "endpoint": "http://p:6006/v1/traces"},
                        {"type": "honeycomb"},
                    ],
                    "phoenix",
                )

            @staticmethod
            def find_adapter_class(t):
                return Cls if t == "phoenix" else None

            @staticmethod
            def backend_label(b):
                return b.get("name") or b["type"]

        monkeypatch.setattr(query_cli, "_import_backends", lambda: FakeBackends)
        code, out, _ = run("status", db=db)
        assert code == 0
        assert "phoenix        phoenix      http://p:6006/v1/traces" in out
        assert "queryable here: traces · metrics" in out
        assert "honeycomb" in out and "no query adapter" in out
        assert "default --source: phoenix" in out


class TestTraces:
    def test_lists_newest_first(self, db):
        code, out, _ = run("traces", db=db)
        assert code == 0
        lines = out.splitlines()
        assert lines[0].startswith("when (UTC)")
        assert "agent" in lines[2] and T1[:12] in lines[2] and "ERROR" in lines[2]
        assert "cron" in lines[3] and T2[:12] in lines[3]
        assert "2 trace(s)" in out

    def test_filters(self, db):
        _, out, _ = run("traces", "--status", "error", "--json", db=db)
        assert [t["traceId"] for t in json.loads(out)] == [T1]
        _, out, _ = run("traces", "--since", "1h", "--json", db=db)
        assert [t["traceId"] for t in json.loads(out)] == [T1]
        _, out, _ = run("traces", "--tool", "bash", "--json", db=db)
        assert [t["traceId"] for t in json.loads(out)] == [T1]
        _, out, _ = run("traces", "--name", "cron", "--json", db=db)
        assert [t["traceId"] for t in json.loads(out)] == [T2]
        _, out, _ = run("traces", "--session", "sess-cron", "--json", db=db)
        assert [t["traceId"] for t in json.loads(out)] == [T2]
        _, out, _ = run("traces", "--min-duration", "30000", "--json", db=db)
        assert [t["traceId"] for t in json.loads(out)] == [T1]

    def test_no_rows(self, db):
        code, out, _ = run("traces", "--session", "nobody", db=db)
        assert code == 0 and "(no rows)" in out

    def test_missing_store_is_an_error(self, tmp_path):
        code, out, err = run("traces", db=str(tmp_path / "nope.db"))
        assert code == 1 and "no live store at" in err and out == ""

    def test_bad_since(self, db):
        code, _, err = run("traces", "--since", "soon", db=db)
        assert code == 1 and "--since" in err


class TestTrace:
    def test_last_renders_the_tree(self, db):
        code, out, _ = run("trace", "last", "--width", "160", db=db)
        assert code == 0
        assert out.startswith(f"trace {T1} · source live · ")
        assert "session sess-1" in out and f"model {MODEL}" in out
        assert "├── skill.observability" in out
        assert "    │   ├── tool.bash" in out
        assert "── 11 spans" in out

    def test_last_n_and_out_of_range(self, db):
        code, out, _ = run("trace", "last-2", db=db)
        assert code == 0 and out.startswith(f"trace {T2}")
        code, _, err = run("trace", "last-3", db=db)
        assert code == 1 and "only 2 trace(s)" in err

    def test_by_id_and_json(self, db):
        code, out, _ = run("trace", T2, "--json", db=db)
        payload = json.loads(out)
        assert payload["trace"]["traceId"] == T2
        assert [s["name"] for s in payload["spans"]] == ["cron", "api." + MODEL]

    def test_unknown_id(self, db):
        code, _, err = run("trace", "c" * 32, db=db)
        assert code == 1 and "not found" in err

    def test_last_within_session(self, db):
        _, out, _ = run("trace", "last", "--session", "sess-cron", db=db)
        assert out.startswith(f"trace {T2}")


class TestSpan:
    def test_prints_every_attribute(self, db):
        code, out, _ = run("span", "t1", db=db)
        assert code == 0
        assert out.startswith("tool.bash  (300 ms, OK)")
        assert f"trace {T1}  span t1  parent a1" in out
        assert "hermes.tool.command = ls -la" in out

    def test_multiline_value_is_indented(self, db):
        code, out, _ = run("span", "r1", db=db)
        assert "input.value = list the files here" in out

    def test_json_and_missing(self, db):
        _, out, _ = run("span", "a1", "--json", db=db)
        assert json.loads(out)["attributes"]["gen_ai.usage.input_tokens"] == 100
        code, _, err = run("span", "zzz", db=db)
        assert code == 1 and "not found" in err


class TestSessions:
    def test_table(self, db):
        code, out, _ = run("sessions", db=db)
        assert code == 0
        row = next(ln for ln in out.splitlines() if ln.startswith("sess-1 "))
        cells = row.split()
        assert cells[1] == "1"  # turns
        assert "sess-cron" in out

    def test_json(self, db):
        _, out, _ = run("sessions", "--json", "--since", "1d", db=db)
        rows = json.loads(out)
        assert [r["session"] for r in rows] == ["sess-1"]
        assert rows[0]["toolCalls"] == 1 and rows[0]["errors"] == 1


class TestStats:
    def test_compute_stats(self):
        st = compute_stats({T1: _turn_spans(), T2: _cron_spans(), "empty": []})
        assert st["traces"] == 2 and st["turns"] == 2 and st["sessions"] == 2
        assert st["spans"] == 13 and st["errors"] == 1
        assert st["tokens"] == 300 and st["cost"] == pytest.approx(0.03)
        assert st["tools"]["bash"] == {"calls": 1, "errors": 0, "completed": 1, "ms": 300.0}
        m = st["models"][MODEL]
        assert m["calls"] == 4 and m["in"] == 280 and m["out"] == 60 and m["errors"] == 1
        assert st["skills"] == {"observability": 1}
        assert st["approvals"] == {"once": 1}
        assert st["slowest"][0]["traceId"] == T1

    def test_command(self, db):
        code, out, _ = run("stats", "--since", "2h", db=db)
        assert code == 0
        assert "turns 1 · sessions 1 · spans 11 · errors 1 · tokens 300 · cost $0.03" in out
        assert "\nmodels\n" in out and MODEL in out
        assert "\ntools\n" in out and "bash" in out
        assert "skills loaded: observability ×1" in out
        assert "approvals: once ×1" in out
        assert "slowest turns" in out

    def test_json(self, db):
        _, out, _ = run("stats", "--json", db=db)
        st = json.loads(out)
        assert st["turns"] == 2 and st["window"]["traces_considered"] == 2


class TestMetrics:
    def test_list(self, db):
        code, out, _ = run("metrics", db=db)
        assert code == 0
        assert "hermes.token.usage" in out and "3" in out
        assert "--group-by" in out

    def test_group_by(self, db):
        code, out, _ = run(
            "metrics",
            "hermes.token.usage",
            "--since",
            "2h",
            "--group-by",
            "gen_ai.request.model",
            db=db,
        )
        assert code == 0
        assert "hermes.token.usage · agg sum · bucket 5m · 3 points" in out
        assert MODEL in out and "150" in out
        assert "other/model" in out and "50" in out

    def test_buckets_and_aggs(self, db):
        _, out, _ = run(
            "metrics", "hermes.token.usage", "--since", "2h", "--buckets", "--bucket", "1m", db=db
        )
        assert "bucket (UTC)" in out
        for agg in ("count", "avg", "max", "last"):
            code, out, _ = run(
                "metrics", "hermes.token.usage", "--since", "2h", "--agg", agg, db=db
            )
            assert code == 0, agg
        _, out, _ = run(
            "metrics", "hermes.token.usage", "--since", "2h", "--agg", "count", "--json", db=db
        )
        assert json.loads(out)["agg"] == "count"

    def test_no_data_and_bad_bucket(self, db):
        code, out, _ = run("metrics", "nope", "--since", "2h", db=db)
        assert code == 0 and "no data" in out
        code, _, err = run("metrics", "hermes.token.usage", "--bucket", "soon", db=db)
        assert code == 1 and "--bucket" in err


class TestLogs:
    def test_prints_records(self, db):
        code, out, _ = run("logs", db=db)
        assert code == 0
        assert "ERROR    hermes.api" in out and "ran bash" in out
        assert f"[trace {T1[:12]}]" in out
        assert "2 record(s)" in out

    def test_filters(self, db):
        _, out, _ = run("logs", "--level", "error", "--json", db=db)
        assert [r["body"] for r in json.loads(out)] == ["boom"]
        _, out, _ = run("logs", "--logger", "hermes.tools", "--json", db=db)
        assert [r["body"] for r in json.loads(out)] == ["ran bash"]
        _, out, _ = run("logs", "--text", "bash", "--session", "sess-1", "--json", db=db)
        assert len(json.loads(out)) == 1
        _, out, _ = run("logs", "--since", "20s", db=db)
        assert "(no log records" in out


class TestSql:
    def test_select(self, db):
        code, out, _ = run(
            "sql",
            "select name, count(*) as n from events where kind='span' group by name order by n desc",
            db=db,
        )
        assert code == 0
        assert "api." + MODEL in out and "4" in out
        assert "row(s)" in out

    def test_json_and_json_extract(self, db):
        _, out, _ = run(
            "sql",
            "select json_extract(data, '$.attributes.\"hermes.tool.outcome\"') as outcome from events where name='tool.bash'",
            "--json",
            db=db,
        )
        assert json.loads(out) == [{"outcome": "completed"}]

    def test_rejects_writes(self, db):
        code, _, err = run("sql", "delete from events", db=db)
        assert code == 1 and "read-only" in err
        # even a SELECT-prefixed statement cannot write: the connection is mode=ro
        code, _, err = run("sql", "select 1; delete from events", db=db)
        assert code == 1

    def test_sql_error_and_missing(self, db, tmp_path):
        code, _, err = run("sql", "select nope from events", db=db)
        assert code == 1 and "sqlite:" in err
        code, _, err = run("sql", "select 1", db=str(tmp_path / "nope.db"))
        assert code == 1 and "no live store" in err

    def test_limit(self, db):
        _, out, _ = run("sql", "select seq from events", "--limit", "2", db=db)
        assert "2 row(s) (limit 2)" in out


# ── backend sources ───────────────────────────────────────────────────────


def _otlp_trace():
    def attr(k, v):
        if isinstance(v, bool):
            return {"key": k, "value": {"boolValue": v}}
        if isinstance(v, int):
            return {"key": k, "value": {"intValue": str(v)}}
        if isinstance(v, float):
            return {"key": k, "value": {"doubleValue": v}}
        return {"key": k, "value": {"stringValue": v}}

    base = NOW - 100 * S
    return {
        "batches": [
            {
                "resource": {"attributes": [attr("service.name", "hermes-e2e")]},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "ff" * 16,
                                "spanId": "1",
                                "name": "agent",
                                "startTimeUnixNano": str(base),
                                "endTimeUnixNano": str(base + 5 * S),
                                "attributes": [
                                    attr("hermes.session_id", "s9"),
                                    attr("hermes.turn.number", 1),
                                ],
                                "status": {"code": 1},
                            },
                            {
                                "traceId": "ff" * 16,
                                "spanId": "2",
                                "parentSpanId": "1",
                                "name": "tool.bash",
                                "startTimeUnixNano": str(base + S),
                                "endTimeUnixNano": str(base + 2 * S),
                                "attributes": [
                                    attr("hermes.tool.outcome", "error"),
                                    attr("x", 1.5),
                                    attr("flag", True),
                                ],
                                "status": {"code": 2, "message": "exit 1"},
                            },
                        ]
                    }
                ],
            }
        ]
    }


class TestOtlp:
    def test_spans_from_otlp(self):
        spans = spans_from_otlp(_otlp_trace())
        assert [s["name"] for s in spans] == ["agent", "tool.bash"]
        root, tool = spans
        assert root["status"] == "OK" and root["attributes"]["service.name"] == "hermes-e2e"
        assert root["attributes"]["hermes.turn.number"] == 1
        assert (
            tool["parent_span_id"] == "1"
            and tool["status"] == "ERROR"
            and tool["status_message"] == "exit 1"
        )
        assert tool["attributes"] == {
            "hermes.tool.outcome": "error",
            "x": 1.5,
            "flag": True,
            "service.name": "hermes-e2e",
        }
        assert tool["duration_ms"] == 1000.0
        assert spans_from_otlp({}) == []

    def test_trace_row_from_card(self):
        card = {
            "traceID": "ff" * 16,
            "rootTraceName": "agent",
            "rootServiceName": "hermes",
            "startTimeUnixNano": str(NOW),
            "durationMs": 4210,
            "spanCount": 7,
            "spanSets": [
                {
                    "spans": [
                        {
                            "attributes": [
                                {"key": "llm.model_name", "value": {"stringValue": MODEL}},
                                {"key": "status", "value": {"stringValue": "error"}},
                                {"key": "hermes.session_id", "value": {"stringValue": "s9"}},
                            ]
                        }
                    ]
                }
            ],
        }
        row = trace_row_from_card(card)
        assert row["traceId"] == "ff" * 16 and row["model"] == MODEL and row["error"] is True
        assert row["session"] == "s9" and row["durationMs"] == 4210 and row["spanCount"] == 7
        assert row["endNs"] == NOW + 4210 * 1_000_000


class FakeAdapter:
    supports_metrics = False
    supports_logs = False

    def __init__(self):
        self.calls = []

    def search(self, f, start_s, end_s, limit):
        self.calls.append(("search", f, start_s, end_s, limit))
        return {
            "traces": [
                {
                    "traceID": "ff" * 16,
                    "rootTraceName": "agent",
                    "rootServiceName": "hermes",
                    "startTimeUnixNano": str(NOW - 100 * S),
                    "durationMs": 5000,
                    "spanCount": 2,
                    "spanSets": [{"spans": [{"attributes": []}]}],
                }
            ]
        }

    def get_trace(self, trace_id):
        self.calls.append(("get_trace", trace_id))
        return _otlp_trace() if trace_id == "ff" * 16 else {"batches": []}


@pytest.fixture()
def fake_backends(monkeypatch):
    from dataclasses import dataclass, field
    from typing import Dict, Optional

    @dataclass
    class StructuredFilter:
        service: Optional[str] = None
        name_regex: Optional[str] = None
        attr_equals: Dict[str, str] = field(default_factory=dict)
        min_duration_ms: Optional[int] = None
        status: Optional[str] = None
        free_text: Optional[str] = None
        raw: Optional[str] = None
        roots_only: bool = True

    @dataclass
    class LogFilter:
        trace_id: Optional[str] = None
        session: Optional[str] = None
        min_level: int = 0
        logger: Optional[str] = None
        text: Optional[str] = None

    adapter = FakeAdapter()

    class Base:
        pass

    Base.StructuredFilter = StructuredFilter
    Base.LogFilter = LogFilter

    class Backends:
        base = Base

        @staticmethod
        def resolve_adapter(name):
            if name == "phoenix":
                return adapter, [{"type": "phoenix"}], Path("/cfg"), None
            if name == "honeycomb":
                return None, [{"type": "honeycomb"}], Path("/cfg"), None
            raise KeyError("phoenix, honeycomb")

    monkeypatch.setattr(query_cli, "_import_backends", lambda: Backends)
    return adapter


class TestBackendSource:
    def test_traces_through_adapter(self, fake_backends, db):
        code, out, _ = run(
            "traces",
            "--source",
            "phoenix",
            "--since",
            "2h",
            "--tool",
            "bash",
            "--model",
            MODEL,
            "--json",
            db=db,
        )
        assert code == 0
        assert json.loads(out)[0]["traceId"] == "ff" * 16
        _, f, start_s, end_s, limit = fake_backends.calls[0]
        assert f.attr_equals == {"llm.model_name": MODEL, "tool.name": "bash"}
        assert f.roots_only is False and limit == 20
        assert end_s - start_s == pytest.approx(7200, abs=5)

    def test_trace_tree_through_adapter(self, fake_backends, db):
        code, out, _ = run("trace", "last", "--source", "phoenix", "--width", "160", db=db)
        assert code == 0
        assert "source phoenix" in out
        assert "└── tool.bash" in out and "error · ERROR: exit 1" in out
        code, _, err = run("trace", "ee" * 16, "--source", "phoenix", db=db)
        assert code == 1 and "not found in phoenix" in err

    def test_span_needs_trace_on_backend(self, fake_backends, db):
        code, _, err = run("span", "2", "--source", "phoenix", db=db)
        assert code == 1 and "--trace" in err
        code, out, _ = run("span", "2", "--source", "phoenix", "--trace", "ff" * 16, db=db)
        assert code == 0 and "hermes.tool.outcome = error" in out

    def test_stats_through_adapter(self, fake_backends, db):
        code, out, _ = run("stats", "--source", "phoenix", db=db)
        assert code == 0 and "turns 1" in out and "bash" in out

    def test_unsupported_views(self, fake_backends, db):
        code, _, err = run("sessions", "--source", "phoenix", db=db)
        assert code == 1 and "live-store view" in err
        code, _, err = run("metrics", "--source", "phoenix", db=db)
        assert code == 1 and "does not serve metrics" in err
        code, _, err = run("metrics", "x", "--source", "phoenix", db=db)
        assert code == 1 and "does not serve metrics" in err
        code, _, err = run("logs", "--source", "phoenix", db=db)
        assert code == 1 and "does not serve logs" in err

    def test_unknown_and_adapterless_backends(self, fake_backends, db):
        code, _, err = run("traces", "--source", "jaeger", db=db)
        assert code == 1 and "configured: phoenix, honeycomb" in err
        code, _, err = run("traces", "--source", "honeycomb", db=db)
        assert code == 1 and "no query adapter" in err

    def test_metrics_and_logs_when_supported(self, fake_backends, db):
        fake_backends.supports_metrics = True
        fake_backends.supports_logs = True
        fake_backends.metric_names = lambda s, e: [{"name": "m", "count": 2, "lastTs": NOW}]
        fake_backends.metrics_query = lambda name, s, e, b, g, agg: {
            "name": name,
            "agg": agg,
            "bucketS": b,
            "buckets": [NOW],
            "series": {"_": [3.0]},
            "points": 1,
        }
        fake_backends.logs_search = lambda f, s, e, limit: [
            {
                "level": "INFO",
                "logger": "l",
                "body": f"text={f.text} lvl={f.min_level}",
                "time_unix_nano": NOW,
            }
        ]
        _, out, _ = run("metrics", "--source", "phoenix", db=db)
        assert "m" in out
        _, out, _ = run("metrics", "m", "--source", "phoenix", db=db)
        assert "window total" in out and "3" in out
        _, out, _ = run("logs", "--source", "phoenix", "--text", "hi", "--level", "warning", db=db)
        assert "text=hi lvl=30" in out

    def test_yaml_is_required_for_backends(self, monkeypatch, db):
        monkeypatch.setitem(sys.modules, "yaml", None)
        code, _, err = run("traces", "--source", "phoenix", db=db)
        assert code == 1 and "pyyaml" in err
        code, out, _ = run("status", db=db)
        assert code == 0 and "backends      unavailable: CliError" in out


# ── the launcher and the fastapi fallback ─────────────────────────────────


def test_launcher_runs_main(db, capsys):
    script = Path(query_cli.__file__).parent / "skills" / "observability" / "scripts" / "otel.py"
    assert script.is_file()
    argv = sys.argv
    sys.argv = [str(script), "traces", "--db", db, "--json"]
    try:
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = argv
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)[0]["traceId"] == T1


def test_http_exception_fallback_without_fastapi(monkeypatch):
    dash = Path(query_cli.__file__).parent / "dashboard"
    if str(dash) not in sys.path:
        sys.path.insert(0, str(dash))
    import backends.base as base  # noqa: E402

    original = base.HTTPException
    monkeypatch.setitem(sys.modules, "fastapi", None)
    importlib.reload(base)
    try:
        exc = base.HTTPException(status_code=502, detail="Backend unreachable")
        assert exc.status_code == 502 and exc.detail == "Backend unreachable"
        assert str(exc) == "502: Backend unreachable"
        assert base.HTTPException is not original
    finally:
        monkeypatch.undo()
        importlib.reload(base)
    assert base.HTTPException is original or base.HTTPException.__module__ == original.__module__
