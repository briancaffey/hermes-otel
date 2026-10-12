"""scripts/coverage_floor.py: the per-module gate (#412)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def floor():
    spec = importlib.util.spec_from_file_location(
        "coverage_floor", REPO_ROOT / "scripts" / "coverage_floor.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _report(tmp_path: Path, percents: dict) -> Path:
    p = tmp_path / "coverage.json"
    p.write_text(
        json.dumps({"files": {m: {"summary": {"percent_covered": v}} for m, v in percents.items()}})
    )
    return p


class TestCheck:
    def test_under_the_floor_by_more_than_the_tolerance_fails(self, floor):
        failures, unfloored = floor.check(
            {"a.py": 90.0, "b.py": 70.0}, {"a.py": 91, "b.py": 80}, 1.0
        )
        assert failures == ["b.py: 70.0% is 10.0 points under its floor of 80%"]
        assert unfloored == []

    def test_a_deleted_module_is_ignored_and_a_new_one_reported(self, floor):
        failures, unfloored = floor.check({"new.py": 50.0}, {"gone.py": 99}, 1.0)
        assert failures == [] and unfloored == ["new.py"]


class TestCli:
    def test_update_records_floors_rounded_down(self, floor, tmp_path):
        report = _report(tmp_path, {"hermes_otel/x.py": 93.7, "hermes_otel/y.py": 100.0})
        floors = tmp_path / "floors.json"
        assert floor.main(["--report", str(report), "--floors", str(floors), "--update"]) == 0
        data = json.loads(floors.read_text())
        assert data["hermes_otel/x.py"] == 93 and data["hermes_otel/y.py"] == 100
        assert data["_comment"].startswith("Per-module coverage floors")

    def test_check_passes_within_tolerance_and_fails_beyond(self, floor, tmp_path, capsys):
        floors = tmp_path / "floors.json"
        floors.write_text(json.dumps({"hermes_otel/x.py": 93}))
        ok = _report(tmp_path, {"hermes_otel/x.py": 92.2})
        assert floor.main(["--report", str(ok), "--floors", str(floors)]) == 0
        bad = _report(tmp_path, {"hermes_otel/x.py": 88.0})
        assert floor.main(["--report", str(bad), "--floors", str(floors)]) == 1
        assert (
            "hermes_otel/x.py: 88.0% is 5.0 points under its floor of 93%"
            in capsys.readouterr().out
        )

    def test_strict_new_fails_on_an_unfloored_module(self, floor, tmp_path):
        floors = tmp_path / "floors.json"
        floors.write_text(json.dumps({}))
        report = _report(tmp_path, {"hermes_otel/new.py": 10.0})
        assert floor.main(["--report", str(report), "--floors", str(floors)]) == 0
        assert floor.main(["--report", str(report), "--floors", str(floors), "--strict-new"]) == 1

    def test_missing_report_is_a_distinct_exit_code(self, floor, tmp_path):
        assert floor.main(["--report", str(tmp_path / "none.json")]) == 2


def test_the_committed_floors_cover_the_package():
    data = json.loads((REPO_ROOT / "coverage-floors.json").read_text())
    modules = [k for k in data if not k.startswith("_")]
    assert "hermes_otel/tracer.py" in modules and "hermes_otel/hooks/llm.py" in modules
    assert all(0 <= v <= 100 for k, v in data.items() if not k.startswith("_"))
