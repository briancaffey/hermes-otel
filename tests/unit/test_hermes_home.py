"""Profile-scoped home and profile name resolution (#70).

The plugin asks Hermes's scope-aware resolver first (a multiplexed gateway
binds each profile's plugin to that profile's home through a context-local
override, not the environment) and falls back to ``$HERMES_HOME`` /
``~/.hermes`` when Hermes is not importable.
"""

from __future__ import annotations

import sys
import types
from contextvars import ContextVar
from pathlib import Path

import pytest

from hermes_otel import hermes_home as hh
from hermes_otel.hooks.attributes import _profile_attributes


@pytest.fixture()
def no_hermes(monkeypatch):
    """Hermes not importable: ``import hermes_constants`` raises."""
    monkeypatch.setitem(sys.modules, "hermes_constants", None)
    monkeypatch.setitem(sys.modules, "hermes_cli", None)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", None)


@pytest.fixture()
def fake_hermes(monkeypatch, tmp_path):
    """A stand-in for Hermes: a context-local home override and the real
    profile-name derivation, keyed on ``HERMES_HOME`` as the root."""
    root = tmp_path / "root"
    (root / "profiles" / "alpha").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    override: ContextVar = ContextVar("home", default=None)

    consts = types.ModuleType("hermes_constants")
    consts.get_hermes_home = lambda: Path(override.get() or root)
    consts.override = override
    profiles = types.ModuleType("hermes_cli.profiles")
    profiles.get_active_profile_name = lambda: hh.profile_name_for(consts.get_hermes_home(), root)
    pkg = types.ModuleType("hermes_cli")
    pkg.profiles = profiles
    monkeypatch.setitem(sys.modules, "hermes_constants", consts)
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", profiles)
    return root, override


class TestProfileNameFor:
    def test_default_home(self, tmp_path):
        root = tmp_path / "h"
        root.mkdir()
        assert hh.profile_name_for(root, root) == "default"

    def test_named_profile(self, tmp_path):
        root = tmp_path / "h"
        (root / "profiles" / "coder-2").mkdir(parents=True)
        assert hh.profile_name_for(root / "profiles" / "coder-2", root) == "coder-2"

    def test_custom_paths(self, tmp_path):
        root = tmp_path / "h"
        (root / "profiles" / "a" / "nested").mkdir(parents=True)
        (tmp_path / "elsewhere").mkdir()
        assert hh.profile_name_for(tmp_path / "elsewhere", root) == "custom"
        assert hh.profile_name_for(root / "profiles" / "a" / "nested", root) == "custom"
        (root / "profiles" / "Bad Name").mkdir()
        assert hh.profile_name_for(root / "profiles" / "Bad Name", root) == "custom"

    def test_root_defaults_to_process_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "profiles" / "p").mkdir(parents=True)
        assert hh.profile_name_for(tmp_path / "profiles" / "p") == "p"
        assert hh.profile_name_for(tmp_path) == "default"


class TestResolveWithoutHermes:
    def test_env_and_default(self, no_hermes, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert hh.resolve_hermes_home() == tmp_path
        assert hh.resolve_profile_name() == "default"
        monkeypatch.delenv("HERMES_HOME")
        assert hh.resolve_hermes_home() == Path.home() / ".hermes"

    def test_named_profile_from_env(self, no_hermes, monkeypatch, tmp_path):
        # `hermes -p x` runs the CLI with HERMES_HOME=<root>/profiles/x; the
        # root is not knowable from that alone, so the name comes from the path.
        (tmp_path / "profiles" / "x").mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "x"))
        # Hermes is not importable, so the profiles root is unknown; a home
        # whose parent is ``profiles`` is still read as that profile.
        assert hh.resolve_profile_name() == "x"
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert hh.resolve_profile_name() == "default"


class TestResolveWithHermes:
    def test_scope_override_wins_over_env(self, fake_hermes):
        root, override = fake_hermes
        assert hh.resolve_hermes_home() == root
        assert hh.resolve_profile_name() == "default"
        token = override.set(str(root / "profiles" / "alpha"))
        try:
            assert hh.resolve_hermes_home() == root / "profiles" / "alpha"
            assert hh.resolve_profile_name() == "alpha"
        finally:
            override.reset(token)
        assert hh.resolve_profile_name() == "default"

    def test_hermes_failures_fall_back(self, fake_hermes, monkeypatch, tmp_path):
        root, _ = fake_hermes

        def boom():
            raise RuntimeError("no scope")

        sys.modules["hermes_constants"].get_hermes_home = boom
        sys.modules["hermes_cli.profiles"].get_active_profile_name = boom
        assert hh.resolve_hermes_home() == root  # env fallback
        assert hh.resolve_profile_name() == "default"
        sys.modules["hermes_constants"].get_hermes_home = lambda: None
        assert hh.resolve_hermes_home() == root


class TestPluginPaths:
    """The config file, live store and debug log follow the resolved home."""

    def test_config_live_store_and_debug_log(self, fake_hermes):
        from hermes_otel import debug_utils, live_store, plugin_config

        root, override = fake_hermes
        alpha = root / "profiles" / "alpha"
        token = override.set(str(alpha))
        try:
            assert plugin_config.hermes_home() == alpha
            # (conftest redirects _default_db_path itself; the pure helper shows the mapping)
            assert live_store.default_db_path_for(plugin_config.hermes_home()) == str(
                alpha / "hermes_otel_live.db"
            )
            assert debug_utils.debug_log_path() == str(
                alpha / "plugins" / "hermes_otel" / "debug.log"
            )
        finally:
            override.reset(token)
        assert plugin_config.hermes_home() == root

    def test_dashboard_adapters_config_path(self, fake_hermes):
        dash = Path(hh.__file__).parent / "dashboard"
        if str(dash) not in sys.path:
            sys.path.insert(0, str(dash))
        import backends  # noqa: E402

        root, override = fake_hermes
        alpha = root / "profiles" / "alpha"
        token = override.set(str(alpha))
        try:
            assert alpha / "hermes_otel.yaml" in backends.candidate_config_paths()
            assert root / "hermes_otel.yaml" not in backends.candidate_config_paths()
        finally:
            override.reset(token)


class TestProfileOnTelemetry:
    def test_profile_attributes(self):
        class T:
            profile_name = "alpha"

        assert _profile_attributes(T()) == {"hermes.profile": "alpha"}
        assert _profile_attributes(object()) == {}

    def test_resource_and_metric_label(self, inmemory_otel_with_metrics):
        exporter, reader, plugin = inmemory_otel_with_metrics
        plugin.profile_name = "alpha"
        assert plugin._build_resource().attributes["hermes.profile"] == "alpha"
        plugin.record_metric("session_count", 1, {"platform": "cli"})
        data = reader.get_metrics_data()
        points = [
            p
            for rm in data.resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
            if m.name == "hermes.session.count"
            for p in m.data.data_points
        ]
        assert points and all(dict(p.attributes)["profile"] == "alpha" for p in points)
        assert dict(points[0].attributes)["platform"] == "cli"

    def test_root_span_carries_profile(self, inmemory_otel_setup):
        from hermes_otel import hooks

        exporter, plugin = inmemory_otel_setup
        plugin.profile_name = "beta"
        hooks.on_session_start(session_id="s-p", model="m", platform="cli")
        hooks.on_session_end(
            session_id="s-p", completed=True, interrupted=False, model="m", platform="cli"
        )
        root = next(s for s in exporter.get_finished_spans() if s.name == "agent")
        assert root.attributes["hermes.profile"] == "beta"
