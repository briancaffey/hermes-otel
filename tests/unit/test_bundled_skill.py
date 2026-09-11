"""The plugin self-registers a bundled 'observability' Hermes skill."""

from pathlib import Path

import hermes_otel


class FakeCtx:
    """Minimal PluginContext stand-in recording hook/skill registrations."""

    def __init__(self, support_skills: bool = True):
        self.hooks = []
        self.skills = []
        self.unload_callbacks = []
        self._support_skills = support_skills
        self.profile_name = "default"

    def register_hook(self, name, callback):
        self.hooks.append(name)

    # Omitting this entirely (support_skills=False) mimics an older Hermes
    # whose PluginContext has no register_skill — register() must not break.
    def register_skill(self, name, path, description=""):
        if not self._support_skills:
            raise AttributeError("register_skill")
        self.skills.append((name, Path(path), description))

    def on_unload(self, callback):
        self.unload_callbacks.append(callback)


def _enabled_tracer(monkeypatch):
    class _T:
        is_enabled = True

        def init(self):
            return True

        def shutdown(self):
            pass

    monkeypatch.setattr("hermes_otel.tracer.get_tracer", lambda profile_name=None: _T())


def test_bundled_skill_file_exists_and_parses():
    path = Path(hermes_otel.__file__).resolve().parent / "skills" / "observability" / "SKILL.md"
    assert path.exists(), "bundled observability SKILL.md missing"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: observability" in text


def test_register_registers_bundled_skill(monkeypatch):
    _enabled_tracer(monkeypatch)
    ctx = FakeCtx(support_skills=True)
    hermes_otel.register(ctx)
    names = [name for name, _path, _desc in ctx.skills]
    assert "observability" in names
    # The path it registered must actually exist.
    registered = next(p for n, p, _ in ctx.skills if n == "observability")
    assert registered.exists()
    assert len(ctx.unload_callbacks) == 1


def test_register_is_forward_compatible_without_register_skill(monkeypatch):
    _enabled_tracer(monkeypatch)
    ctx = FakeCtx(support_skills=False)
    # Must not raise even though register_skill blows up.
    hermes_otel.register(ctx)
    assert ctx.skills == []
    # Hooks still registered — skill failure doesn't abort registration.
    assert "pre_tool_call" in ctx.hooks


def test_register_passes_profile_name_to_tracer(monkeypatch):
    captured = {}

    class _T:
        is_enabled = False

        def init(self):
            return False

    def _get_tracer(profile_name=None):
        captured["profile_name"] = profile_name
        return _T()

    monkeypatch.setattr("hermes_otel.tracer.get_tracer", _get_tracer)
    ctx = FakeCtx()
    ctx.profile_name = "work"

    hermes_otel.register(ctx)

    assert captured["profile_name"] == "work"


def test_register_falls_back_for_older_hermes_context(monkeypatch):
    captured = {}

    class LegacyCtx:
        pass

    class _T:
        is_enabled = False

        def init(self):
            return False

    def _get_tracer(profile_name=None):
        captured["profile_name"] = profile_name
        return _T()

    monkeypatch.setattr("hermes_otel.tracer.get_tracer", _get_tracer)

    hermes_otel.register(LegacyCtx())

    assert captured["profile_name"] == "default"


def test_register_remains_compatible_without_profile_or_unload_api(monkeypatch):
    class LegacyCtx:
        def __init__(self):
            self.hooks = []

        def register_hook(self, name, callback):
            self.hooks.append(name)

    _enabled_tracer(monkeypatch)
    ctx = LegacyCtx()

    hermes_otel.register(ctx)

    assert "pre_tool_call" in ctx.hooks
