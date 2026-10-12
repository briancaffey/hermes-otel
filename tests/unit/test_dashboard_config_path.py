"""The dashboard must resolve the plugin config from the same places the
tracer does, or the Traces tab's query backend silently resolves to nothing.
"""

from __future__ import annotations

import pytest

from hermes_otel.dashboard import backends as dash_backends


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


def test_candidates_are_exactly_the_tracer_s(tmp_path):
    # The dashboard used to add a ``config.yaml`` beside its own package, a file
    # the tracer never reads (#413).
    home = tmp_path / "home"
    assert dash_backends.candidate_config_paths() == [
        home / "hermes_otel.yaml",
        home / "plugins" / "hermes_otel" / "config.yaml",
    ]


def test_two_files_present_warns_once(tmp_path, caplog):
    import logging

    home = tmp_path / "home"
    durable = home / "hermes_otel.yaml"
    legacy = home / "plugins" / "hermes_otel" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    durable.write_text("backends: []\n")
    legacy.write_text("backends: []\n")
    dash_backends._WARNED_TWO_FILES.clear()
    with caplog.at_level(logging.WARNING, logger="hermes_otel"):
        assert dash_backends.resolve_config_path() == durable
        assert dash_backends.resolve_config_path() == durable
    warnings = [r.getMessage() for r in caplog.records if "ignoring" in r.getMessage()]
    assert len(warnings) == 1 and str(legacy) in warnings[0]
