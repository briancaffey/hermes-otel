"""Tests for profile-scoped debug logging."""

from hermes_otel import debug_utils


def test_debug_log_uses_active_profile_home(monkeypatch, tmp_path):
    profile_home = tmp_path / "profiles" / "work"
    log_dir = profile_home / "plugins" / "hermes_otel"
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(debug_utils, "_DEBUG_ENABLED", True)
    monkeypatch.setattr(debug_utils, "active_hermes_home", lambda: profile_home)

    debug_utils.debug_log("profile-scoped")

    assert (log_dir / "debug.log").read_text() == "profile-scoped\n"
