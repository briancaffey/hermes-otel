"""Which config.yaml the plugin reads, and why the location matters.

`hermes plugins update` cannot update a subdirectory install (the installed
directory is a plain copy with no `.git`), so upgrading means
`hermes plugins install ... --force`, which replaces the plugin directory
wholesale. A config kept inside that directory does not survive it. Hence a
durable location outside the plugin directory, plus an explicit env override.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_otel import plugin_config as pc

CONFIG = "project_name: from-{}\n"


@pytest.fixture
def locations(tmp_path, monkeypatch):
    """Point every candidate location at a temp dir; create none of them."""
    home = tmp_path / "hermes-home"
    (home / "plugins" / "hermes_otel").mkdir(parents=True)
    durable = home / "hermes_otel.yaml"
    legacy = home / "plugins" / "hermes_otel" / "config.yaml"
    monkeypatch.setattr(pc, "DURABLE_CONFIG_PATH", durable)
    monkeypatch.setattr(pc, "DEFAULT_CONFIG_PATH", legacy)
    monkeypatch.delenv(pc.CONFIG_PATH_ENV, raising=False)
    return {"home": home, "durable": durable, "legacy": legacy, "tmp": tmp_path}


class TestResolveConfigPath:
    def test_no_file_anywhere_resolves_to_none(self, locations):
        assert pc.resolve_config_path() is None

    def test_legacy_plugin_dir_still_read(self, locations):
        locations["legacy"].write_text(CONFIG.format("legacy"))
        assert pc.resolve_config_path() == locations["legacy"]

    def test_durable_location_wins_over_legacy(self, locations):
        locations["legacy"].write_text(CONFIG.format("legacy"))
        locations["durable"].write_text(CONFIG.format("durable"))
        assert pc.resolve_config_path() == locations["durable"]

    def test_env_override_wins_over_both(self, locations, monkeypatch):
        explicit = locations["tmp"] / "elsewhere.yaml"
        explicit.write_text(CONFIG.format("env"))
        locations["legacy"].write_text(CONFIG.format("legacy"))
        locations["durable"].write_text(CONFIG.format("durable"))
        monkeypatch.setenv(pc.CONFIG_PATH_ENV, str(explicit))
        assert pc.resolve_config_path() == explicit

    def test_env_override_is_returned_even_when_missing(self, locations, monkeypatch):
        """An explicit path that does not exist should not silently fall back."""
        missing = locations["tmp"] / "typo.yaml"
        locations["durable"].write_text(CONFIG.format("durable"))
        monkeypatch.setenv(pc.CONFIG_PATH_ENV, str(missing))
        assert pc.resolve_config_path() == missing

    def test_env_override_expands_user(self, locations, monkeypatch):
        monkeypatch.setenv(pc.CONFIG_PATH_ENV, "~/some-config.yaml")
        assert pc.resolve_config_path() == Path.home() / "some-config.yaml"

    def test_blank_env_override_is_ignored(self, locations, monkeypatch):
        locations["durable"].write_text(CONFIG.format("durable"))
        monkeypatch.setenv(pc.CONFIG_PATH_ENV, "   ")
        assert pc.resolve_config_path() == locations["durable"]

    def test_shadowed_legacy_copy_is_reported(self, locations, caplog):
        locations["legacy"].write_text(CONFIG.format("legacy"))
        locations["durable"].write_text(CONFIG.format("durable"))
        with caplog.at_level("WARNING"):
            pc.resolve_config_path()
        assert "ignoring the copy in the plugin directory" in caplog.text


class TestHermesHome:
    def test_defaults_to_dot_hermes_in_home(self, monkeypatch):
        monkeypatch.delenv("HERMES_HOME", raising=False)
        assert pc.hermes_home() == Path.home() / ".hermes"

    def test_honours_hermes_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "custom"))
        assert pc.hermes_home() == tmp_path / "custom"

    def test_expands_user_in_hermes_home(self, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", "~/elsewhere")
        assert pc.hermes_home() == Path.home() / "elsewhere"

    def test_blank_hermes_home_falls_back(self, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", "  ")
        assert pc.hermes_home() == Path.home() / ".hermes"


class TestLoadConfigUsesResolution:
    def test_loads_from_durable_location(self, locations):
        locations["durable"].write_text("project_name: durable-project\n")
        assert pc.load_config().project_name == "durable-project"

    def test_loads_from_legacy_location(self, locations):
        locations["legacy"].write_text("project_name: legacy-project\n")
        assert pc.load_config().project_name == "legacy-project"

    def test_explicit_path_argument_still_wins(self, locations, monkeypatch):
        explicit = locations["tmp"] / "explicit.yaml"
        explicit.write_text("project_name: explicit-project\n")
        monkeypatch.setenv(pc.CONFIG_PATH_ENV, str(locations["durable"]))
        locations["durable"].write_text("project_name: durable-project\n")
        assert pc.load_config(path=explicit).project_name == "explicit-project"

    def test_no_config_anywhere_yields_defaults(self, locations):
        assert pc.load_config().project_name == pc.HermesOtelConfig().project_name
