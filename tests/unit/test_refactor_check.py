"""scripts/refactor_check/refactor_check.py (#410): the parts that need no Hermes run."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rc():
    spec = importlib.util.spec_from_file_location(
        "refactor_check", REPO_ROOT / "scripts" / "refactor_check" / "refactor_check.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


def _span(name, sid, parent=None, status="OK", **attrs):
    return {
        "name": name,
        "span_id": sid,
        "parent_span_id": parent,
        "status": status,
        "attributes": attrs,
    }


def _side(traces, metrics=("hermes.token.usage",), check=None, logs=None):
    return {
        "label": "x",
        "token": "T",
        "profile": "p",
        "prompts": "a",
        "check": check or {"runs": []},
        "live": {"traces": traces, "metrics": list(metrics), "logs": logs or {}},
    }


class TestNormalise:
    def test_shape_keeps_keys_and_stable_values_only(self, rc):
        root = _span("agent", "r", **{"hermes.session_id": "s", "hermes.turn.number": 1})
        api = _span(
            "api.gpt",
            "a",
            "r",
            **{"gen_ai.usage.total_tokens": 42, "tool.name": "x", "input.value": "hi"},
        )
        by_id = {"r": root, "a": api}
        shape = rc.normalise_span(api, by_id, ignore=["input.value"])
        assert shape["parent"] == "agent" and shape["status"] == "OK"
        assert shape["keys"] == ["gen_ai.usage.total_tokens", "tool.name"]
        assert shape["values"] == {"tool.name": "x"}
        root_shape = rc.normalise_span(root, by_id, ignore=[])
        assert root_shape["parent"] is None and "hermes.session_id" not in root_shape["keys"]
        assert root_shape["values"] == {"hermes.turn.number": 1}

    def test_marker_id_is_found_in_any_string_attribute(self, rc):
        spans = [
            _span(
                "agent",
                "r",
                **{"input.value": "echo R1-shell-echo-out (scenario marker: R1-shell-echo)"},
            )
        ]
        assert rc._marker_id("R1", spans) == "shell-echo"
        assert rc._marker_id("R2", spans) is None


class TestCompare:
    def test_identical_sides_are_quiet(self, rc):
        t = [[{"name": "agent", "parent": None, "status": "OK", "keys": ["a"], "values": {}}]]
        assert rc.compare(_side({"p": t}), _side({"p": t}), {}) == ([], [])

    def test_every_kind_of_difference_is_named(self, rc):
        a = [
            [
                {
                    "name": "agent",
                    "parent": None,
                    "status": "OK",
                    "keys": ["a", "b"],
                    "values": {"tool.name": "x"},
                }
            ]
        ]
        b = [
            [
                {
                    "name": "agent",
                    "parent": None,
                    "status": "ERROR",
                    "keys": ["a", "c"],
                    "values": {"tool.name": "y"},
                }
            ]
        ]
        diffs, tolerated = rc.compare(
            _side({"p": a}, metrics=["m1"]), _side({"p": b}, metrics=["m2"]), {}
        )
        assert tolerated == []
        text = "\n".join(diffs)
        assert "status base='OK' branch='ERROR'" in text
        assert (
            "attributes gone on branch: ['b']" in text and "attributes new on branch: ['c']" in text
        )
        assert "tool.name base='x' branch='y'" in text
        assert "metric names: gone=['m1'] new=['m2']" in text

    def test_allowlist_tolerates_prompts_and_metrics(self, rc):
        a = [[{"name": "agent", "parent": None, "status": "OK", "keys": [], "values": {}}]]
        b = [[{"name": "cron", "parent": None, "status": "OK", "keys": [], "values": {}}]]
        allow = {"tolerate_prompts": ["p"], "ignore_metrics": ["m1"]}
        assert rc.compare(_side({"p": a}, metrics=["m1"]), _side({"p": b}, metrics=[]), allow) == (
            [],
            [],
        )

    def test_check_batch_matrix_cells_are_compared(self, rc):
        report_a = {"runs": [{"id": "p", "sources": {"live": {"findings": []}}}]}
        report_b = {"runs": [{"id": "p", "sources": {"live": {"findings": [["FAIL", "x"]]}}}]}
        t = [[{"name": "agent", "parent": None, "status": "OK", "keys": [], "values": {}}]]
        diffs, _ = rc.compare(_side({"p": t}, check=report_a), _side({"p": t}, check=report_b), {})
        assert diffs == ["check_batch p/live: base=PASS branch=FAIL"]


# ── the auxiliary race (#552) ──────────────────────────────────────────
# The no-tools turn of scripts/refactor_check/out/int-wave-e-vs-main, as the
# live store shapes it: the root, the auxiliary api.* span (a child of the
# main api.* span), the main api.* span under the llm span, the llm span.

API = "api.nvidia/nemotron-3-super-120b-a12b"
LLM = "llm.nvidia/nemotron-3-super-120b-a12b"
ROOT_KEYS = [
    "gen_ai.operation.name",
    "gen_ai.usage.total_tokens",
    "hermes.platform",
    "hermes.turn.api_call_count",
    "hermes.turn.number",
    "input.value",
    "output.value",
]
AUX_ROOT_KEYS = [
    "hermes.turn.auxiliary.api_call_count",
    "hermes.turn.auxiliary.cost.status",
    "hermes.turn.auxiliary.input_tokens",
    "hermes.turn.auxiliary.output_tokens",
]
REQUEST_KEYS = [
    "gen_ai.input.messages",
    "gen_ai.operation.name",
    "gen_ai.request.model",
    "hermes.auxiliary.task",
    "hermes.content.input_chars",
    "hermes.query_source",
    "hermes.turn.number",
    "input.value",
    "llm.request.max_tokens",
]
SWEEP_KEYS = ["hermes.span.ended_by", "hermes.span.sweep_reason"]
RESPONSE_KEYS = [
    "gen_ai.output.messages",
    "gen_ai.response.finish_reasons",
    "gen_ai.response.model",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
    "hermes.content.output_chars",
    "hermes.cost.source",
    "hermes.cost.status",
    "llm.response.duration_ms",
    "llm.response.finish_reason",
    "llm.response.output_chars",
    "llm.token_count.completion",
    "llm.token_count.prompt",
    "llm.token_count.total",
    "output.mime_type",
    "output.value",
]
METRICS = ["gen_ai.client.token.usage", "hermes.auxiliary.count", "hermes.token.usage"]
SWEPT = "hermes.span.swept.count"


def _shape(name, parent, keys, **values):
    return {"name": name, "parent": parent, "status": "OK", "keys": sorted(keys), "values": values}


def _turn(aux_landed):
    root = _shape(
        "agent",
        None,
        ROOT_KEYS + (AUX_ROOT_KEYS if aux_landed else []),
        **{
            "gen_ai.operation.name": "invoke_agent",
            "hermes.platform": "cli",
            "hermes.turn.number": 1,
        },
    )
    aux_values = {"gen_ai.operation.name": "chat", "hermes.query_source": "auxiliary"}
    if aux_landed:
        aux_values["hermes.cost.status"] = "unknown"
    aux = _shape(
        API, API, REQUEST_KEYS + (RESPONSE_KEYS if aux_landed else SWEEP_KEYS), **aux_values
    )
    main = _shape(
        API,
        LLM,
        REQUEST_KEYS + RESPONSE_KEYS + ["hermes.api.ttfc_ms"],
        **{
            "gen_ai.operation.name": "chat",
            "hermes.query_source": "main",
            "hermes.cost.status": "unknown",
        },
    )
    llm = _shape(
        LLM, "agent", ["gen_ai.operation.name", "hermes.turn.number"], **{"hermes.turn.number": 1}
    )
    return [root, aux, main, llm]


def _raced_sides(swept):
    """base and branch sides where the auxiliary call lost the race on ``swept``."""
    base = _side(
        {"no-tools": [_turn(aux_landed=swept != "base")]},
        metrics=METRICS + ([SWEPT] if swept == "base" else []),
    )
    branch = _side(
        {"no-tools": [_turn(aux_landed=swept != "branch")]},
        metrics=METRICS + ([SWEPT] if swept == "branch" else []),
    )
    return base, branch


class TestAuxiliaryRace:
    def test_run_e_base_swept_is_tolerated_not_failed(self, rc, tmp_path, capsys):
        base, branch = _raced_sides("base")
        diffs, tolerated = rc.compare(base, branch, {})
        assert diffs == []
        assert len(tolerated) == 2
        assert (
            tolerated[0].startswith("no-tools: auxiliary race (#552)")
            and "swept on base" in tolerated[0]
        )
        assert tolerated[1] == f"metric names: {SWEPT} only on base, from the race above"
        for name, side in (("base", base), ("branch", branch)):
            (tmp_path / name).mkdir()
            (tmp_path / name / "side.json").write_text(json.dumps(side))
        assert rc.main(["--recompare", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "No differences outside the allowlist." in out
        assert "Tolerated, not failing the run:" in out and "auxiliary race" in out

    def test_run_f_branch_swept_and_an_unmarked_trace_are_tolerated(self, rc):
        base, branch = _raced_sides("branch")
        # The auxiliary call that started after the root ended: its own trace,
        # branch only, carrying no exact batch marker (#577).
        branch["live"]["unmarked"] = [[_shape(API, None, RESPONSE_KEYS)]]
        diffs, tolerated = rc.compare(base, branch, {})
        assert diffs == []
        assert [t for t in tolerated if "swept on branch" in t]
        assert f"{SWEPT} only on branch" in tolerated[1]
        assert tolerated[2] == (
            f"1 trace(s) on branch carry no exact batch marker (roots ['{API}']): "
            "an auxiliary call that started after its turn (#577), not compared"
        )

    def test_sides_recorded_before_unmarked_existed_still_compare(self, rc):
        base, branch = _raced_sides("branch")
        assert "unmarked" not in branch["live"]
        diffs, _ = rc.compare(base, branch, {})
        assert diffs == []


class TestExactMarker:
    """#577: a trace is keyed by the batch's exact marker, never by marker-like text."""

    TOKEN = "R20261017-branch"
    ECHO = f"{TOKEN}-shell-echo-out"

    def test_echoed_marker_like_text_matches_no_prompt(self, rc):
        # Title generation after on_session_end: its own trace, echoing the
        # shell output but not the prompt's marker line.
        stray = [_span(API, "x", **{"output.value": f"Echo {self.ECHO}"})]
        assert rc._marker_id(self.TOKEN, stray, ["shell-echo", "no-tools"]) is None
        assert rc._marker_id(self.TOKEN, stray) is None

    def test_the_marker_line_wins(self, rc):
        turn = [
            _span(
                "agent",
                "r",
                **{"input.value": f"echo {self.ECHO}\n(scenario marker: {self.TOKEN}-shell-echo)"},
            )
        ]
        assert rc._marker_id(self.TOKEN, turn, ["shell-echo"]) == "shell-echo"

    def test_a_bare_exact_marker_counts_without_the_marker_line(self, rc):
        # A clipped preview lost the marker line; the rendered marker survives.
        turn = [_span("agent", "r", **{"input.value": f"say {self.TOKEN}-multi-s1t2 now"})]
        assert rc._marker_id(self.TOKEN, turn, ["multi-s1t1", "multi-s1t2", "multi"]) == (
            "multi-s1t2"
        )
        assert rc._marker_id(self.TOKEN, turn, ["multi"]) is None
        assert rc._marker_id(self.TOKEN, turn) is None

    def test_batch_marker_ids_reads_every_turn(self, rc, tmp_path):
        manifest = tmp_path / "runs.json"
        manifest.write_text(
            json.dumps(
                {
                    "runs": [
                        {"marker": f"{self.TOKEN}-shell-echo", "turns": []},
                        {
                            "marker": f"{self.TOKEN}-multi-s1t1",
                            "turns": [
                                {"marker": f"{self.TOKEN}-multi-s1t1"},
                                {"marker": f"{self.TOKEN}-multi-s1t2"},
                            ],
                        },
                        {"turns": [{"marker": "other-token-x"}]},
                    ]
                }
            )
        )
        assert rc.batch_marker_ids(manifest, self.TOKEN) == [
            "multi-s1t1",
            "multi-s1t2",
            "shell-echo",
        ]
        assert rc.batch_marker_ids(tmp_path / "missing.json", self.TOKEN) == []

    def test_dump_live_sets_the_stray_trace_aside(self, rc, tmp_path, monkeypatch):
        from hermes_otel.live_store import LIVE_DB_FILENAME, LiveStore

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(rc, "profile_home", lambda profile: home)
        store = LiveStore(db_path=str(home / LIVE_DB_FILENAME))
        now = time.time_ns()
        marker_line = f"(scenario marker: {self.TOKEN}-shell-echo)"
        for trace_id, span_id, name, text in (
            ("t1", "r1", "agent", f"echo {self.ECHO}\n{marker_line}"),
            ("t2", "a2", API, f"Echo {self.ECHO}"),
        ):
            store.add_span(
                {
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "parent_span_id": None,
                    "name": name,
                    "status": "OK",
                    "start_time_unix_nano": now,
                    "end_time_unix_nano": now + 1,
                    "attributes": {"input.value": text},
                }
            )
        store.flush()
        store.close()
        live = rc.dump_live("p", self.TOKEN, (), ids=["shell-echo"])
        assert sorted(live["traces"]) == ["shell-echo"]
        assert [[s["name"] for s in t] for t in live["unmarked"]] == [[API]]

    def test_identical_sides_report_no_race(self, rc):
        base, _ = _raced_sides("branch")
        assert rc.compare(base, json.loads(json.dumps(base)), {}) == ([], [])

    def test_a_second_differing_api_span_fails(self, rc):
        base, branch = _raced_sides("branch")
        main_span = branch["live"]["traces"]["no-tools"][0][2]
        main_span["keys"].remove("llm.response.output_chars")
        diffs, tolerated = rc.compare(base, branch, {})
        assert tolerated == []
        assert any("attributes gone on branch: ['llm.response.output_chars']" in d for d in diffs)
        assert any(
            "attributes new on branch: ['hermes.span.ended_by', 'hermes.span.sweep_reason']" in d
            for d in diffs
        )

    def test_a_root_difference_outside_auxiliary_fails(self, rc):
        base, branch = _raced_sides("branch")
        branch["live"]["traces"]["no-tools"][0][0]["keys"].append("hermes.turn.tool_count")
        diffs, tolerated = rc.compare(base, branch, {})
        assert tolerated == [] and any("hermes.turn.tool_count" in d for d in diffs)

    def test_a_non_response_key_on_the_swept_span_fails(self, rc):
        base, branch = _raced_sides("branch")
        branch["live"]["traces"]["no-tools"][0][1]["keys"].remove("hermes.auxiliary.task")
        diffs, tolerated = rc.compare(base, branch, {})
        assert tolerated == [] and any("hermes.auxiliary.task" in d for d in diffs)

    def test_a_swept_main_span_is_not_the_race(self, rc):
        base, branch = _raced_sides("branch")
        for side in (base, branch):
            side["live"]["traces"]["no-tools"][0][1]["values"]["hermes.query_source"] = "main"
        diffs, tolerated = rc.compare(base, branch, {})
        assert tolerated == [] and diffs

    def test_the_swept_metric_on_the_answered_side_still_fails(self, rc):
        base, branch = _raced_sides("base")
        base["live"]["metrics"].remove(SWEPT)
        branch["live"]["metrics"].append(SWEPT)
        diffs, tolerated = rc.compare(base, branch, {})
        assert diffs == [f"metric names: gone=[] new=['{SWEPT}']"]
        assert len(tolerated) == 1 and "swept on base" in tolerated[0]

    def test_a_status_or_parent_change_on_the_raced_span_fails(self, rc):
        base, branch = _raced_sides("branch")
        branch["live"]["traces"]["no-tools"][0][1]["status"] = "ERROR"
        diffs, tolerated = rc.compare(base, branch, {})
        assert tolerated == [] and any("status base='OK' branch='ERROR'" in d for d in diffs)


