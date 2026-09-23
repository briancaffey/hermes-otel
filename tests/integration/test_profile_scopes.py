"""Two profiles in one process, the way a multiplexed gateway loads plugins (#70).

Hermes keeps one plugin manager per home and imports a directory plugin once
per manager, binding discovery and every request to that profile's home via a
context-local override (``hermes_constants.get_hermes_home``). This test does
the same: it imports the package twice under two module names, registers each
copy inside a different home scope, runs a turn through each, and checks that
each profile got its own config, live store and debug log, and that its spans
and metrics carry its own name.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import pytest

import hermes_otel
from hermes_otel.hermes_home import profile_name_for
from hermes_otel.live_store import LiveStore

PKG = Path(hermes_otel.__file__).resolve().parent


class FakeCtx:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_skill(self, name, path, description=""):
        pass


def _load_copy(module_name: str):
    """Import ``hermes_otel`` as a fresh package under ``module_name`` (its own
    singletons), exactly as Hermes's per-profile loader does."""
    parent = module_name.rpartition(".")[0]
    if parent and parent not in sys.modules:
        # Hermes creates the ``hermes_plugins`` namespace package the same way.
        ns = types.ModuleType(parent)
        ns.__path__ = []  # type: ignore[attr-defined]
        ns.__package__ = parent
        sys.modules[parent] = ns
    spec = importlib.util.spec_from_file_location(
        module_name, PKG / "__init__.py", submodule_search_locations=[str(PKG)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _evict(module_name: str) -> None:
    for name in [n for n in sys.modules if n == module_name or n.startswith(module_name + ".")]:
        del sys.modules[name]


@pytest.fixture()
def fake_hermes(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    for p in ("profiles/alpha", "profiles/beta"):
        (root / p).mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("HERMES_OTEL_DEBUG", "true")
    for var in list(os_environ_backends()):
        monkeypatch.delenv(var, raising=False)
    override: ContextVar = ContextVar("home", default=None)

    consts = types.ModuleType("hermes_constants")
    consts.get_hermes_home = lambda: Path(override.get() or root)
    profiles = types.ModuleType("hermes_cli.profiles")
    profiles.get_active_profile_name = lambda: profile_name_for(consts.get_hermes_home(), root)
    pkg = types.ModuleType("hermes_cli")
    pkg.profiles = profiles
    monkeypatch.setitem(sys.modules, "hermes_constants", consts)
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", profiles)

    @contextmanager
    def scope(name: str):
        token = override.set(str(root / "profiles" / name))
        try:
            yield root / "profiles" / name
        finally:
            override.reset(token)

    return root, scope


def os_environ_backends():
    import os

    return [
        k
        for k in os.environ
        if k.startswith(("OTEL_", "HERMES_OTEL_")) and k != "HERMES_OTEL_DEBUG"
    ]


def _turn(mod, session_id: str) -> None:
    mod.hooks.on_session_start(session_id=session_id, model="m", platform="cli")
    mod.hooks.on_pre_api_request(
        session_id=session_id, model="m", request_id=f"r-{session_id}", request={"messages": []}
    )
    mod.hooks.on_post_api_request(
        session_id=session_id,
        model="m",
        request_id=f"r-{session_id}",
        usage={"prompt_tokens": 10, "completion_tokens": 2},
        response={},
    )
    mod.hooks.on_session_end(
        session_id=session_id, completed=True, interrupted=False, model="m", platform="cli"
    )


def test_two_profiles_in_one_process(fake_hermes):
    root, scope = fake_hermes
    names = ("hermes_plugins.hermes_otel", "hermes_plugins.hermes_otel__home_test")
    copies = {}
    try:
        for profile, module_name in zip(("alpha", "beta"), names):
            with scope(profile) as home:
                # per-profile config, as a user would write it
                (home / "hermes_otel.yaml").write_text(f"project_name: proj-{profile}\n")
                mod = _load_copy(module_name)
                mod.register(FakeCtx())
                copies[profile] = mod

        tracers = {p: m.tracer.get_tracer() for p, m in copies.items()}
        assert tracers["alpha"] is not tracers["beta"]
        assert tracers["alpha"].hermes_home == root / "profiles" / "alpha"
        assert tracers["beta"].hermes_home == root / "profiles" / "beta"
        assert tracers["alpha"].profile_name == "alpha"
        assert tracers["beta"].profile_name == "beta"
        # each copy read its own profile's config file
        assert tracers["alpha"].config.project_name == "proj-alpha"
        assert tracers["beta"].config.project_name == "proj-beta"

        for profile, mod in copies.items():
            with scope(profile):
                _turn(mod, f"s-{profile}")
            mod.tracer.get_tracer()._force_flush()

        for profile in ("alpha", "beta"):
            home = root / "profiles" / profile
            assert (home / "plugins" / "hermes_otel" / "debug.log").is_file(), profile
            db = home / "hermes_otel_live.db"
            assert db.is_file(), profile
            store = LiveStore(db_path=str(db))
            try:
                spans = store.spans()
                metrics = store.metrics()
            finally:
                store.close()
            root_span = next(s for s in spans if s["name"] == "agent")
            assert root_span["attributes"]["hermes.profile"] == profile
            assert root_span["attributes"]["hermes.session_id"] == f"s-{profile}"
            # nothing from the other profile leaked into this store
            assert {
                s["attributes"].get("hermes.session_id") for s in spans if s["name"] == "agent"
            } == {f"s-{profile}"}
            assert metrics and all(m["attributes"].get("profile") == profile for m in metrics)
        # the default home got nothing: no store, no debug log
        assert not (root / "hermes_otel_live.db").exists()
        assert not (root / "plugins" / "hermes_otel" / "debug.log").exists()
    finally:
        for mod in copies.values():
            try:
                mod.tracer.get_tracer().shutdown()
            except Exception:
                pass
        for module_name in names:
            _evict(module_name)
        sys.modules.pop("hermes_plugins", None)
