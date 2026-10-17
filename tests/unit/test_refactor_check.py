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
        assert rc.compare(_side({"p": t}), _side({"p": t}), {}) == []

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
        diffs = rc.compare(_side({"p": a}, metrics=["m1"]), _side({"p": b}, metrics=["m2"]), {})
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
        assert rc.compare(_side({"p": a}, metrics=["m1"]), _side({"p": b}, metrics=[]), allow) == []

    def test_check_batch_matrix_cells_are_compared(self, rc):
        report_a = {"runs": [{"id": "p", "sources": {"live": {"findings": []}}}]}
        report_b = {"runs": [{"id": "p", "sources": {"live": {"findings": [["FAIL", "x"]]}}}]}
        t = [[{"name": "agent", "parent": None, "status": "OK", "keys": [], "values": {}}]]
        diffs = rc.compare(_side({"p": t}, check=report_a), _side({"p": t}, check=report_b), {})
        assert diffs == ["check_batch p/live: base=PASS branch=FAIL"]


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