class TestPackageInstall:
    def test_materialise_head_and_install_preserve_the_profile_files(self, rc, tmp_path):
        staged = tmp_path / "staged"
        label = rc.materialise("HEAD", staged)
        assert label.startswith("HEAD @") and (staged / "hermes_otel" / "plugin.yaml").exists()
        plugin_dir = tmp_path / "plugins" / "hermes_otel"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "config.yaml").write_text("keep: me\n")
        (plugin_dir / "stale.py").write_text("gone\n")
        rc.install_package(staged, plugin_dir)
        assert (plugin_dir / "plugin.yaml").exists() and (plugin_dir / "hooks" / "llm.py").exists()
        assert (plugin_dir / "config.yaml").read_text() == "keep: me\n"
        assert not (plugin_dir / "stale.py").exists()
        backup = tmp_path / "backup"
        rc.backup_plugin(plugin_dir, backup)
        (plugin_dir / "plugin.yaml").write_text("broken")
        rc.restore_plugin(backup, plugin_dir)
        assert (plugin_dir / "plugin.yaml").read_text() != "broken"

    def test_materialise_working_tree_copies_the_package(self, rc, tmp_path):
        label = rc.materialise(None, tmp_path)
        assert label.startswith("working tree @")
        assert (tmp_path / "hermes_otel" / "tracer.py").exists()


