"""Profile compatibility helpers."""

from __future__ import annotations

import sys
import types
from pathlib import Path

from hermes_otel.profile_context import active_hermes_home, profile_name_from_context


def test_active_home_prefers_hermes_context(monkeypatch, tmp_path):
    profile_home = tmp_path / "profiles" / "work"
    hermes_constants = types.ModuleType("hermes_constants")
    hermes_constants.get_hermes_home = lambda: profile_home
    monkeypatch.setitem(sys.modules, "hermes_constants", hermes_constants)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "default"))

    assert active_hermes_home() == profile_home


def test_active_home_falls_back_to_environment(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "hermes_constants", None)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "custom"))

    assert active_hermes_home() == tmp_path / "custom"


def test_active_home_falls_back_to_default(monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_constants", None)
    monkeypatch.delenv("HERMES_HOME", raising=False)

    assert active_hermes_home() == Path.home() / ".hermes"


def test_profile_name_uses_host_context():
    ctx = types.SimpleNamespace(profile_name="work")

    assert profile_name_from_context(ctx) == "work"


def test_profile_name_falls_back_for_older_context():
    assert profile_name_from_context(object()) == "default"


def test_profile_name_falls_back_when_property_fails():
    class BrokenContext:
        @property
        def profile_name(self):
            raise RuntimeError("unsupported")

    assert profile_name_from_context(BrokenContext()) == "default"


def test_profile_name_falls_back_for_blank_value():
    ctx = types.SimpleNamespace(profile_name=" ")

    assert profile_name_from_context(ctx) == "default"
