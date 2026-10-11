"""Transform observers (#33) through the real pipeline: terminal truncation
becomes visible on the tool span, the final response size lands on the root,
and nothing is ever replaced."""

from __future__ import annotations

import json

from _helpers import one as _one

from hermes_otel.hooks import (
    on_post_llm_call,
    on_post_tool_call,
    on_pre_llm_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
    on_transform_llm_output,
    on_transform_terminal_output,
)

SID = "20260626_010101_abc123"
RAW = "x" * 200_000
# What Hermes' head+tail limit (tools/tool_output_truncate.py) leaves in the result.
NOTICE = "\n\n... [OUTPUT TRUNCATED - 150,000 chars omitted out of 200,000 total] ...\n\n"
KEPT = "x" * 30_000 + NOTICE + "x" * 20_000


def _turn(exporter, raw=RAW, kept=KEPT, returncode=0, extra=None):
    """One turn: llm → terminal tool (raw output observed, limited result) → final text."""
    returns = []
    on_session_start(session_id=SID, model="gpt-4", platform="cli")
    on_pre_llm_call(
        session_id=SID,
        user_message="run it",
        conversation_history=[],
        is_first_turn=True,
        model="gpt-4",
        platform="cli",
    )
    on_pre_tool_call(
        tool_name="terminal",
        args={"command": "seq 1 100000"},
        task_id="t1",
        tool_call_id="tc1",
        session_id=SID,
    )
    returns.append(
        on_transform_terminal_output(
            command="seq 1 100000",
            output=raw,
            returncode=returncode,
            task_id="t1",
            env_type="local",
            tool_call_id="tc1",
        )
    )
    result = json.dumps({"output": kept, "returncode": returncode, **(extra or {})})
    on_post_tool_call(
        tool_name="terminal",
        args={"command": "seq 1 100000"},
        result=result,
        task_id="t1",
        tool_call_id="tc1",
        session_id=SID,
    )
    on_post_llm_call(
        session_id=SID,
        user_message="run it",
        assistant_response="done",
        conversation_history=[],
        model="gpt-4",
        platform="cli",
    )
    returns.append(
        on_transform_llm_output(
            response_text="The output had 100000 lines.",
            session_id=SID,
            model="gpt-4",
            platform="cli",
            turn_id=f"{SID}:t1:abcd",
        )
    )
    on_session_end(session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="cli")
    return exporter.get_finished_spans(), returns, result


class TestTerminalTruncationIsVisible:
    def test_raw_size_result_size_and_the_gap_on_the_tool_span(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        spans, returns, result = _turn(exporter)
        assert returns == [None, None]
        tool = _one(spans, "tool.terminal")
        a = tool.attributes
        assert a["hermes.terminal.output.chars"] == 200_000
        assert a["hermes.terminal.returncode"] == 0
        assert a["hermes.terminal.env_type"] == "local"
        assert a["hermes.tool.result.chars"] == len(result)
        assert a["hermes.terminal.output.truncated"] is True
        assert a["hermes.terminal.output.dropped_chars"] == 150_000

    def test_capture_window_spill_is_read_from_the_envelope(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        spans, _, _ = _turn(
            exporter, raw="x" * 1_000, kept="x" * 1_000, extra={"output_total_chars": 9_000}
        )
        a = _one(spans, "tool.terminal").attributes
        assert a["hermes.terminal.output.truncated"] is True
        assert a["hermes.terminal.output.dropped_chars"] == 8_000

    def test_stripped_whitespace_is_not_truncation(self, inmemory_otel_setup):
        # Observed live: `seq 1 100000 | wc -l` is "  100000\n" raw and "100000" after
        # Hermes strips it; a size comparison called that truncation.
        exporter, _ = inmemory_otel_setup
        spans, _, _ = _turn(exporter, raw="  100000\n", kept="100000", returncode=2)
        a = _one(spans, "tool.terminal").attributes
        assert a["hermes.terminal.output.chars"] == 9
        assert a["hermes.terminal.returncode"] == 2
        assert a["hermes.terminal.output.truncated"] is False
        assert "hermes.terminal.output.dropped_chars" not in a

    def test_final_response_size_lands_on_the_turn_root(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        spans, _, _ = _turn(exporter)
        root = _one(spans, "agent")
        assert root.attributes["hermes.llm.output.chars"] == len("The output had 100000 lines.")
        assert "hermes.llm.output.chars" not in _one(spans, "llm.gpt-4").attributes

    def test_tool_span_without_the_observer_has_only_the_result_size(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_session_start(session_id=SID, model="gpt-4", platform="cli")
        on_pre_tool_call(
            tool_name="read_file",
            args={"path": "a"},
            task_id="t2",
            tool_call_id="tc2",
            session_id=SID,
        )
        on_post_tool_call(
            tool_name="read_file",
            args={"path": "a"},
            result='{"content": "abc"}',
            task_id="t2",
            tool_call_id="tc2",
            session_id=SID,
        )
        on_session_end(
            session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        a = _one(exporter.get_finished_spans(), "tool.read_file").attributes
        assert a["hermes.tool.result.chars"] == len('{"content": "abc"}')
        assert not any(k.startswith("hermes.terminal.") for k in a)

    def test_fans_out_to_every_exporter(self, two_exporter_pipeline):
        exporter_a, exporter_b, _ = two_exporter_pipeline
        _turn(exporter_a)
        for exporter in (exporter_a, exporter_b):
            tool = _one(exporter.get_finished_spans(), "tool.terminal")
            assert tool.attributes["hermes.terminal.output.truncated"] is True
