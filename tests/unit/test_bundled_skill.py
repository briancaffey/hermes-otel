"""The plugin self-registers a bundled 'observability' Hermes skill."""

from pathlib import Path

import pytest

import hermes_otel


class FakeCtx:
    """Minimal PluginContext stand-in recording hook/skill registrations."""

    def __init__(self, support_skills: bool = True, support_prompt_sections: bool = True):
        self.hooks = []
        self.skills = []
        self.prompt_sections = []
        self._support_skills = support_skills
        self._support_prompt_sections = support_prompt_sections

    def register_hook(self, name, callback):
        self.hooks.append(name)

    # Omitting this entirely (support_skills=False) mimics an older Hermes
    # whose PluginContext has no register_skill — register() must not break.
    def register_skill(self, name, path, description=""):
        if not self._support_skills:
            raise AttributeError("register_skill")
        self.skills.append((name, Path(path), description))

    def register_system_prompt_section(self, section_id, content, **kwargs):
        if not self._support_prompt_sections:
            raise AttributeError("register_system_prompt_section")
        self.prompt_sections.append((section_id, content, kwargs))


class LegacyCtx:
    """Older Hermes context with neither skill nor prompt-section APIs."""

    def __init__(self):
        self.hooks = []

    def register_hook(self, name, callback):
        self.hooks.append(name)


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


def test_register_advertises_telemetry_as_behavioral_evidence(monkeypatch):
    _enabled_tracer(monkeypatch)
    ctx = FakeCtx()
    hermes_otel.register(ctx)

    [(section_id, content, options)] = ctx.prompt_sections
    assert section_id == "hermes-otel.discovery"
    assert "skill/tool usage" in content
    assert "hermes_otel:observability" in content
    assert options == {"max_chars": 400}


def test_register_is_forward_compatible_without_register_skill(monkeypatch):
    _enabled_tracer(monkeypatch)
    ctx = FakeCtx(support_skills=False)
    # Must not raise even though register_skill blows up.
    hermes_otel.register(ctx)
    assert ctx.skills == []
    assert ctx.prompt_sections[0][0] == "hermes-otel.discovery"
    # Hooks still registered — skill failure doesn't abort registration.
    assert "pre_tool_call" in ctx.hooks


def test_register_is_forward_compatible_without_prompt_sections(monkeypatch):
    _enabled_tracer(monkeypatch)
    ctx = LegacyCtx()
    hermes_otel.register(ctx)
    assert "pre_tool_call" in ctx.hooks


def test_register_does_not_hide_prompt_registration_failures(monkeypatch):
    _enabled_tracer(monkeypatch)

    class BrokenPromptCtx(FakeCtx):
        def register_system_prompt_section(self, section_id, content, **kwargs):
            raise RuntimeError("prompt registry is broken")

    with pytest.raises(RuntimeError, match="prompt registry is broken"):
        hermes_otel.register(BrokenPromptCtx())