class TestBatchMetricNames:
    """#532: metric names are compared for the batch, not for the whole live store."""

    def test_batch_started_ns_reads_the_manifest(self, rc, tmp_path):
        manifest = tmp_path / "runs.json"
        manifest.write_text(json.dumps({"started_unix": 1792238308.5}))
        assert rc.batch_started_ns(manifest) == 1792238308500000000
        manifest.write_text("{}")
        assert rc.batch_started_ns(manifest) == 0
        assert rc.batch_started_ns(tmp_path / "missing.json") == 0

    def test_dump_live_keeps_only_names_recorded_since_the_batch_start(
        self, rc, tmp_path, monkeypatch
    ):
        from hermes_otel.live_store import LIVE_DB_FILENAME, LiveStore

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(rc, "profile_home", lambda profile: home)
        store = LiveStore(db_path=str(home / LIVE_DB_FILENAME))
        # Recent timestamps: the store's retention sweep drops old rows on flush.
        old_ns = time.time_ns() - 120 * 1_000_000_000
        store.add_metric("hermes.skill.inferred", 1, {}, ts_ns=old_ns)
        store.add_metric("hermes.token.usage", 7, {}, ts_ns=old_ns + 60 * 1_000_000_000)
        store.flush()
        store.close()
        batch_start = old_ns + 30 * 1_000_000_000
        assert rc.dump_live("p", "T", (), since_ns=batch_start)["metrics"] == ["hermes.token.usage"]
        assert rc.dump_live("p", "T", ())["metrics"] == [
            "hermes.skill.inferred",
            "hermes.token.usage",
        ]


