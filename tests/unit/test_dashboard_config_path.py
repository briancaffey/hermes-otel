"""The dashboard must resolve the plugin config from the same places the
tracer does, or the Traces tab's query backend silently resolves to nothing.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# ``fastapi`` is a dashboard-side runtime dependency, not a plugin/test one
# (see test_dashboard_backends_roots_only.py) — stub the one symbol imported.
if "fastapi" not in sys.modules:
    _fastapi_stub = types.ModuleType("fastapi")

    class _StubHTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    _fastapi_stub.HTTPException = _StubHTTPException  # type: ignore[attr-defined]
    sys.modules["fastapi"] = _fastapi_stub

_DASHBOARD = Path(__file__).resolve().parent.parent.parent / "hermes_otel" / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

import backends as dash_backends  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_OTEL_CONFIG", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    # Keep the legacy in-plugin config.yaml out of the picture.
    monkeypatch.setattr(
        dash_backends,
        "__file__",
        str(tmp_path / "plugin" / "dashboard" / "backends" / "__init__.py"),
    )
    yield


def test_prefers_durable_hermes_home_file(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    durable = home / "hermes_otel.yaml"
    durable.write_text("backends:\n  - type: phoenix\n    endpoint: http://x:6006/v1/traces\n")
    legacy = home / "plugins" / "hermes_otel" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("backends: []\n")

    assert dash_backends.resolve_config_path() == durable
    cfg_path, backends, _pin = dash_backends.load_config()
    assert cfg_path == durable
    assert [b["type"] for b in backends] == ["phoenix"]


def test_falls_back_to_legacy_plugin_dir_file(tmp_path):
    legacy = tmp_path / "home" / "plugins" / "hermes_otel" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("backends: []\n")
    assert dash_backends.resolve_config_path() == legacy


def test_env_override_wins(monkeypatch, tmp_path):
    explicit = tmp_path / "elsewhere.yaml"
    explicit.write_text("backends: []\n")
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "hermes_otel.yaml").write_text("backends: []\n")
    monkeypatch.setenv("HERMES_OTEL_CONFIG", str(explicit))
    assert dash_backends.candidate_config_paths()[0] == explicit
    assert dash_backends.resolve_config_path() == explicit


def test_nothing_found(tmp_path):
    assert dash_backends.resolve_config_path() is None
    assert dash_backends.candidate_config_paths()[0] == tmp_path / "home" / "hermes_otel.yaml"
