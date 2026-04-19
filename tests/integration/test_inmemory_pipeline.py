"""Integration tests using InMemorySpanExporter to verify the full
hook -> span -> export pipeline without any network I/O."""

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


class TestToolSpanExport:
    def test_tool_span_exports_with_correct_attributes(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup

        on_pre_tool_call(tool_name="bash", args={"cmd": "ls"}, task_id="t1")
        on_post_tool_call(tool_name="bash", args={"cmd": "ls"}, result="file.txt", task_id="t1")

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "tool.bash"
        attrs = dict(span.attributes)
        assert attrs["tool.name"] == "bash"
        assert attrs["openinference.span.kind"] == "TOOL"
        assert "file.txt" in attrs["output.value"]

    def test_tool_error_span_has_error_status(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup

        on_pre_tool_call(tool_name="bash", args={"cmd": "rm"}, task_id="t1")
        on_post_tool_call(
            tool_name="bash",
            args={"cmd": "rm"},
            result='{"error": "permission denied"}',
            task_id="t1",
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        from opentelemetry.trace import StatusCode

        assert span.status.status_code == StatusCode.ERROR

    def test_tool_success_span_has_ok_status(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup

        on_pre_tool_call(tool_name="bash", args={}, task_id="t1")
        on_post_tool_call(tool_name="bash", args={}, result="done", task_id="t1")

        span = exporter.get_finished_spans()[0]
        from opentelemetry.trace import StatusCode

        assert span.status.status_code == StatusCode.OK


class TestLlmSpanExport:
    def test_llm_span_exports(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup

        on_pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        on_post_llm_call(
            session_id="s1",
            user_message="hello",
            assistant_response="hi there",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "llm.gpt-4"
        attrs = dict(span.attributes)
        assert attrs["openinference.span.kind"] == "LLM"
        assert attrs["input.value"] == "hello"
        assert attrs["output.value"] == "hi there"


class TestApiSpanExport:
    def test_api_span_exports_with_token_counts(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup

        on_pre_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=5,
            tool_count=0,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=1024,
        )
        on_post_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.5,
            finish_reason="stop",
            message_count=5,
            response_model="gpt-4",
            usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            assistant_content_chars=200,
            assistant_tool_call_count=0,
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "api.gpt-4"
        attrs = dict(span.attributes)
        # Dual conventions
        assert attrs["llm.token_count.prompt"] == 100
        assert attrs["gen_ai.usage.input_tokens"] == 100
        assert attrs["llm.token_count.completion"] == 50
        assert attrs["gen_ai.usage.output_tokens"] == 50


class TestSessionSpanExport:
    def test_session_span_exports(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup

        on_session_start(session_id="s1", model="gpt-4", platform="api_server")
        on_session_end(
            session_id="s1",
            completed=True,
            interrupted=False,
            model="gpt-4",
            platform="api_server",
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "agent"
        attrs = dict(span.attributes)
        assert attrs["openinference.span.kind"] == "AGENT"
        assert attrs["hermes.session.completed"] is True