def test_allowlist_loads_with_reasons(rc):
    allow = rc.load_allowlist(REPO_ROOT / "scripts" / "refactor_check" / "allowlist.yaml")
    assert "hermes.preview.output.truncated" in allow["ignore_attribute_keys"]
    assert allow["tolerate_prompts"] == []


def test_dry_run_prints_the_plan(rc, capsys):
    assert rc.main(["--dry-run", "--base", "main", "--only", "shell-echo"]) == 0
    assert "vs main" in capsys.readouterr().out


def test_recompare_scores_saved_sides_with_the_current_allowlist(rc, tmp_path, capsys):
    shape = {"name": "api.m", "parent": "agent", "status": "OK", "keys": ["a"], "values": {}}
    base = {
        "label": "l",
        "token": "T",
        "profile": "p",
        "prompts": "a",
        "check": {"runs": []},
        "live": {"traces": {"p": [[shape]]}, "metrics": [], "logs": {}},
    }
    branch = json.loads(json.dumps(base))
    branch["live"]["traces"]["p"][0][0]["keys"] = ["a", "llm.token_count.prompt_details.cache_read"]
    for name, side in (("base", base), ("branch", branch)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "side.json").write_text(json.dumps(side))
    assert rc.main(["--recompare", str(tmp_path)]) == 0
    assert "No differences outside the allowlist" in capsys.readouterr().out
    assert (tmp_path / "report.md").exists()
    branch["live"]["traces"]["p"][0][0]["keys"] = ["a", "brand.new.key"]
    (tmp_path / "branch" / "side.json").write_text(json.dumps(branch))
    assert rc.main(["--recompare", str(tmp_path)]) == 1
