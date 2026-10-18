"""The dashboard reads the plugin config through the tracer's own resolver
(``plugin_config.resolve_config_path_with_reason``), so the Traces tab can
never resolve to a file the tracer does not read (#413, #519).
"""

from __future__ import annotations

import logging

import pytest

from hermes_otel import plugin_config as pc
from hermes_otel.dashboard import backends as dash_backends


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_OTEL_CONFIG", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    # Derive both locations from HERMES_HOME (the session conftest pins them).
    monkeypatch.setattr(pc, "DURABLE_CONFIG_PATH", None)
    monkeypatch.setattr(pc, "DEFAULT_CONFIG_PATH", None)
    dash_backends.clear_caches()
    yield
    dash_backends.clear_caches()


def test_prefers_durable_hermes_home_file(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    durable = home / "hermes_otel.yaml"
    durable.write_text("backends:\n  - type: phoenix\n    endpoint: http://x:6006/v1/traces\n")
    legacy = home / "plugins" / "hermes_otel" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("backends: []\n")

    cfg_path, backends, _pin = dash_backends.load_config()
    assert cfg_path == durable == pc.resolve_config_path()
    assert [b["type"] for b in backends] == ["phoenix"]


def test_falls_back_to_legacy_plugin_dir_file(tmp_path):
    legacy = tmp_path / "home" / "plugins" / "hermes_otel" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("backends: []\n")
    assert dash_backends.load_config()[0] == legacy


def test_env_override_wins(monkeypatch, tmp_path):
    explicit = tmp_path / "elsewhere.yaml"
    explicit.write_text("backends: []\n")
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "hermes_otel.yaml").write_text("backends: []\n")
    monkeypatch.setenv("HERMES_OTEL_CONFIG", str(explicit))
    assert dash_backends.candidate_config_paths()[0] == explicit
    assert dash_backends.load_config()[0] == explicit


def test_nothing_found(tmp_path):
    assert dash_backends.load_config() == (None, [], None)
    assert dash_backends.candidate_config_paths()[0] == tmp_path / "home" / "hermes_otel.yaml"


def test_candidates_are_exactly_the_tracer_s(tmp_path):
    # The dashboard used to add a ``config.yaml`` beside its own package, a file
    # the tracer never reads (#413).
    home = tmp_path / "home"
    assert (
        dash_backends.candidate_config_paths()
        == [
            pc.durable_config_path(),
            pc.legacy_config_path(),
        ]
        == [home / "hermes_otel.yaml", home / "plugins" / "hermes_otel" / "config.yaml"]
    )


def test_two_files_present_warns_once(tmp_path, caplog):
    # The dashboard resolves the path on every poll; the tracer's resolver
    # warns about a shadowed legacy copy once per pair, not per call.
    home = tmp_path / "home"
    durable = home / "hermes_otel.yaml"
    legacy = home / "plugins" / "hermes_otel" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    durable.write_text("backends: []\n")
    legacy.write_text("backends: []\n")
    pc._WARNED_SHADOWED.clear()
    with caplog.at_level(logging.WARNING, logger="hermes_otel"):
        assert dash_backends.load_config()[0] == durable
        dash_backends.clear_caches()
        assert dash_backends.load_config()[0] == durable
    warnings = [r.getMessage() for r in caplog.records if "ignoring" in r.getMessage()]
    assert len(warnings) == 1 and str(legacy) in warnings[0]
