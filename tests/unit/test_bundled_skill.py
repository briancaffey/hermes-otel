"""The plugin self-registers a bundled 'observability' Hermes skill."""

from pathlib import Path

import hermes_otel


class FakeCtx:
    """Minimal PluginContext stand-in recording hook/skill registrations."""

    def __init__(self, support_skills: bool = True):
        self.hooks = []
        self.skills = []
        self._support_skills = support_skills

    def register_hook(self, name, callback):
        self.hooks.append(name)

    # Omitting this entirely (support_skills=False) mimics an older Hermes
    # whose PluginContext has no register_skill — register() must not break.
    def register_skill(self, name, path, description=""):
        if not self._support_skills:
            raise AttributeError("register_skill")
        self.skills.append((name, Path(path), description))


def _enabled_tracer(monkeypatch):
    class _T:
        is_enabled = True

        def init(self):
            return True

    monkeypatch.setattr("hermes_otel.tracer.get_tracer", lambda: _T())


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


def test_register_is_forward_compatible_without_register_skill(monkeypatch):
    _enabled_tracer(monkeypatch)
    ctx = FakeCtx(support_skills=False)
    # Must not raise even though register_skill blows up.
    hermes_otel.register(ctx)
    assert ctx.skills == []
    # Hooks still registered — skill failure doesn't abort registration.
    assert "pre_tool_call" in ctx.hooks
