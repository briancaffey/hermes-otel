"""Integration tests for hermes.tool.* and hermes.skill.* attributes on tool spans."""

import pytest
from hermes_otel.hooks import (
    on_post_tool_call,
    on_pre_tool_call,
)


def _span_by_name(spans, name):
    for s in spans:
        if s.name == name:
            return s
    raise ValueError(f"No span named '{name}' in {[s.name for s in spans]}")


class TestToolTargetAttributes:
    def test_target_attribute_from_path(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_pre_tool_call(tool_name="read", args={"path": "/tmp/foo.txt"}, task_id="t1")
        on_post_tool_call(
            tool_name="read", args={"path": "/tmp/foo.txt"}, result="contents", task_id="t1"
        )
        span = _span_by_name(exporter.get_finished_spans(), "tool.read")
        assert span.attributes["hermes.tool.target"] == "/tmp/foo.txt"

    def test_target_attribute_from_file_path(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_pre_tool_call(tool_name="edit", args={"file_path": "/etc/hosts"}, task_id="t1")
        on_post_tool_call(
            tool_name="edit", args={"file_path": "/etc/hosts"}, result="ok", task_id="t1"
        )
        span = _span_by_name(exporter.get_finished_spans(), "tool.edit")
        assert span.attributes["hermes.tool.target"] == "/etc/hosts"

    def test_command_attribute_from_command(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_pre_tool_call(tool_name="bash", args={"command": "ls -la"}, task_id="t1")
        on_post_tool_call(tool_name="bash", args={"command": "ls -la"}, result="ok", task_id="t1")
        span = _span_by_name(exporter.get_finished_spans(), "tool.bash")
        assert span.attributes["hermes.tool.command"] == "ls -la"

    def test_no_target_or_command_when_absent(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_pre_tool_call(tool_name="noop", args={"foo": "bar"}, task_id="t1")
        on_post_tool_call(tool_name="noop", args={"foo": "bar"}, result="ok", task_id="t1")
        span = _span_by_name(exporter.get_finished_spans(), "tool.noop")
        assert "hermes.tool.target" not in span.attributes
        assert "hermes.tool.command" not in span.attributes


class TestToolOutcomeAttribute:
    def test_completed_on_plain_result(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        on_post_tool_call(tool_name="bash", args={}, result="ok", task_id="t1")
        span = _span_by_name(exporter.get_finished_spans(), "tool.bash")
        assert span.attributes["hermes.tool.outcome"] == "completed"

    def test_error_on_error_result(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        on_post_tool_call(tool_name="bash", args={}, result='{"error": "boom"}', task_id="t1")
        span = _span_by_name(exporter.get_finished_spans(), "tool.bash")
        assert span.attributes["hermes.tool.outcome"] == "error"

    def test_explicit_status_overrides(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        on_post_tool_call(tool_name="bash", args={}, result='{"status": "timeout"}', task_id="t1")
        span = _span_by_name(exporter.get_finished_spans(), "tool.bash")
        assert span.attributes["hermes.tool.outcome"] == "timeout"

    def test_timeout_does_not_flip_span_status_to_error(self, inmemory_otel_setup):
        """Per PRD: only `error` outcome maps to span status ERROR; timeouts stay OK."""
        from opentelemetry.trace import StatusCode

        exporter, _ = inmemory_otel_setup
        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        on_post_tool_call(tool_name="bash", args={}, result='{"status": "timeout"}', task_id="t1")
        span = _span_by_name(exporter.get_finished_spans(), "tool.bash")
        assert span.status.status_code == StatusCode.OK

    def test_error_flips_span_status(self, inmemory_otel_setup):
        from opentelemetry.trace import StatusCode

        exporter, _ = inmemory_otel_setup
        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        on_post_tool_call(tool_name="bash", args={}, result='{"error": "boom"}', task_id="t1")
        span = _span_by_name(exporter.get_finished_spans(), "tool.bash")
        assert span.status.status_code == StatusCode.ERROR


class TestSkillNameInference:
    def test_skill_name_attached_on_skills_path(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        args = {"path": "/repo/skills/monitor/SKILL.md"}
        on_pre_tool_call(tool_name="read", args=args, task_id="t1")
        on_post_tool_call(tool_name="read", args=args, result="ok", task_id="t1")
        span = _span_by_name(exporter.get_finished_spans(), "tool.read")
        assert span.attributes["hermes.skill.name"] == "monitor"

    def test_no_skill_on_regular_path(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        args = {"path": "/tmp/scratch.txt"}
        on_pre_tool_call(tool_name="read", args=args, task_id="t1")
        on_post_tool_call(tool_name="read", args=args, result="ok", task_id="t1")
        span = _span_by_name(exporter.get_finished_spans(), "tool.read")
        assert "hermes.skill.name" not in span.attributes

    def test_no_skill_on_optional_skills_references(self, inmemory_otel_setup):
        """Per PRD acceptance criteria — must NOT match /optional-skills/<n>/references/"""
        exporter, _ = inmemory_otel_setup
        args = {"path": "/repo/optional-skills/monitor/references/api.md"}
        on_pre_tool_call(tool_name="read", args=args, task_id="t1")
        on_post_tool_call(tool_name="read", args=args, result="ok", task_id="t1")
        span = _span_by_name(exporter.get_finished_spans(), "tool.read")
        assert "hermes.skill.name" not in span.attributes


class TestSkillInferredCounter:
    def test_counter_incremented_on_inference(self, inmemory_otel_with_metrics):
        span_exporter, metric_reader, _ = inmemory_otel_with_metrics
        args = {"path": "/repo/skills/builder/SKILL.md"}
        on_pre_tool_call(tool_name="read", args=args, task_id="t1")
        on_post_tool_call(tool_name="read", args=args, result="ok", task_id="t1")

        metric_reader.collect()
        data = metric_reader.get_metrics_data()
        metric_names = [
            m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        ]
        assert "hermes.skill.inferred" in metric_names
