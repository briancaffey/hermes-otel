"""The transform_* observers (#33) never replace content and never raise."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hermes_otel.helpers import char_count, terminal_truncation
from hermes_otel.hooks import on_transform_llm_output, on_transform_terminal_output
from hermes_otel.hooks.transforms import _set_attributes


class _Span:
    def __init__(self):
        self.attributes = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value


class TestCharCount:
    def test_values(self):
        assert char_count(None) is None
        assert char_count("") == 0
        assert char_count("héllo") == 5
        assert char_count({"output": "ab"}) == len(str({"output": "ab"}))


class TestTerminalObserver:
    def test_records_raw_size_on_the_open_tool_span(self, mock_tracer):
        span = _Span()
        mock_tracer.spans._active_spans["terminal:tc1"] = span
        out = on_transform_terminal_output(
            command="seq 1 100000",
            output="x" * 50_000,
            returncode=0,
            task_id="t1",
            env_type="local",
            tool_call_id="tc1",
        )
        assert out is None
        assert span.attributes == {
            "hermes.terminal.output.chars": 50_000,
            "hermes.terminal.returncode": 0,
            "hermes.terminal.env_type": "local",
        }
        assert mock_tracer.sessions.pop_tool_output_chars("terminal:tc1") == 50_000

    def test_falls_back_to_task_id_without_a_tool_call_id(self, mock_tracer):
        span = _Span()
        mock_tracer.spans._active_spans["terminal:t9"] = span
        assert (
            on_transform_terminal_output(command="ls", output="a", returncode=1, task_id="t9")
            is None
        )
        assert span.attributes["hermes.terminal.output.chars"] == 1
        assert span.attributes["hermes.terminal.returncode"] == 1

    def test_returns_none_for_every_input_shape(self, mock_tracer):
        assert on_transform_terminal_output() is None
        assert on_transform_terminal_output(output=None, returncode="not-an-int") is None
        assert on_transform_terminal_output(output=b"bytes", returncode=None, task_id=None) is None
        assert on_transform_terminal_output(output="x", returncode=True, unexpected_field=1) is None

    def test_no_span_still_keeps_the_size_for_post_tool_call(self, mock_tracer):
        assert on_transform_terminal_output(output="abc", returncode=0, task_id="t2") is None
        assert mock_tracer.sessions.pop_tool_output_chars("terminal:t2") == 3

    def test_non_int_returncode_and_blank_env_type_are_omitted(self, mock_tracer):
        span = _Span()
        mock_tracer.spans._active_spans["terminal:t3"] = span
        on_transform_terminal_output(output="abc", returncode="1", task_id="t3", env_type="")
        assert span.attributes == {"hermes.terminal.output.chars": 3}

    def test_disabled_tracer_is_a_no_op(self, mock_tracer):
        mock_tracer.is_enabled = False
        assert on_transform_terminal_output(output="abc", returncode=0, task_id="t4") is None
        assert mock_tracer.sessions.pop_tool_output_chars("terminal:t4") is None


class TestLlmOutputObserver:
    def test_records_size_on_the_session_root(self, mock_tracer):
        root = _Span()
        mock_tracer.spans._roots["s1"] = root
        out = on_transform_llm_output(
            response_text="final answer",
            session_id="s1",
            model="m",
            platform="cli",
            turn_id="s1:t:x",
        )
        assert out is None
        assert root.attributes == {"hermes.llm.output.chars": 12}

    def test_returns_none_for_every_input_shape(self, mock_tracer):
        assert on_transform_llm_output() is None
        assert on_transform_llm_output(response_text=None, session_id=None) is None
        assert on_transform_llm_output(response_text="x", session_id="unknown", extra=1) is None

    def test_disabled_tracer_is_a_no_op(self, mock_tracer):
        mock_tracer.is_enabled = False
        root = _Span()
        mock_tracer.spans._roots["s1"] = root
        assert on_transform_llm_output(response_text="x", session_id="s1") is None
        assert root.attributes == {}


class TestSetAttributes:
    def test_skips_objects_without_the_otel_api_and_none_values(self):
        _set_attributes(object(), {"a": 1})  # LangSmith runs have no set_attribute
        span = _Span()
        _set_attributes(span, {"a": 1, "b": None})
        assert span.attributes == {"a": 1}

    def test_never_raises_when_set_attribute_does(self):
        span = MagicMock()
        span.set_attribute.side_effect = RuntimeError("boom")
        _set_attributes(span, {"a": 1})


class TestFailOpen:
    def test_a_raising_tracer_lookup_returns_none(self):
        with patch("hermes_otel.tracer.get_tracer", side_effect=RuntimeError("boom")):
            assert on_transform_terminal_output(output="x", returncode=0, task_id="t") is None
            assert on_transform_llm_output(response_text="x", session_id="s") is None


class TestTerminalTruncation:
    def test_spill_envelope(self):
        assert terminal_truncation({"output": "x" * 100, "output_total_chars": 5000}) == (
            True,
            4900,
        )

    def test_head_tail_notice(self):
        kept = (
            "x" * 10
            + "\n\n... [OUTPUT TRUNCATED - 150,000 chars omitted out of 200,000 total] ...\n\n"
            + "x" * 10
        )
        assert terminal_truncation({"output": kept}) == (True, 150_000)

    def test_plain_output_is_not_truncation(self):
        assert terminal_truncation({"output": "100000"}) == (False, None)
        assert terminal_truncation({}) == (False, None)
        assert terminal_truncation({"output": None}) == (False, None)
        assert terminal_truncation({"output": "x", "output_total_chars": True}) == (False, None)
