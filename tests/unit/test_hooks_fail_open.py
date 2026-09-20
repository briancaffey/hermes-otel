"""Hook handlers must never raise into the agent loop (#88).

Hermes catches handler exceptions, but a raise still aborts the handler
mid-way — spans stay open, parent stacks go unbalanced — and spams the
Hermes log. Every shape below reproduced a raise before the fix.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermes_otel import hooks
from hermes_otel.hooks import (
    _as_dict,
    on_api_request_error,
    on_post_api_request,
    on_post_tool_call,
    on_pre_tool_call,
)


@pytest.fixture
def tracer(monkeypatch):
    tr = MagicMock()
    tr.is_enabled = True
    tr.config.skill_spans = True
    tr.config.capture_previews = True
    tr.config.capture_full_prompts = False
    tr.config.capture_full_responses = False
    tr.config.host_metrics = False
    tr.config.emit_genai_metrics = False
    tr.config.preview_max_chars = 1200
    tr.config.tool_input_preview_max_chars = None
    tr.config.tool_output_preview_max_chars = None
    tr.sessions.pop_tool_start.return_value = 1.0
    monkeypatch.setattr("hermes_otel.tracer.get_tracer", lambda: tr)
    return tr


class TestToolHooks:
    def test_scalar_json_result_for_skill_view_does_not_raise(self, tracer):
        on_post_tool_call(
            tool_name="skill_view",
            args={"name": "x"},
            result="42",
            task_id="t1",
            session_id="s1",
            status="ok",
        )
        tracer.end_span.assert_called_once()
        assert tracer.end_span.call_args[1]["attributes"]["hermes.tool.outcome"] == "completed"

    def test_list_json_result_does_not_raise(self, tracer):
        on_post_tool_call(tool_name="read", args={}, result="[1, 2]", task_id="t1", status="ok")
        tracer.end_span.assert_called_once()

    def test_non_serialisable_args_still_start_span(self, tracer):
        on_pre_tool_call(tool_name="terminal", args={"data": b"\x00", "p": object()}, task_id="t1")
        tracer.start_span.assert_called_once()
        attrs = tracer.start_span.call_args[1]["attributes"]
        assert "input.value" in attrs

    def test_non_string_tool_name_does_not_raise(self, tracer):
        on_pre_tool_call(tool_name=None, args={}, task_id="t1")
        on_post_tool_call(tool_name=None, args={}, result="ok", task_id="t1")
        tracer.start_span.assert_called_once()
        tracer.end_span.assert_called_once()


class TestApiHooks:
    def _post(self, usage):
        on_post_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="m",
            provider="p",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.1,
            finish_reason="stop",
            message_count=1,
            response_model="m",
            usage=usage,
            assistant_content_chars=1,
            assistant_tool_call_count=0,
        )

    def test_usage_as_object_is_read(self, tracer):
        self._post(SimpleNamespace(prompt_tokens=10, output_tokens=5))
        attrs = tracer.end_span.call_args[1]["attributes"]
        assert attrs["gen_ai.usage.input_tokens"] == 10
        assert attrs["gen_ai.usage.output_tokens"] == 5

    def test_usage_as_pydantic_like_is_read(self, tracer):
        class Usage:
            def model_dump(self):
                return {"prompt_tokens": 7, "output_tokens": 3}

        self._post(Usage())
        attrs = tracer.end_span.call_args[1]["attributes"]
        assert attrs["gen_ai.usage.input_tokens"] == 7

    def test_usage_as_string_does_not_raise(self, tracer):
        self._post("weird")
        tracer.end_span.assert_called_once()

    @pytest.mark.parametrize(
        "error, expected_message",
        [
            (RuntimeError("boom"), "boom"),
            ("plain text failure", "plain text failure"),
            ({"type": "RateLimit", "message": "slow down"}, "slow down"),
            (None, "fallback reason"),
        ],
    )
    def test_error_shapes_close_the_span(self, tracer, error, expected_message):
        on_api_request_error(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="m",
            provider="p",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.1,
            error=error,
            reason="fallback reason",
            status_code=500,
            retry_count=0,
            retryable=False,
        )
        tracer.end_span.assert_called_once()
        assert expected_message in str(tracer.end_span.call_args[1]["error_message"])


class TestFailOpenDecorator:
    def test_unexpected_exception_is_swallowed_and_warned_once(self, tracer, monkeypatch, caplog):
        import logging

        hooks._FAIL_OPEN_WARNED.clear()
        boom = MagicMock(side_effect=ValueError("kaboom"))
        monkeypatch.setattr("hermes_otel.hooks.tools._resolve_tool_outcome", boom)
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            for _ in range(3):
                assert on_post_tool_call(tool_name="x", args={}, result="{}", task_id="t") is None
        warnings = [r for r in caplog.records if "kaboom" in r.getMessage()]
        assert len(warnings) == 1  # once per (hook, exception type)

    def test_wrapped_names_are_preserved(self):
        assert on_post_tool_call.__name__ == "on_post_tool_call"


class TestAsDict:
    def test_shapes(self):
        assert _as_dict({"a": 1}) == {"a": 1}
        assert _as_dict(None) == {}
        assert _as_dict("") == {}
        assert _as_dict("msg") == {"message": "msg"}
        assert _as_dict(SimpleNamespace(a=1)) == {"a": 1}
        assert _as_dict(42) == {}
