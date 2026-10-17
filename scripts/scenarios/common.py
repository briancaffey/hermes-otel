"""Shared plumbing for the scenario harness: profile homes, the scenario file,
markers, and the per-scenario config overlays.

Kept free of third-party imports except PyYAML (loaded lazily), so run.py,
check.py and setup_profile.py stay runnable with ``uv run --extra dev python``.
"""

from __future__ import annotations

import copy
import datetime as dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from _common import profile_home  # noqa: E402

DEFAULT_SCENARIOS = HERE / "scenarios.yaml"
DEFAULT_PROFILE = "scenarios"
MODES = ("cli", "resume", "gateway", "cron", "kanban")
MARKER_PREFIX = "scenario marker"


def yaml_mod():
    try:
        import yaml  # type: ignore
    except ImportError:
        sys.exit("pyyaml is required: uv run --extra dev python scripts/scenarios/<script>.py")
    return yaml


# ── Profiles ─────────────────────────────────────────────────────────────


def runs_dir(profile: str, token: str) -> Path:
    """Where a run keeps its manifest, transcripts and report."""
    return profile_home(profile) / "scenario_runs" / token


# ── Scenario file ────────────────────────────────────────────────────────


def load_scenarios(path: Path) -> Dict[str, Any]:
    """The scenario file, normalised: ``scenarios`` (``prompts`` accepted) is a
    list of mappings, each with an ``id`` and a ``mode``."""
    with path.open("r", encoding="utf-8") as f:
        data = yaml_mod().safe_load(f)
    if not isinstance(data, dict):
        sys.exit(f"{path}: expected a mapping")
    items = data.get("scenarios", data.get("prompts"))
    if not isinstance(items, list):
        sys.exit(f"{path}: expected a `scenarios:` list")
    seen = set()
    for s in items:
        if not isinstance(s, dict) or not s.get("id"):
            sys.exit(f"{path}: every scenario needs an `id`")
        if s["id"] in seen:
            sys.exit(f"{path}: duplicate scenario id {s['id']!r}")
        seen.add(s["id"])
        s.setdefault("mode", "cli")
        if s["mode"] not in MODES:
            sys.exit(f"{path}: scenario {s['id']!r} has unknown mode {s['mode']!r}")
    data["scenarios"] = items
    return data


def select(data: Dict[str, Any], only: str = "", suite: str = "") -> List[Dict[str, Any]]:
    """Scenarios picked by ``--only`` ids, else by ``--suite`` (a scenario's
    ``suite`` defaults to ``quick``; ``all`` takes every one)."""
    items = data["scenarios"]
    wanted = [s.strip() for s in only.split(",") if s.strip()]
    if wanted:
        by_id = {s["id"]: s for s in items}
        missing = [w for w in wanted if w not in by_id]
        if missing:
            sys.exit(f"unknown scenario id(s): {', '.join(missing)}")
        return [by_id[w] for w in wanted]
    if not suite or suite == "all":
        return list(items)
    suites = data.get("suites") or {}
    if suite in suites:
        allowed = set(suites[suite])
        return [s for s in items if (s.get("suite") or "quick") in allowed]
    return [s for s in items if (s.get("suite") or "quick") == suite]


def default_token() -> str:
    return "S" + dt.datetime.now().strftime("%y%m%d-%H%M%S")


def render(text: str, *, token: str, marker: str, workdir: Path) -> str:
    return (
        text.replace("{marker}", marker)
        .replace("{token}", token)
        .replace("{workdir}", str(workdir))
    )


def tag(text: str, marker: str) -> str:
    """The prompt as sent: the text plus a line that lets any backend find the
    turn by free-text search."""
    return f"{text}\n({MARKER_PREFIX}: {marker})"


def render_expect(value: Any, marker: str) -> Any:
    """``{marker}`` substituted through an expectation block."""
    if isinstance(value, str):
        return value.replace("{marker}", marker)
    if isinstance(value, list):
        return [render_expect(v, marker) for v in value]
    if isinstance(value, dict):
        return {k: render_expect(v, marker) for k, v in value.items()}
    return value


# ── Overlays ─────────────────────────────────────────────────────────────


def merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class Overlay:
    """A scenario's ``config:`` (Hermes ``config.yaml``), ``enable_toolsets:``
    and ``otel:`` (the plugin's ``hermes_otel.yaml``) applied to a profile for
    the duration of a ``with`` block; both files are put back byte for byte
    afterwards (the agent may edit its own config, the restore covers that)."""

    def __init__(self, profile: str, scenario: Dict[str, Any]) -> None:
        self.home = profile_home(profile)
        self.config = scenario.get("config") or {}
        self.enable = list(scenario.get("enable_toolsets") or [])
        self.otel = scenario.get("otel") or {}
        self._saved: Dict[Path, Optional[str]] = {}

    def _apply(self, path: Path, change) -> None:
        yaml = yaml_mod()
        original = path.read_text(encoding="utf-8") if path.exists() else None
        self._saved[path] = original
        cfg = (yaml.safe_load(original) if original else None) or {}
        path.write_text(yaml.safe_dump(change(cfg), sort_keys=False), encoding="utf-8")

    def __enter__(self) -> "Overlay":
        if self.config or self.enable:

            def hermes_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
                cfg = merge(cfg, copy.deepcopy(self.config))
                if self.enable:
                    toolsets = list(cfg.get("toolsets") or [])
                    cfg["toolsets"] = toolsets + [t for t in self.enable if t not in toolsets]
                    agent = cfg.setdefault("agent", {})
                    agent["disabled_toolsets"] = [
                        t for t in (agent.get("disabled_toolsets") or []) if t not in self.enable
                    ]
                return cfg

            self._apply(self.home / "config.yaml", hermes_cfg)
        if self.otel:
            self._apply(
                self.home / "hermes_otel.yaml", lambda cfg: merge(cfg, copy.deepcopy(self.otel))
            )
        return self

    def __exit__(self, *exc: Any) -> None:
        for path, original in self._saved.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original, encoding="utf-8")
