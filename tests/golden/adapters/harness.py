"""The adapter goldens (#408): one fixed set of scenarios run against every
dashboard adapter with canned backend responses, the outputs recorded.

Layout, per adapter type under ``tests/golden/adapters/<type>/``:

* ``config.json``  — the backend entry the adapter is built from, plus the
  ids and names the scenarios use (``t0``, ``trace_id``, ``session_id``,
  ``model``, ``counter``, ``gauge``, ``group_by``; ``metrics`` / ``logs``
  false when the entry declares no such signal).
* ``routes.json``  — the scripted server (see ``helpers.wire.FakeWire``).
* ``expected/<scenario>.json`` — what the adapter answered and what it asked
  (``calls``), normalised. ``UPDATE_GOLDEN=1 uv run --extra dev pytest
  tests/unit/test_dashboard_goldens.py`` rewrites them; a refactor PR never
  changes one silently.

``seed_responses.py`` writes the first two from the shapes the unit tests
recorded against real backends; the third is recorded by the test itself.
"""

from __future__ import annotations

import difflib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from helpers.wire import FakeWire

from hermes_otel.dashboard import backends
from hermes_otel.dashboard.backends.base import (
    BackendError,
    LogFilter,
    StructuredFilter,
    filter_support_of,
)

ROOT = Path(__file__).resolve().parent
NS = 1_000_000_000
MISSING = "0" * 32


def adapter_dir(backend_type: str) -> Path:
    return ROOT / backend_type


def recorded_types() -> List[str]:
    return sorted(p.name for p in ROOT.iterdir() if (p / "config.json").exists())


def load(backend_type: str) -> Dict[str, Any]:
    d = adapter_dir(backend_type)
    cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    cfg["routes"] = json.loads((d / "routes.json").read_text(encoding="utf-8"))
    return cfg


def build(cfg: Dict[str, Any]):
    entry = dict(cfg["entry"])
    cls = backends.find_adapter_class(entry["type"])
    assert cls is not None, entry["type"]
    return cls(entry)


# ── scenarios ──────────────────────────────────────────────────────────

Scenario = Callable[[Any, Dict[str, Any]], Any]


def _window(cfg: Dict[str, Any]):
    t0 = int(cfg["t0"])
    return t0 - 3600, t0 + 60


def _search(f: StructuredFilter, limit: int = 5) -> Scenario:
    def run(a, cfg):
        s, e = _window(cfg)
        return a.search(f, s, e, limit)

    return run


def _scenarios(cfg: Dict[str, Any]) -> Dict[str, Scenario]:
    t0 = int(cfg["t0"])
    trace = cfg["trace_id"]
    sc: Dict[str, Scenario] = {
        "status": lambda a, c: a.status(),
        "filter_support": lambda a, c: filter_support_of(a),
        "trace_url": lambda a, c: a.trace_url(trace),
        "search_default": _search(StructuredFilter()),
        "search_session": _search(
            StructuredFilter(attr_equals={"hermes.session_id": cfg["session_id"]})
        ),
        "search_model": _search(StructuredFilter(attr_equals={"llm.model_name": cfg["model"]})),
        "search_status_error": _search(StructuredFilter(status="error")),
        "search_status_ok": _search(StructuredFilter(status="ok")),
        "search_name_regex": _search(StructuredFilter(name_regex="agent")),
        "search_min_duration": _search(StructuredFilter(min_duration_ms=1000)),
        "search_cursor": _search(StructuredFilter(before_ns=t0 * NS + 1), limit=1),
        "search_widened_tools": _search(StructuredFilter(name_prefix="tool.", roots_only=False)),
        "search_free_text": _search(StructuredFilter(free_text="hello")),
        "get_trace_found": lambda a, c: a.get_trace(trace),
        "get_trace_missing": lambda a, c: a.get_trace(MISSING),
    }
    if cfg.get("metrics", True):
        sc["metric_names"] = lambda a, c: a.metric_names(*_window(c))
        sc["metrics_query_counter"] = lambda a, c: a.metrics_query(
            c["counter"], *_window(c), 300, group_by=c.get("group_by"), agg="sum"
        )
        sc["metrics_query_gauge"] = lambda a, c: a.metrics_query(
            c["gauge"], *_window(c), 300, agg="max"
        )
    if cfg.get("logs", True):
        sc["logs_search_default"] = lambda a, c: a.logs_search(LogFilter(), *_window(c), 50)
        sc["logs_search_level_text"] = lambda a, c: a.logs_search(
            LogFilter(min_level=30, text="careful"), *_window(c), 50
        )
        sc["logs_search_trace"] = lambda a, c: a.logs_search(
            LogFilter(trace_id=trace), *_window(c), 50
        )
        sc["loggers"] = lambda a, c: a.loggers(*_window(c))
    return sc


