"""The payload boundary (#512): one reader per Hermes field, shared by the hooks."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from helpers.golden import config_for

from hermes_otel.hooks.api_call import HookPayload, full_response_attributes, response_attributes
from hermes_otel.hooks.kanban import _int_attributes
from hermes_otel.hooks.llm import _error_attributes

TOOL_CALL = {"id": "call_1", "type": "function", "function": {"name": "ls", "arguments": "{}"}}


class TestAssistantMessage:
    """The two spellings Hermes uses for the reply read as one pair."""

    def test_raw_object_spelling(self):
        message = SimpleNamespace(content="hi", tool_calls=[TOOL_CALL])
        assert HookPayload({"assistant_message": message}).assistant_message == ("hi", [TOOL_CALL])

    def test_response_dict_spelling(self):
        response = {"assistant_message": {"role": "assistant", "content": "hi", "tool_calls": []}}
        assert HookPayload({"response": response}).assistant_message == ("hi", [])

    def test_raw_object_wins_over_the_response_dict(self):
        """``post_api_request`` sends both; the raw object is the unsanitised one."""
        message = SimpleNamespace(content="object", tool_calls=[TOOL_CALL])
        response = {"assistant_message": {"content": "sanitised", "tool_calls": []}}
        payload = HookPayload({"assistant_message": message, "response": response})
        assert payload.assistant_message == ("object", [TOOL_CALL])

    def test_nothing_sent(self):
        assert HookPayload({}).assistant_message == (None, None)
        assert HookPayload({"response": None}).assistant_message == (None, None)


class TestFullResponseAttributes:
    @staticmethod
    def _tracer(mode: str):
        return SimpleNamespace(config=config_for(mode))

    def test_off_outside_full_capture(self):
        payload = HookPayload({"response": {"assistant_message": {"content": "hi"}}})
        assert full_response_attributes(self._tracer("preview"), payload) == {}

    def test_text_reply_from_the_response_dict(self):
        """The auxiliary hook's former inline shape, byte for byte."""
        payload = HookPayload(
            {"response": {"assistant_message": {"role": "assistant", "content": "Chat title"}}}
        )
        assert full_response_attributes(self._tracer("full"), payload) == {
            "gen_ai.output.messages": '[{"role": "assistant", "content": "Chat title"}]',
            "output.value": "Chat title",
            "output.mime_type": "text/plain",
            "hermes.content.output_chars": 10,
        }

    def test_tool_calls_only(self):
        payload = HookPayload(
            {"response": {"assistant_message": {"content": "", "tool_calls": [TOOL_CALL]}}}
        )
        attrs = full_response_attributes(self._tracer("full"), payload)
        assert attrs["output.mime_type"] == "application/json"
        assert json.loads(attrs["output.value"]) == [TOOL_CALL]
        assert json.loads(attrs["gen_ai.output.messages"]) == [
            {"role": "assistant", "tool_calls": [TOOL_CALL]}
        ]
        assert attrs["hermes.content.output_chars"] == len(attrs["output.value"])

    def test_empty_reply_sets_nothing(self):
        payload = HookPayload(
            {"response": {"assistant_message": {"content": None, "tool_calls": []}}}
        )
        assert full_response_attributes(self._tracer("full"), payload) == {}


class TestResponseAttributesDurationOnly:
    def test_failure_path_has_only_the_timing(self):
        assert response_attributes(api_duration=1.25) == {"llm.response.duration_ms": 1250.0}
        assert response_attributes(api_duration=None) == {}


class TestErrorAttributes:
    def test_every_value_under_its_keys(self):
        assert _error_attributes("RateLimitError", 429, 1, 5, True) == {
            "error.type": "RateLimitError",
            "http.response.status_code": 429,
            "gen_ai.response.status_code": 429,
            "hermes.retry.count": 1,
            "hermes.max_retries": 5,
            "hermes.retryable": True,
        }

    def test_nothing_usable_is_absent(self):
        assert _error_attributes("", None, "n/a", None, None) == {}

    def test_digit_strings_and_zero_count(self):
        attrs = _error_attributes("", "503", 0, None, False)
        assert attrs == {
            "http.response.status_code": 503,
            "gen_ai.response.status_code": 503,
            "hermes.retry.count": 0,
            "hermes.retryable": False,
        }


class TestKanbanIntAttributes:
    @pytest.mark.parametrize("value", [True, False, "4242", "1", 3.5, None, object()])
    def test_only_a_real_int_counts(self, value):
        assert _int_attributes(("hermes.kanban.worker_pid", value)) == {}

    def test_ints_including_negative_exit_codes(self):
        assert _int_attributes(
            ("hermes.kanban.worker_pid", 4242), ("hermes.kanban.exit_code", -15)
        ) == {"hermes.kanban.worker_pid": 4242, "hermes.kanban.exit_code": -15}
        assert _int_attributes(("hermes.kanban.worker_pid", 0)) == {"hermes.kanban.worker_pid": 0}
