"""The scenario harness's pure parts (scripts/scenarios, #278): the scenario
file loader and suites, markers, config overlays, the expectation grammar of
check.py and the manifest shape run.py writes. The Hermes runs themselves are
exercised by `make scenarios`, not here."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "scenarios"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"scenarios_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


common = _load("common")
check = _load("check")
run = _load("run")


def span(sid, name, parent=None, start=0, end=1, status="OK", **attrs):
    return {
        "span_id": sid,
        "parent_span_id": parent,
        "name": name,
        "status": status,
        "start_time_unix_nano": start,
        "end_time_unix_nano": end,
        "attributes": attrs,
    }


def turn_spans(marker="T-x", session="s1", turn=1, **root_attrs):
    return [
        span(
            "r",
            "agent",
            None,
            0,
            100,
            **{"hermes.session_id": session, "hermes.turn.number": turn, **root_attrs},
        ),
        span("l", "llm.m", "r", 1, 99),
        span("a1", "api.m", "l", 1, 10, **{"input.value": f"echo {marker}-out"}),
        span("t1", "tool.terminal", "l", 10, 30, **{"tool.name": "terminal"}),
        span("t2", "tool.terminal", "l", 15, 35, **{"tool.name": "terminal"}),
        span("a2", "api.m", "l", 40, 50, **{"output.value": f"{marker}-out"}),
    ]


def fails(findings):
    return [m for level, m in findings if level == "FAIL"]


# ── the scenario file ────────────────────────────────────────────────────


class TestScenarioFile:
    def test_library_loads_and_every_mode_is_known(self):
        data = common.load_scenarios(common.DEFAULT_SCENARIOS)
        ids = [s["id"] for s in data["scenarios"]]
        assert len(ids) == len(set(ids))
        assert {s["mode"] for s in data["scenarios"]} == set(common.MODES)

    def test_suites(self):
        data = common.load_scenarios(common.DEFAULT_SCENARIOS)
        quick = {s["id"] for s in common.select(data, suite="quick")}
        full = {s["id"] for s in common.select(data, suite="full")}
        extra = {s["id"] for s in common.select(data, suite="extra")}
        assert "shell-echo" in quick and "resume-three-turns" in quick
        assert quick < full and "gateway-five-sessions" in full and "cron-job" in full
        assert "moa-turn" in extra and not extra & full
        # the quick suite never needs a gateway, a scheduler or a second profile
        modes = {s["mode"] for s in common.select(data, suite="quick")}
        assert modes <= {"cli", "resume"}

    def test_only_keeps_order_and_rejects_unknown_ids(self):
        data = common.load_scenarios(common.DEFAULT_SCENARIOS)
        picked = common.select(data, only="no-tools,shell-echo", suite="quick")
        assert [s["id"] for s in picked] == ["no-tools", "shell-echo"]
        with pytest.raises(SystemExit):
            common.select(data, only="nope")

    def test_legacy_prompts_key_and_bad_mode(self, tmp_path):
        p = tmp_path / "s.yaml"
        p.write_text("prompts:\n  - id: a\n    prompt: hi\n")
        assert common.load_scenarios(p)["scenarios"][0]["mode"] == "cli"
        p.write_text("scenarios:\n  - id: a\n    mode: telepathy\n")
        with pytest.raises(SystemExit):
            common.load_scenarios(p)

    def test_every_expect_key_is_one_check_knows(self):
        """A typo in scenarios.yaml would silently check nothing."""
        known_turn = {
            "status", "llm_calls_min", "tools_min", "tools_max", "tool_names_any",
            "tools_concurrent_min", "tools_per_response_min", "span_prefix_count_min", "subagent_roots_min",
            "agent_depth_min", "attr_values_min", "span_names_any", "output_contains",
            "root_attrs", "span_attrs_any", "attrs_absent", "attr_max_len", "text_absent",
            "logs_min", "log_levels_any", "events_min", "event_names_any", "events_max",
            "logs_only_events", "logs_text_absent",
        }  # fmt: skip
        known_run = {
            "turn_numbers", "same_session", "sessions_distinct", "traces_distinct",
            "distinct_root_attr_min", "no_cross_session_logs", "log_attribution_in",
        }  # fmt: skip
        src = (SCRIPTS / "check.py").read_text()
        for key in known_turn | known_run:
            assert f'"{key}"' in src, key
        data = common.load_scenarios(common.DEFAULT_SCENARIOS)
        for s in data["scenarios"]:
            assert set(s.get("expect") or {}) <= known_turn, s["id"]
            assert set(s.get("expect_run") or {}) <= known_run, s["id"]
            for t in s.get("turns") or []:
                if isinstance(t, dict):
                    assert set(t.get("expect") or {}) <= known_turn, s["id"]


# ── markers and rendering ────────────────────────────────────────────────


class TestMarkers:
    def test_single_and_multi_turn_markers(self):
        assert run.marker_for("S1", "shell-echo", 0, 0, False) == "S1-shell-echo"
        assert run.marker_for("S1", "gw", 2, 1, True) == "S1-gw-s3t2"

    def test_prompt_is_rendered_and_tagged(self, tmp_path):
        text = common.tag(
            common.render(
                "echo {marker} in {workdir} for {token}",
                token="S1",
                marker="S1-a",
                workdir=tmp_path,
            ),
            "S1-a",
        )
        assert text.startswith(f"echo S1-a in {tmp_path} for S1")
        assert text.endswith("(scenario marker: S1-a)")

    def test_render_expect_substitutes_nested(self):
        ex = {"output_contains": "{marker}-x", "text_absent": ["{marker}"], "n": 3}
        assert common.render_expect(ex, "M") == {
            "output_contains": "M-x",
            "text_absent": ["M"],
            "n": 3,
        }

    def test_turns_of(self):
        assert run.turns_of({"prompt": "a"}) == [{"prompt": "a"}]
        got = run.turns_of({"turns": ["a", {"prompt": "b", "expect": {"status": "ok"}}]})
        assert got == [{"prompt": "a"}, {"prompt": "b", "expect": {"status": "ok"}}]

    def test_session_id_is_read_from_chat_output(self):
        out = "hello\n\nsession_id: 20261013_210419_e2ebf6\n"
        assert run.SESSION_RE.findall(out) == ["20261013_210419_e2ebf6"]
        assert (
            run.CRON_ID_RE.search("Created job: eaa1d152fa2e\n  Name: x").group(1) == "eaa1d152fa2e"
        )


# ── overlays ─────────────────────────────────────────────────────────────


class TestOverlay:
    def test_applies_and_restores_byte_for_byte(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        home = tmp_path / "profiles" / "p"
        home.mkdir(parents=True)
        cfg = "# keep me\nmodel: {default: m}\nagent:\n  disabled_toolsets: [delegation, memory]\n"
        (home / "config.yaml").write_text(cfg)
        scenario = {
            "config": {"model": {"base_url": "http://x"}},
            "enable_toolsets": ["delegation"],
            "otel": {"content_capture": "off"},
        }
        import yaml

        with common.Overlay("p", scenario):
            got = yaml.safe_load((home / "config.yaml").read_text())
            assert got["model"] == {"default": "m", "base_url": "http://x"}
            assert got["toolsets"] == ["delegation"]
            assert got["agent"]["disabled_toolsets"] == ["memory"]
            assert yaml.safe_load((home / "hermes_otel.yaml").read_text()) == {
                "content_capture": "off"
            }
        assert (home / "config.yaml").read_text() == cfg
        assert not (home / "hermes_otel.yaml").exists()

    def test_no_overlay_touches_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        with common.Overlay("p", {"prompt": "x"}) as o:
            assert o._saved == {}


# ── per-turn expectations ────────────────────────────────────────────────


class TestEvaluate:
    def test_a_good_turn_passes(self):
        ex = {
            "status": "ok",
            "llm_calls_min": 2,
            "tools_min": 2,
            "tool_names_any": ["terminal"],
            "output_contains": "{marker}-out",
            "root_attrs": {"hermes.turn.number": 1},
            "tools_concurrent_min": 2,
        }
        assert fails(check.evaluate(ex, "T-x", turn_spans(), None)) == []

    def test_counts_and_names(self):
        ex = {
            "llm_calls_min": 3,
            "tools_max": 1,
            "tool_names_any": ["web"],
            "span_names_any": ["skill."],
        }
        got = fails(check.evaluate(ex, "T-x", turn_spans(), None))
        assert len(got) == 4

    def test_status_reads_the_parentless_root(self):
        spans = turn_spans()
        spans[0]["status"] = "ERROR"
        assert fails(check.evaluate({"status": "ok"}, "T-x", spans, None))
        assert not fails(check.evaluate({"status": "error"}, "T-x", spans, None))

    def test_root_attrs_compare_booleans_and_strings(self):
        spans = turn_spans(**{"hermes.turn.trigger": "cron", "flag": "true"})
        ex = {"root_attrs": {"hermes.turn.trigger": "cron", "flag": True}}
        assert not fails(check.evaluate(ex, "T-x", spans, None))
        assert fails(
            check.evaluate({"root_attrs": {"hermes.turn.trigger": "api"}}, "T-x", spans, None)
        )

    def test_turn_root_is_the_outermost_agent(self):
        spans = [
            span("k", "kanban.claim"),
            span("w", "agent", "k", **{"hermes.profile": "b"}),
            span("c", "agent", "w", **{"hermes.profile": "child"}),
        ]
        assert check.turn_root(spans)["span_id"] == "w"
        assert check.turn_root([span("x", "api.m")])["span_id"] == "x"
        assert check.turn_root([]) is None

    def test_span_attrs_any_presence_and_substring(self):
        spans = turn_spans()
        spans[3]["attributes"]["gen_ai.tool.call.id"] = "call_1"
        ok = {"span_attrs_any": {"gen_ai.tool.call.id": "", "tool.name": "term"}}
        assert not fails(check.evaluate(ok, "T-x", spans, None))
        bad = fails(
            check.evaluate({"span_attrs_any": {"hermes.cron.job_id": ""}}, "T-x", spans, None)
        )
        assert bad and "values seen: none" in bad[0]

    def test_privacy_keys(self):
        spans = turn_spans()
        assert fails(check.evaluate({"attrs_absent": ["input.value"]}, "T-x", spans, None))
        assert not fails(
            check.evaluate({"attrs_absent": ["gen_ai.input.messages"]}, "T-x", spans, None)
        )
        assert fails(check.evaluate({"attr_max_len": {"input.value": 5}}, "T-x", spans, None))
        assert not fails(check.evaluate({"attr_max_len": {"input.value": 500}}, "T-x", spans, None))

    def test_text_absent_looks_in_spans_and_logs(self):
        spans = turn_spans()
        logs = [{"logger": "agent", "body": "key sk-123", "attributes": {}}]
        assert fails(check.evaluate({"text_absent": ["{marker}-out"]}, "T-x", spans, None))
        assert fails(check.evaluate({"text_absent": ["sk-123"]}, "T-x", spans, logs))
        assert not fails(check.evaluate({"text_absent": ["sk-999"]}, "T-x", spans, logs))
        # logs_text_absent ignores span content (spans are not redacted by design)
        spans[2]["attributes"]["output.value"] = "sk-123"
        assert fails(check.evaluate({"logs_text_absent": ["sk-123"]}, "T-x", spans, logs))
        assert not fails(check.evaluate({"logs_text_absent": ["sk-123"]}, "T-x", spans, []))

    def test_log_and_event_keys(self):
        logs = [
            {"level": "INFO", "session_id": "s1", "event_name": None, "logger": "agent"},
            {"level": "INFO", "session_id": "s1", "event_name": "hermes.tool.call", "logger": "e"},
        ]
        ex = {
            "logs_min": 2,
            "log_levels_any": ["INFO"],
            "events_min": 1,
            "event_names_any": ["hermes.tool.call"],
        }
        assert not fails(check.evaluate(ex, "T-x", turn_spans(), logs))
        assert fails(check.evaluate({"logs_only_events": True}, "T-x", turn_spans(), logs))
        assert fails(check.evaluate({"events_max": 0}, "T-x", turn_spans(), logs))
        # a source without logs skips every log key
        assert not fails(check.evaluate({"logs_min": 99}, "T-x", turn_spans(), None))

    def test_records_without_session_warn(self):
        logs = [{"level": "INFO", "session_id": None}]
        got = check.evaluate({}, "T-x", turn_spans(), logs)
        assert got and got[0][0] == "WARN"

    def test_multi_agent_keys(self):
        spans = [
            span("r", "agent"),
            span("s", "subagent.researcher", "r"),
            span("c", "agent", "s", **{"hermes.session.is_subagent": True}),
            span("x", "api.m", "c", **{"hermes.auxiliary.task": "moa_reference"}),
        ]
        ex = {
            "span_prefix_count_min": {"subagent.": 1},
            "subagent_roots_min": 1,
            "agent_depth_min": 2,
            "attr_values_min": {"hermes.auxiliary.task": {"moa_reference": 1}},
        }
        assert not fails(check.evaluate(ex, "T-x", spans, None))
        assert (
            len(
                fails(
                    check.evaluate(
                        {"agent_depth_min": 3, "subagent_roots_min": 2}, "T-x", spans, None
                    )
                )
            )
            == 2
        )

    def test_tools_per_response(self):
        apis = [span("a1", "api.m", start=0, end=10), span("a2", "api.m", start=40, end=50)]
        tools = [
            span("t1", "tool.x", start=11, end=12),
            span("t2", "tool.x", start=13, end=14),
            span("t3", "tool.x", start=51, end=52),
        ]
        assert check.tools_per_response(apis, tools) == 2
        assert fails(check.evaluate({"tools_per_response_min": 3}, "T-x", apis + tools, None))
        assert check.tools_per_response([], tools) == 0

    def test_overlap_max(self):
        spans = [
            span("a", "tool.x", start=0, end=10),
            span("b", "tool.x", start=5, end=15),
            span("c", "tool.x", start=10, end=20),
        ]
        assert check.overlap_max(spans) == 2
        assert check.overlap_max([]) == 0


# ── scenario-level expectations ──────────────────────────────────────────


def found_turn(session, turn, trace, session_id, logs=None, **root_attrs):
    spans = turn_spans(session=session_id, turn=turn, **root_attrs)
    return {
        "session": session,
        "turn": turn,
        "trace_id": trace,
        "root": check.turn_root(spans),
        "spans": spans,
        "logs": logs or [],
    }


class TestEvaluateRun:
    def test_resume_shape(self):
        turns = [found_turn(0, i, f"t{i}", "s1") for i in (1, 2, 3)]
        ex = {"same_session": True, "traces_distinct": True, "turn_numbers": [1, 2, 3]}
        assert fails(check.evaluate_run(ex, turns)) == []

    def test_turn_numbers_restarting_per_process_fail(self):
        turns = [found_turn(0, 1, "t1", "s1"), found_turn(0, 2, "t2", "s1")]
        turns[1]["root"]["attributes"]["hermes.turn.number"] = 1
        got = fails(check.evaluate_run({"turn_numbers": [1, 2]}, turns))
        assert got == ["session 1: hermes.turn.number [1, 1], expected [1, 2]"]

    def test_sessions_traces_and_cross_session_logs(self):
        turns = [
            found_turn(
                0,
                1,
                "t1",
                "a",
                logs=[{"session_id": "a", "attributes": {"hermes.log.attribution": "context"}}],
            ),
            found_turn(1, 1, "t1", "b", logs=[{"session_id": "a", "attributes": {}}]),
        ]
        ex = {
            "sessions_distinct": 3,
            "traces_distinct": True,
            "no_cross_session_logs": True,
            "log_attribution_in": ["context", "session_tag"],
            "same_session": True,
        }
        got = fails(check.evaluate_run(ex, turns))
        assert len(got) == 5

    def test_distinct_root_attr(self):
        turns = [
            found_turn(0, 1, "t1", "a", **{"hermes.profile": "scenarios"}),
            found_turn(1, 1, "t2", "b", **{"hermes.profile": "scenarios-b"}),
        ]
        assert not fails(
            check.evaluate_run({"distinct_root_attr_min": {"hermes.profile": 2}}, turns)
        )
        assert fails(
            check.evaluate_run({"distinct_root_attr_min": {"gen_ai.request.model": 2}}, turns)
        )

    def test_missing_turns_are_left_out_not_failed(self):
        turns = [found_turn(0, 1, "t1", "a"), {"session": 0, "turn": 2, "root": None}]
        got = check.evaluate_run({"turn_numbers": [1, 2]}, turns)
        assert fails(got) == [] and got[0][0] == "INFO"
        assert check.evaluate_run({}, turns) == []


# ── the report ───────────────────────────────────────────────────────────


class TestReport:
    def _results(self, findings, run_findings):
        return {
            "token": "S1",
            "profile": "scenarios",
            "sources": [{"name": "live"}, {"name": "jaeger", "down": True}],
            "runs": [
                {
                    "id": "resume",
                    "mode": "resume",
                    "turns": [
                        {
                            "marker": "S1-resume-s1t1",
                            "sources": {
                                "live": {
                                    "trace": "t",
                                    "spans": 5,
                                    "tools": 1,
                                    "logs": 3,
                                    "findings": findings,
                                },
                                "jaeger": {"skipped": "source down"},
                            },
                        },
                        {
                            "marker": "S1-resume-s1t2",
                            "sources": {
                                "live": {
                                    "trace": "u",
                                    "spans": 4,
                                    "tools": 0,
                                    "logs": 2,
                                    "findings": [],
                                },
                                "jaeger": {"skipped": "source down"},
                            },
                        },
                    ],
                    "run": {"live": run_findings},
                }
            ],
        }

    def test_matrix_rows_and_findings(self):
        manifest = {
            "started_at": "now",
            "runs": [
                {
                    "id": "resume",
                    "mode": "resume",
                    "turns": [{"marker": "S1-resume-s1t1", "exit_code": 0, "duration_s": 3}],
                }
            ],
        }
        results = self._results([("FAIL", "boom")], [("FAIL", "numbering")])
        text = check.render_report(results, manifest)
        assert "| resume | s1t1 | ✗ 5/1/3 | – down |" in text
        assert "| resume | run | ✗ | – down |" in text
        assert "Skipped (not answering): jaeger." in text
        assert "**FAIL** `resume-s1t1` @ live: boom" in text
        assert "**FAIL** `resume` (run) @ live: numbering" in text
        assert check.has_failure(results)
        assert not check.has_failure(self._results([("WARN", "meh")], []))

    def test_known_issue_downgrades_and_reports_when_fixed(self):
        results = self._results([("FAIL", "boom")], [("FAIL", "numbering")])
        entry = results["runs"][0]
        check.mark_known(entry, "#1")
        assert not check.has_failure(results)
        cell = entry["turns"][0]["sources"]["live"]
        assert cell["findings"] == [("KNOWN", "boom (known issue #1)")]
        assert check.cell_text(cell) == "≈ 5/1/3"
        fixed = self._results([], [])["runs"][0]
        check.mark_known(fixed, "#1")
        assert "no longer reproduces" in fixed["turns"][0]["sources"]["live"]["findings"][0][1]

    def test_pre_harness_manifest_entry_is_one_turn(self):
        legacy = {"id": "a", "marker": "B1-a", "exit_code": 0, "timed_out": False, "duration_s": 2}
        assert check.turns_of_run(legacy) == [
            {"marker": "B1-a", "exit_code": 0, "timed_out": False, "duration_s": 2}
        ]


# ── the manifest run.py writes (dry run) ─────────────────────────────────


def test_dry_run_prints_every_turn_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert run.main(["--dry-run", "--suite", "all"]) == 0
    out = capsys.readouterr().out
    assert "(scenario marker:" in out and "resume-three-turns" in out
    assert not (tmp_path / "profiles").exists()
    json.dumps(out)  # printable
