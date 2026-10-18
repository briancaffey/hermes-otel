#!/usr/bin/env python3
"""Per-module coverage floors (#412).

The global ``--cov-fail-under=85`` lets a refactor drop one module from 95 %
to 60 % unnoticed as long as the total stays up. This script reads the JSON
report pytest-cov writes (``--cov-report=json``) and ``coverage-floors.json``
(``{"<module path>": <percent>}``, recorded from ``main``) and fails when any
module is more than ``--tolerance`` points under its floor.

    python scripts/coverage_floor.py                 # check (CI, make ci-test)
    python scripts/coverage_floor.py --update        # re-record after a deliberate change
    python scripts/coverage_floor.py --add-missing   # floors for new modules only
    python scripts/coverage_floor.py --strict-new    # a module with no floor is a failure

A module that no longer exists is ignored; a new module without a floor is
reported and passes unless ``--strict-new`` is given, so the floor file is
updated deliberately, not on every PR. ``--add-missing`` records a floor for
each module without one, at its current coverage, and leaves every other
line of the file alone; ``--update`` rewrites them all.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple

DEFAULT_REPORT = "coverage.json"
DEFAULT_FLOORS = "coverage-floors.json"
DEFAULT_TOLERANCE = 1.0


def load_percents(report_path: Path) -> Dict[str, float]:
    """``{module: percent_covered}`` from a pytest-cov JSON report."""
    data = json.loads(report_path.read_text(encoding="utf-8"))
    files = data.get("files") or {}
    return {
        path.replace("\\", "/"): float(info["summary"]["percent_covered"])
        for path, info in files.items()
        if isinstance(info, dict) and "summary" in info
    }


def load_floors(floors_path: Path) -> Dict[str, float]:
    if not floors_path.exists():
        return {}
    data = json.loads(floors_path.read_text(encoding="utf-8"))
    return {k: float(v) for k, v in data.items() if not k.startswith("_")}


def check(
    percents: Dict[str, float], floors: Dict[str, float], tolerance: float
) -> Tuple[List[str], List[str]]:
    """``(failures, unfloored)``: modules under their floor, modules with none."""
    failures: List[str] = []
    for module, floor in sorted(floors.items()):
        if module not in percents:
            continue  # deleted or renamed; --update drops it
        actual = percents[module]
        if actual + tolerance < floor:
            failures.append(
                f"{module}: {actual:.1f}% is {floor - actual:.1f} points under its floor of {floor:.0f}%"
            )
    unfloored = sorted(m for m in percents if m not in floors)
    return failures, unfloored


def write_floors(percents: Dict[str, float], floors_path: Path) -> Dict[str, int]:
    """Record every module's floor as its percent rounded down."""
    floors = {module: int(math.floor(pct)) for module, pct in sorted(percents.items())}
    out = {
        "_comment": (
            "Per-module coverage floors (percent, branch coverage included), recorded "
            "from main by `python scripts/coverage_floor.py --update`. A PR may not "
            "drop a module more than one point under its floor; raise floors when "
            "a change deliberately adds coverage (#412)."
        ),
        **floors,
    }
    floors_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return floors


def add_missing_floors(percents: Dict[str, float], floors_path: Path) -> Dict[str, int]:
    """Insert a floor for every module that has none, at its percent rounded
    down; existing floors and the ``_comment`` are left as they are."""
    existing = json.loads(floors_path.read_text(encoding="utf-8")) if floors_path.exists() else {}
    added = {
        module: int(math.floor(pct))
        for module, pct in sorted(percents.items())
        if module not in existing
    }
    if added:
        floors_path.write_text(json.dumps({**existing, **added}, indent=2) + "\n", encoding="utf-8")
    return added


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--report", default=DEFAULT_REPORT, help="pytest-cov JSON report")
    ap.add_argument("--floors", default=DEFAULT_FLOORS, help="the floors file")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--update", action="store_true", help="re-record the floors from the report")
    mode.add_argument(
        "--add-missing", action="store_true", help="record floors for modules without one only"
    )
    ap.add_argument("--strict-new", action="store_true", help="fail on a module without a floor")
    args = ap.parse_args(argv)

    report = Path(args.report)
    if not report.exists():
        print(f"coverage floor: no report at {report} (run pytest with --cov-report=json)")
        return 2
    percents = load_percents(report)
    floors_path = Path(args.floors)

    if args.update:
        floors = write_floors(percents, floors_path)
        print(f"coverage floor: recorded {len(floors)} module floors in {floors_path}")
        return 0
    if args.add_missing:
        added = add_missing_floors(percents, floors_path)
        for module, floor in added.items():
            print(f"coverage floor: added {module} at {floor}%")
        print(f"coverage floor: added {len(added)} module floor(s) to {floors_path}")
        return 0

    floors = load_floors(floors_path)
    failures, unfloored = check(percents, floors, args.tolerance)
    for line in failures:
        print(f"coverage floor: FAIL {line}")
    for module in unfloored:
        print(
            f"coverage floor: no floor for {module} ({percents[module]:.1f}%); "
            "add it with --add-missing"
        )
    if failures or (args.strict_new and unfloored):
        print(
            f"coverage floor: {len(failures)} module(s) under their floor"
            + (f", {len(unfloored)} without one" if unfloored else "")
        )
        return 1
    print(f"coverage floor: ✓ {len(floors)} modules at or above their floors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
