"""Profile-aware service identity (#395): ``service.name`` / ``service.namespace``
on the Resource, ``gen_ai.agent.name`` on the turn root, and the service the
dashboard adapters scope their queries to."""

from __future__ import annotations

import pytest

from hermes_otel import hooks
from hermes_otel.dashboard import backends
from hermes_otel.helpers import named_profile
from hermes_otel.pipeline import build_resource
from hermes_otel.plugin_config import HermesOtelConfig, load_config


class TestHelpers:
    @pytest.mark.parametrize("value", ["", "default", "custom", "  default ", None, 3])
    def test_unnamed_profiles(self, value):
        assert named_profile(value) == ""

    def test_named_profile(self):
        assert named_profile(" coder ") == "coder"


class TestConfig:
    def test_off_by_default(self):
        assert HermesOtelConfig().service_name_from_profile is False

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("HERMES_OTEL_SERVICE_NAME_FROM_PROFILE", "true")
        assert load_config().service_name_from_profile is True

    def test_yaml(self, tmp_path, monkeypatch):
        path = tmp_path / "hermes_otel.yaml"
        path.write_text("service_name_from_profile: true\n")
        monkeypatch.setenv("HERMES_OTEL_CONFIG", str(path))
        assert load_config().service_name_from_profile is True


class TestResource:
    def test_default_unchanged(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        plugin.profile_name = "coder"
        attrs = build_resource(plugin.config, plugin.profile_name).attributes
        assert attrs["service.name"] == "hermes-agent"
        assert "service.namespace" not in attrs
        assert attrs["hermes.profile"] == "coder"

    def test_opt_in_uses_the_profile(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(service_name_from_profile=True)
        plugin.profile_name = "coder"
        attrs = build_resource(plugin.config, plugin.profile_name).attributes
        assert attrs["service.name"] == "coder"
        assert attrs["service.namespace"] == "hermes-agent"
        assert attrs["service.instance.id"]  # still one per process

    def test_opt_in_default_profile_stays_hermes_agent(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(service_name_from_profile=True)
        plugin.profile_name = "default"
        attrs = build_resource(plugin.config, plugin.profile_name).attributes
        assert attrs["service.name"] == "hermes-agent"
        assert "service.namespace" not in attrs

    def test_resource_attributes_still_win(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(
            service_name_from_profile=True,
            resource_attributes={"service.name": "bot", "service.namespace": "team"},
        )
        plugin.profile_name = "coder"
        attrs = build_resource(plugin.config, plugin.profile_name).attributes
        assert attrs["service.name"] == "bot"
        assert attrs["service.namespace"] == "team"

    def test_resource_service_name_keeps_the_profile_namespace(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(
            service_name_from_profile=True, resource_attributes={"service.name": "bot"}
        )
        plugin.profile_name = "coder"
        attrs = build_resource(plugin.config, plugin.profile_name).attributes
        assert attrs["service.name"] == "bot"
        assert attrs["service.namespace"] == "hermes-agent"

    @pytest.mark.parametrize("from_profile", [False, True])
    def test_otel_service_name_has_no_effect(self, inmemory_otel_setup, monkeypatch, from_profile):
        """The plugin's own ``service.name`` beats the SDK's env detector (#609)."""
        _, plugin = inmemory_otel_setup
        monkeypatch.setenv("OTEL_SERVICE_NAME", "foo")
        plugin.config = HermesOtelConfig(service_name_from_profile=from_profile)
        plugin.profile_name = "coder"
        attrs = build_resource(plugin.config, plugin.profile_name).attributes
        assert attrs["service.name"] == ("coder" if from_profile else "hermes-agent")


def _root(exporter, plugin, profile, **host_kwargs):
    plugin.profile_name = profile
    hooks.on_session_start(session_id="s-id", model="m", platform="cli", **host_kwargs)
    hooks.on_session_end(
        session_id="s-id",
        completed=True,
        interrupted=False,
        model="m",
        platform="cli",
        **host_kwargs,
    )
    return next(s for s in exporter.get_finished_spans() if s.name == "agent")


class TestAgentName:
    def test_named_profile_names_the_agent(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        root = _root(exporter, plugin, "coder")
        assert root.attributes["gen_ai.agent.name"] == "coder"

    def test_does_not_need_the_service_option(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        assert plugin.config.service_name_from_profile is False
        root = _root(exporter, plugin, "reviewer")
        assert root.attributes["gen_ai.agent.name"] == "reviewer"

    @pytest.mark.parametrize("profile", ["default", "custom"])
    def test_unnamed_profiles_keep_hermes_agent(self, inmemory_otel_setup, profile):
        exporter, plugin = inmemory_otel_setup
        root = _root(exporter, plugin, profile)
        assert root.attributes["gen_ai.agent.name"] == "hermes-agent"

    def test_host_agent_name_wins(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        root = _root(exporter, plugin, "coder", agent_name="planner")
        assert root.attributes["gen_ai.agent.name"] == "planner"


class TestDashboardDefaultService:
    @pytest.fixture(autouse=True)
    def _no_env(self, monkeypatch):
        monkeypatch.delenv("HERMES_OTEL_SERVICE_NAME_FROM_PROFILE", raising=False)
        monkeypatch.setattr(backends, "resolve_profile_name", lambda: "coder")

    def _top(self, monkeypatch, data):
        monkeypatch.setattr(backends, "top_level_config", lambda: data)

    def test_off_is_hermes_agent(self, monkeypatch):
        self._top(monkeypatch, {})
        assert backends.default_service_name({}) == "hermes-agent"

    def test_on_follows_the_viewed_profile(self, monkeypatch):
        self._top(monkeypatch, {"service_name_from_profile": True})
        assert backends.default_service_name({}) == "coder"

    def test_env_var_turns_it_on(self, monkeypatch):
        self._top(monkeypatch, {})
        monkeypatch.setenv("HERMES_OTEL_SERVICE_NAME_FROM_PROFILE", "1")
        assert backends.default_service_name({}) == "coder"

    def test_env_var_turns_it_off(self, monkeypatch):
        self._top(monkeypatch, {"service_name_from_profile": True})
        monkeypatch.setenv("HERMES_OTEL_SERVICE_NAME_FROM_PROFILE", "false")
        assert backends.default_service_name({}) == "hermes-agent"

    def test_default_profile_is_hermes_agent(self, monkeypatch):
        self._top(monkeypatch, {"service_name_from_profile": True})
        monkeypatch.setattr(backends, "resolve_profile_name", lambda: "default")
        assert backends.default_service_name({}) == "hermes-agent"

    def test_explicit_names_still_win(self, monkeypatch):
        self._top(
            monkeypatch,
            {"service_name_from_profile": True, "resource_attributes": {"service.name": "bot"}},
        )
        assert backends.default_service_name({}) == "bot"
        assert backends.default_service_name({"service_name": "svc"}) == "svc"

    def test_profile_lookup_failure_falls_back(self, monkeypatch):
        self._top(monkeypatch, {"service_name_from_profile": True})

        def boom():
            raise RuntimeError("no hermes")

        monkeypatch.setattr(backends, "resolve_profile_name", boom)
        assert backends.default_service_name({}) == "hermes-agent"
