"""Every dashboard adapter against its recorded backend responses (#408).

One fixed scenario list, run through ``tests/golden/adapters/harness.py``;
the answer and the requests made are compared with the committed fixture.
A new adapter fails here until its fixtures are recorded; a refactor that
changes an answer or a request fails with a diff. ``UPDATE_GOLDEN=1`` rewrites.
"""

from __future__ import annotations

import pytest
from golden.adapters import harness

from hermes_otel.dashboard import backends


def _types():
    return sorted(sorted(cls.handles)[0] for cls in backends.adapters())


@pytest.mark.parametrize("backend_type", _types())
def test_every_adapter_has_recorded_fixtures(backend_type):
    assert backend_type in harness.recorded_types(), (
        f"no golden fixtures for {backend_type!r}: add tests/golden/adapters/{backend_type}/"
        "config.json and routes.json (see tests/golden/adapters/seed_responses.py), then record "
        "with UPDATE_GOLDEN=1 uv run --extra dev pytest tests/unit/test_dashboard_goldens.py "
        f"-k {backend_type}"
    )


@pytest.mark.parametrize("scenario", harness.ALL_SCENARIO_NAMES)
@pytest.mark.parametrize("backend_type", _types())
def test_golden(backend_type, scenario, monkeypatch):
    if backend_type not in harness.recorded_types():
        pytest.skip("no fixtures yet (test_every_adapter_has_recorded_fixtures fails)")
    cfg = harness.load(backend_type)
    if not harness.applies(cfg, scenario):
        pytest.skip(f"{backend_type} declares no {scenario.split('_')[0]} support")
    actual = harness.run(backend_type, scenario, monkeypatch)
    diff = harness.check(backend_type, scenario, actual)
    assert diff is None, diff
