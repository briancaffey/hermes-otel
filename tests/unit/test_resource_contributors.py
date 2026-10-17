"""``build_resource`` as a list of contributors (#504): each one alone, and
the spec's ``required_resource_attributes`` checked once for every backend."""

from __future__ import annotations

import pytest

from hermes_otel import pipeline
from hermes_otel.backends import _ResolvedBackend
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin


def _plugin(**config) -> HermesOTelPlugin:
    return HermesOTelPlugin(config=HermesOtelConfig(**config))


class TestContributors:
    def test_service_identity_default(self):
        attrs = pipeline._service_identity(_plugin())
        assert attrs["service.name"] == "hermes-agent"
        assert attrs["service.instance.id"] == pipeline._SERVICE_INSTANCE_ID
        assert "service.namespace" not in attrs
        assert "hermes.profile" in attrs

    def test_service_identity_from_a_named_profile(self):
        plugin = _plugin(service_name_from_profile=True)
        plugin.profile_name = "research"
        attrs = pipeline._service_identity(plugin)
        assert attrs["service.name"] == "research"
        assert attrs["service.namespace"] == "hermes-agent"
        assert attrs["hermes.profile"] == "research"

    def test_kanban_identity_needs_the_task(self, monkeypatch):
        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
        monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
        assert pipeline._kanban_identity(_plugin()) == {}
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
        assert pipeline._kanban_identity(_plugin()) == {
            "hermes.kanban.task_id": "t_1",
            "hermes.kanban.board": "default",
        }

    def test_project_config_over_env(self, monkeypatch):
        monkeypatch.delenv("OTEL_PROJECT_NAME", raising=False)
        assert pipeline._project(_plugin()) == {}
        monkeypatch.setenv("OTEL_PROJECT_NAME", "from-env")
        assert pipeline._project(_plugin()) == {"openinference.project.name": "from-env"}
        assert pipeline._project(_plugin(project_name="cfg")) == {
            "openinference.project.name": "cfg"
        }

    def test_host_name_only_with_host_metrics(self):
        assert pipeline._host_name(_plugin(host_metrics=False)) == {}
        assert pipeline._host_name(_plugin(host_metrics=True))["host.name"]

    def test_every_contributor_is_pure_over_the_plugin(self):
        plugin = _plugin()
        for contribute in pipeline._RESOURCE_CONTRIBUTORS:
            assert contribute(plugin) == contribute(plugin)


class TestRequiredResourceAttributes:
    def _weave(self, **attrs) -> _ResolvedBackend:
        return _ResolvedBackend(
            type="weave",
            endpoint="https://trace.wandb.ai/otel/v1/traces",
            display_name="W&B Weave",
            headers={},
            resource_attributes=attrs or None,
        )

    def test_missing_routing_fails_with_the_spec_names(self):
        with pytest.raises(ValueError, match="wandb.entity, wandb.project as resource attributes"):
            pipeline.build_resource(_plugin(), [self._weave()])
        with pytest.raises(ValueError, match="requires wandb.project as resource attributes"):
            pipeline.build_resource(_plugin(), [self._weave(**{"wandb.entity": "team"})])

    def test_either_overlay_satisfies_it(self):
        plugin = _plugin(resource_attributes={"wandb.entity": "team", "wandb.project": "p"})
        attrs = dict(pipeline.build_resource(plugin, [self._weave()]).attributes)
        assert attrs["wandb.entity"] == "team"
        attrs = dict(
            pipeline.build_resource(
                _plugin(), [self._weave(**{"wandb.entity": "t", "wandb.project": "p"})]
            ).attributes
        )
        assert attrs["wandb.project"] == "p"

    def test_a_type_without_requirements_passes(self):
        rb = _ResolvedBackend(
            type="otlp", endpoint="http://localhost:4318/v1/traces", display_name="x", headers={}
        )
        assert pipeline.build_resource(_plugin(), [rb]).attributes["service.name"]
