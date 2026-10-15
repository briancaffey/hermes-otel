"""Characterization of the hook layer (#407): every scenario in
``tests/golden/record_hooks.py``, under every config, must export exactly the
spans, metric points and events recorded in ``tests/golden/hooks/``.

``UPDATE_GOLDEN=1`` rewrites the fixtures. A refactor PR may not change one
without listing every changed line (CONTRIBUTING.md, "Golden fixtures").
"""

from __future__ import annotations

import os

import pytest
from golden.record_hooks import CONFIGS, SCENARIOS, fixture_path, record
from helpers.golden import diff, dumps

CASES = [
    pytest.param(scenario, config, id=f"{scenario}-{config}")
    for scenario in SCENARIOS
    for config in CONFIGS
]


@pytest.fixture(autouse=True)
def _stable_environment(monkeypatch):
    # Nothing a developer's shell exports may reach a recording.
    for var in (
        "HERMES_KANBAN_TASK",
        "HERMES_KANBAN_BOARD",
        "HERMES_KANBAN_RUN_ID",
        "HERMES_PROFILE",
        "HERMES_OTEL_TRACEPARENT",
        "HERMES_OTEL_TRACESTATE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HERMES_REDACT_SECRETS", "true")


@pytest.mark.parametrize("scenario,config", CASES)
def test_golden(scenario, config):
    path = fixture_path(scenario, config)
    actual = dumps(record(scenario, config))
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    assert path.exists(), f"no golden for {path.name}; record with UPDATE_GOLDEN=1"
    expected = path.read_text(encoding="utf-8")
    if expected != actual:
        pytest.fail(
            f"{path.name} changed; if the change is deliberate, re-record with UPDATE_GOLDEN=1 "
            f"and list every changed line in the PR:\n" + diff(expected, actual, path.name),
            pytrace=False,
        )


def test_recording_is_deterministic():
    """Two recordings of the same scenario agree byte for byte."""
    a = dumps(record("one_shot_turn", "full"))
    b = dumps(record("one_shot_turn", "full"))
    assert a == b