ERROR_SCENARIOS = {
    "error_401": [{"status": 401, "text": '{"error": "bad token"}'}],
    "error_404": [{"status": 404, "text": "404 page not found"}],
    "error_500": [{"status": 500, "text": "internal error"}],
    "error_timeout": [{"raise": "timeout"}],
    "error_unreachable": [{"raise": "unreachable"}],
}


def scenario_names(cfg: Dict[str, Any]) -> List[str]:
    return list(_scenarios(cfg)) + list(ERROR_SCENARIOS)


ALL_SCENARIO_NAMES = scenario_names({"t0": 0, "trace_id": "", "session_id": "", "model": ""})


def applies(cfg: Dict[str, Any], name: str) -> bool:
    return name in scenario_names(cfg)


# ── running and normalising ────────────────────────────────────────────


def _normalise(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    return value


def _error(exc: BackendError) -> Dict[str, Any]:
    return {
        "kind": getattr(exc, "kind", "backend"),
        "status_code": exc.status_code,
        "detail": str(exc.detail),
    }


def run(backend_type: str, name: str, monkeypatch) -> Dict[str, Any]:
    """One scenario on a fresh adapter with the scripted server: the answer
    (or the BackendError) plus every request the adapter made."""
    from helpers.dashboard import isolate_backends

    cfg = load(backend_type)
    isolate_backends(monkeypatch)
    # The clock the adapters read for "now" (trace windows, probes, UI links).
    monkeypatch.setattr(time, "time", lambda: float(int(cfg["t0"]) + 60))
    if name in ERROR_SCENARIOS:
        wire = FakeWire(ERROR_SCENARIOS[name]).install(monkeypatch)
        a = build(cfg)
        out: Dict[str, Any] = {}
        for label, fn in (
            ("search", lambda: a.search(StructuredFilter(), *_window(cfg), 5)),
            ("get_trace", lambda: a.get_trace(cfg["trace_id"])),
        ):
            try:
                out[label] = {"result": _normalise(fn())}
            except BackendError as exc:
                out[label] = {"error": _error(exc)}
        return {"outcomes": out, "calls": _normalise(wire.calls)}
    wire = FakeWire(cfg["routes"]).install(monkeypatch)
    a = build(cfg)
    try:
        result = {"result": _normalise(_scenarios(cfg)[name](a, cfg))}
    except BackendError as exc:
        result = {"error": _error(exc)}
    result["calls"] = _normalise(wire.calls)
    return result


def dumps(value: Any) -> str:
    return json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def expected_path(backend_type: str, name: str) -> Path:
    return adapter_dir(backend_type) / "expected" / f"{name}.json"


def check(backend_type: str, name: str, actual: Dict[str, Any]) -> Optional[str]:
    """``None`` when the recorded fixture matches, else a unified diff. With
    ``UPDATE_GOLDEN=1`` the fixture is (re)written and the check passes."""
    path = expected_path(backend_type, name)
    text = dumps(actual)
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return None
    if not path.exists():
        return (
            f"no golden for {backend_type}/{name}: record it with\n"
            f"  UPDATE_GOLDEN=1 uv run --extra dev pytest tests/unit/test_dashboard_goldens.py "
            f"-k '{backend_type} and {name}'"
        )
    recorded = path.read_text(encoding="utf-8")
    if recorded == text:
        return None
    diff = difflib.unified_diff(
        recorded.splitlines(), text.splitlines(), fromfile=str(path), tofile="actual", lineterm=""
    )
    return "\n".join(diff)
