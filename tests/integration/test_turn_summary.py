"""Integration tests for per-turn summary attributes on the session span."""

import pytest
from hermes_otel.hooks import (
    on_post_api_request,
    on_post_llm_call,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_llm_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)


def _span_by_name(spans, name):
    for s in spans:
        if s.name == name:
            return s
    raise ValueError(f"No span named '{name}' in {[s.name for s in spans]}")


def _run_full_turn(session_id: str, tool_calls):
    """Helper: drive a full session through the hook sequence.

    tool_calls: list of (tool_name, args_dict, result) tuples.
    """
    on_session_start(session_id=session_id, model="gpt-4", platform="cli")
    on_pre_llm_call(
        session_id=session_id,
        user_message="hello",
        conversation_history=[],
        is_first_turn=True,
        model="gpt-4",
        platform="cli",
    )
    on_pre_api_request(
        task_id="api1",
        session_id=session_id,
        platform="cli",
        model="gpt-4",
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        message_count=5,
        tool_count=len(tool_calls),
        approx_input_tokens=500,
        request_char_count=2000,
        max_tokens=1024,
    )
    for i, (name, args, result) in enumerate(tool_calls):
        task_id = f"t{i}"
        on_pre_tool_call(tool_name=name, args=args, task_id=task_id, session_id=session_id)
        on_post_tool_call(
            tool_name=name, args=args, result=result, task_id=task_id, session_id=session_id
        )
    on_post_api_request(
        task_id="api1",
        session_id=session_id,
        platform="cli",
        model="gpt-4",
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        api_duration=1.0,
        finish_reason="stop",
        message_count=5,
        response_model="gpt-4",
        usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        assistant_content_chars=200,
        assistant_tool_call_count=len(tool_calls),
    )
    on_post_llm_call(
        session_id=session_id,
        user_message="hello",
        assistant_response="done",
        conversation_history=[],
        model="gpt-4",
        platform="cli",
    )
    on_session_end(
        session_id=session_id,
        completed=True,
        interrupted=False,
        model="gpt-4",
        platform="cli",
    )


class TestTurnSummaryBasic:
    def test_two_tool_session_attributes(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _run_full_turn(
            "s1",
            [
                ("bash", {"command": "ls"}, "file1\nfile2"),
                ("read", {"path": "/tmp/foo"}, "contents"),
            ],
        )

        spans = exporter.get_finished_spans()
        agent = _span_by_name(spans, "agent")
        attrs = dict(agent.attributes)

        assert attrs["hermes.turn.tool_count"] == 2
        assert attrs["hermes.turn.tools"] == "bash,read"
        assert attrs["hermes.turn.tool_outcomes"] == "completed"
        assert attrs["hermes.turn.api_call_count"] == 1
        assert attrs["hermes.turn.final_status"] == "completed"
        # Targets / commands are pipe-joined distinct values
        assert attrs["hermes.turn.tool_targets"] == "/tmp/foo"
        assert attrs["hermes.turn.tool_commands"] == "ls"

    def test_distinct_tools_deduplicated(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _run_full_turn(
            "s1",
            [
                ("bash", {"command": "ls"}, "ok"),
                ("bash", {"command": "pwd"}, "ok"),
                ("bash", {"command": "ls"}, "ok"),  # duplicate command
            ],
        )
        agent = _span_by_name(exporter.get_finished_spans(), "agent")
        attrs = dict(agent.attributes)
        assert attrs["hermes.turn.tool_count"] == 1
        assert attrs["hermes.turn.tools"] == "bash"
        # Distinct commands only
        assert attrs["hermes.turn.tool_commands"] == "ls|pwd"

    def test_skill_inference_included(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _run_full_turn(
            "s1",
            [
                ("read", {"path": "/repo/skills/monitor/SKILL.md"}, "ok"),
                ("read", {"path": "/repo/skills/deployer/index.md"}, "ok"),
            ],
        )
        agent = _span_by_name(exporter.get_finished_spans(), "agent")
        attrs = dict(agent.attributes)
        assert attrs["hermes.turn.skill_count"] == 2
        assert attrs["hermes.turn.skills"] == "deployer,monitor"

    def test_error_outcome_reflected(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _run_full_turn(
            "s1",
            [
                ("bash", {"command": "ls"}, "ok"),
                ("bash", {"command": "fail"}, '{"error": "boom"}'),
            ],
        )
        agent = _span_by_name(exporter.get_finished_spans(), "agent")
        attrs = dict(agent.attributes)
        assert "completed" in attrs["hermes.turn.tool_outcomes"]
        assert "error" in attrs["hermes.turn.tool_outcomes"]


class TestTurnSummaryEdgeCases:
    def test_session_with_no_tools(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _run_full_turn("s1", [])
        agent = _span_by_name(exporter.get_finished_spans(), "agent")
        attrs = dict(agent.attributes)
        # Zero-count attributes are skipped entirely
        assert "hermes.turn.tool_count" not in attrs
        assert "hermes.turn.tools" not in attrs
        # But api_call_count and final_status are present
        assert attrs["hermes.turn.api_call_count"] == 1
        assert attrs["hermes.turn.final_status"] == "completed"

    def test_interrupted_session_final_status(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_session_end(
            session_id="s1", completed=False, interrupted=True, model="gpt-4", platform="cli"
        )
        agent = _span_by_name(exporter.get_finished_spans(), "agent")
        assert agent.attributes["hermes.turn.final_status"] == "interrupted"

    def test_incomplete_session_final_status(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_session_end(
            session_id="s1", completed=False, interrupted=False, model="gpt-4", platform="cli"
        )
        agent = _span_by_name(exporter.get_finished_spans(), "agent")
        assert agent.attributes["hermes.turn.final_status"] == "incomplete"

    def test_long_tool_list_clipped(self, inmemory_otel_setup):
        """Very long tools string is capped at 500 chars with ellipsis."""
        exporter, _ = inmemory_otel_setup
        many = [(f"tool_{i:04d}", {}, "ok") for i in range(200)]
        _run_full_turn("s1", many)
        agent = _span_by_name(exporter.get_finished_spans(), "agent")
        tools_attr = agent.attributes["hermes.turn.tools"]
        assert len(tools_attr) == 500
        assert tools_attr.endswith("...")
