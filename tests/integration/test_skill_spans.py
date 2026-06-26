"""Integration tests for skill execution-window spans (issue #39)."""

from hermes_otel.hooks import (
    on_post_tool_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)
from hermes_otel.plugin_config import HermesOtelConfig


def _parent_span_id(span):
    return span.parent.span_id if span.parent is not None else None


def _spans_named(spans, name):
    return [s for s in spans if s.name == name]


def _one(spans, name):
    matches = _spans_named(spans, name)
    assert len(matches) == 1, f"expected exactly one {name!r}, got {[s.name for s in spans]}"
    return matches[0]


def _load_skill(session_id, skill, task="sk1", tool="skill_view", args=None):
    """Fire a pre/post tool pair that loads a skill."""
    args = args if args is not None else {"name": skill}
    on_pre_tool_call(tool_name=tool, args=args, task_id=task, session_id=session_id)
    on_post_tool_call(
        tool_name=tool, args=args, result="loaded", task_id=task, session_id=session_id
    )


class TestSkillSpanBasics:
    def test_skill_view_opens_span_under_agent_root(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _load_skill("s1", "axolotl")
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        spans = exporter.get_finished_spans()
        skill = _one(spans, "skill.axolotl")
        agent = _one(spans, "agent")
        attrs = dict(skill.attributes)
        assert attrs["hermes.skill.name"] == "axolotl"
        assert attrs["hermes.skill.source"] == "skill_view"
        assert attrs["hermes.span_kind"] == "skill"
        assert attrs["gen_ai.skill.name"] == "axolotl"
        assert attrs["hermes.skill.result_status"] == "completed"
        # Nested under the turn root, not the tool span.
        assert _parent_span_id(skill) == agent.context.span_id

    def test_path_match_source(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _load_skill(
            "s1", "deploy", tool="read", args={"path": "/home/u/.hermes/skills/deploy/SKILL.md"}
        )
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        skill = _one(exporter.get_finished_spans(), "skill.deploy")
        assert dict(skill.attributes)["hermes.skill.source"] == "path_match"

    def test_interrupted_turn_marks_result_status(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _load_skill("s1", "axolotl")
        on_session_end(
            session_id="s1", completed=False, interrupted=True, model="gpt-4", platform="cli"
        )
        skill = _one(exporter.get_finished_spans(), "skill.axolotl")
        assert dict(skill.attributes)["hermes.skill.result_status"] == "interrupted"


class TestSkillSpanOverlap:
    def test_two_skills_overlap_as_siblings(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _load_skill("s1", "alpha", task="sk1")
        _load_skill("s1", "beta", task="sk2")
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        spans = exporter.get_finished_spans()
        alpha = _one(spans, "skill.alpha")
        beta = _one(spans, "skill.beta")
        agent = _one(spans, "agent")
        # Both closed (present in finished spans), both children of the root.
        assert _parent_span_id(alpha) == agent.context.span_id
        assert _parent_span_id(beta) == agent.context.span_id

    def test_same_skill_loaded_twice_is_one_span(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _load_skill("s1", "axolotl", task="sk1")
        _load_skill("s1", "axolotl", task="sk2")  # reload — keep the first window
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        assert len(_spans_named(exporter.get_finished_spans(), "skill.axolotl")) == 1


class TestSkillSpanConfigAndCompat:
    def test_disabled_keeps_attribute_and_counter_but_no_span(self, inmemory_otel_with_metrics):
        exporter, metric_reader, plugin = inmemory_otel_with_metrics
        plugin.config = HermesOtelConfig(skill_spans=False)

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _load_skill("s1", "axolotl")
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        spans = exporter.get_finished_spans()
        assert _spans_named(spans, "skill.axolotl") == []  # no skill span
        # The tool span still carries the inferred attribute (additive feature).
        tool = _one(spans, "tool.skill_view")
        assert dict(tool.attributes)["hermes.skill.name"] == "axolotl"

    def test_counter_and_turn_rollup_preserved(self, inmemory_otel_with_metrics):
        exporter, metric_reader, _ = inmemory_otel_with_metrics

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        _load_skill("s1", "axolotl")
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        # skill_inferred counter still fires.
        data = metric_reader.get_metrics_data()
        names = [
            m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        ]
        assert "hermes.skill.inferred" in names

        # Turn-level rollup attributes still land on the agent root.
        agent = _one(exporter.get_finished_spans(), "agent")
        attrs = dict(agent.attributes)
        assert attrs["hermes.turn.skill_count"] == 1
        assert "axolotl" in attrs["hermes.turn.skills"]
