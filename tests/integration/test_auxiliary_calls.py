"""Auxiliary LLM calls (#389): ``pre_auxiliary_call`` / ``post_auxiliary_call``.

Hermes fires the pair once per physical provider attempt of every auxiliary
task (titling, compression, MoA reference models and aggregator, vision,
approval, the kanban decomposer, the goal judge ...), with the
``pre/post_api_request`` payload shape plus ``aux_task`` and the identity of
the main turn it runs under (empty for turn-less callers).
"""

from __future__ import annotations

import pytest
from _helpers import parent_span_id as _parent_span_id
from _helpers import span_by_name as _span_by_name
from opentelemetry.trace import StatusCode

from hermes_otel.hooks import (
    on_post_api_request,
    on_post_auxiliary_call,
    on_post_llm_call,
    on_pre_api_request,
    on_pre_auxiliary_call,
    on_pre_llm_call,
    on_session_end,
    on_session_finalize,
    on_session_start,
    on_subagent_start,
)

SID = "s-aux"


def _open_turn(session_id=SID, model="gpt-4", turn_id=None, first=True):
    if first:
        on_session_start(session_id=session_id, model=model, platform="cli")
    on_pre_llm_call(
        session_id=session_id,
        user_message="hello",
        conversation_history=[],
        is_first_turn=first,
        model=model,
        platform="cli",
        **({"turn_id": turn_id} if turn_id else {}),
    )


def _close_turn(session_id=SID, model="gpt-4"):
    on_post_llm_call(
        session_id=session_id,
        user_message="hello",
        assistant_response="hi",
        conversation_history=[],
        model=model,
        platform="cli",
    )
    on_session_end(
        session_id=session_id, completed=True, interrupted=False, model=model, platform="cli"
    )


def _base(task="title_generation", session_id=SID, request_id="", started_at=100.0, **extra):
    """The payload fields Hermes puts on BOTH hooks of one attempt."""
    base = dict(
        session_id=session_id,
        task_id="",
        turn_id="",
        platform="cli" if session_id else "",
        aux_task=task,
        api_request_id=request_id,
        retry_count=0,
        api_call_count=1,
        model="gpt-4o-mini",
        provider="openai",
        base_url="",
        api_mode="chat_completions",
        streaming=False,
        started_at=started_at,
        message_count=2,
    )
    base.update(extra)
    return base


def _pre(**overrides):
    base = _base(**overrides)
    on_pre_auxiliary_call(
        **base,
        request_messages=[{"role": "user", "content": "name this chat"}],
        system_prompt="You name chats.",
        tool_count=0,
        approx_input_tokens=12,
        request_char_count=48,
        max_tokens=32,
        request={"method": "POST", "body": {}},
    )
    return base


def _post(base, *, usage=None, error=None, duration=0.25):
    payload = dict(
        base,
        ended_at=base["started_at"] + duration,
        api_duration=duration,
        error=error,
        error_type=None if error is None else error.split(":")[0],
    )
    if error is not None or base.get("streaming"):
        payload.update(
            finish_reason=None,
            response_model=None,
            usage=None,
            response=None,
            assistant_content_chars=0,
            assistant_tool_call_count=0,
        )
    else:
        payload.update(
            finish_reason="stop",
            response_model="gpt-4o-mini-2024",
            usage=usage or {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            response={
                "model": "gpt-4o-mini-2024",
                "finish_reason": "stop",
                "assistant_message": {"role": "assistant", "content": "Chat title"},
            },
            assistant_content_chars=10,
            assistant_tool_call_count=0,
        )
    on_post_auxiliary_call(**payload)


def _aux_spans(exporter):
    return [
        s
        for s in exporter.get_finished_spans()
        if s.attributes.get("hermes.query_source") == "auxiliary"
    ]


class TestSpanShape:
    def test_nests_under_the_owning_turn(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_turn()
        base = _pre()
        _post(base)
        _close_turn()

        spans = exporter.get_finished_spans()
        aux = _span_by_name(spans, "api.gpt-4o-mini")
        llm = _span_by_name(spans, "llm.gpt-4")
        root = _span_by_name(spans, "agent")
        assert _parent_span_id(aux) == llm.context.span_id
        assert aux.context.trace_id == root.context.trace_id
        a = aux.attributes
        assert a["hermes.query_source"] == "auxiliary"
        assert a["hermes.auxiliary.task"] == "title_generation"
        assert a["openinference.span.kind"] == "LLM"
        assert a["gen_ai.operation.name"] == "chat"
        assert a["gen_ai.conversation.id"] == SID
        assert a["llm.model_name"] == "gpt-4o-mini"
        assert a["gen_ai.provider.name"] == "openai"
        assert a["gen_ai.response.model"] == "gpt-4o-mini-2024"
        assert a["gen_ai.usage.input_tokens"] == 12
        assert a["llm.token_count.completion"] == 3
        assert a["gen_ai.response.finish_reasons"] == ("stop",)
        assert a["llm.response.duration_ms"] == 250.0
        assert a["llm.request.max_tokens"] == 32
        assert aux.status.status_code == StatusCode.OK

    def test_turn_less_call_is_its_own_root(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        base = _pre(task="kanban_decomposer", session_id="", request_id="req-9")
        _post(base)

        aux = _span_by_name(exporter.get_finished_spans(), "api.gpt-4o-mini")
        assert _parent_span_id(aux) is None
        assert "gen_ai.conversation.id" not in aux.attributes
        assert "session.id" not in aux.attributes
        assert aux.attributes["hermes.auxiliary.task"] == "kanban_decomposer"

    def test_call_after_the_turn_links_to_the_ended_root(self, inmemory_otel_setup):
        """#588: title generation can start after on_session_end closed the root."""
        exporter, _ = inmemory_otel_setup
        _open_turn()
        _close_turn()
        base = _pre(request_id="req-title")
        _post(base)

        spans = exporter.get_finished_spans()
        aux = _span_by_name(spans, "api.gpt-4o-mini")
        root = _span_by_name(spans, "agent")
        assert _parent_span_id(aux) is None
        assert aux.context.trace_id != root.context.trace_id
        assert aux.attributes["hermes.span.after_turn"] is True
        assert aux.attributes["hermes.query_source"] == "auxiliary"
        assert aux.attributes["hermes.auxiliary.task"] == "title_generation"
        assert aux.attributes["gen_ai.usage.input_tokens"] == 12
        assert [(ln.context.trace_id, ln.context.span_id) for ln in aux.links] == [
            (root.context.trace_id, root.context.span_id)
        ]
        assert aux.links[0].attributes["hermes.link"] == "turn_root"

    def test_in_turn_call_has_no_after_turn_link(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_turn()
        _close_turn()
        # The next turn of the same session: its auxiliary call nests normally.
        _open_turn()
        _post(_pre(request_id="req-2"))
        _close_turn()
        aux = _span_by_name(exporter.get_finished_spans(), "api.gpt-4o-mini")
        assert _parent_span_id(aux) is not None
        assert "hermes.span.after_turn" not in aux.attributes
        assert not aux.links

    def test_session_without_a_closed_root_gets_no_link(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _post(_pre(session_id="s-never-rooted", request_id="req-3"))
        aux = _span_by_name(exporter.get_finished_spans(), "api.gpt-4o-mini")
        assert _parent_span_id(aux) is None
        assert "hermes.span.after_turn" not in aux.attributes
        assert not aux.links

    def test_post_without_pre_after_the_turn_is_linked_too(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_turn()
        _close_turn()
        _post(_base(request_id="late-title"))

        spans = exporter.get_finished_spans()
        aux = _span_by_name(spans, "api.gpt-4o-mini")
        root = _span_by_name(spans, "agent")
        assert _parent_span_id(aux) is None
        assert aux.attributes["hermes.span.after_turn"] is True
        assert aux.attributes["hermes.query_source"] == "auxiliary"
        assert [ln.context.span_id for ln in aux.links] == [root.context.span_id]
        assert aux.links[0].attributes["hermes.link"] == "turn_root"

    def test_full_prompt_and_response_capture(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_turn()
        base = _pre()
        _post(base)
        _close_turn()
        a = _span_by_name(exporter.get_finished_spans(), "api.gpt-4o-mini").attributes
        assert "name this chat" in a["gen_ai.input.messages"]
        assert a["input.value"] == a["gen_ai.input.messages"]
        assert a["gen_ai.system_instructions"] == "You name chats."
        assert a["output.value"] == "Chat title"
        assert "Chat title" in a["gen_ai.output.messages"]

    def test_preview_off_captures_no_content(self, inmemory_otel_setup):
        from hermes_otel.plugin_config import HermesOtelConfig

        exporter, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(capture_previews=False)
        _open_turn()
        base = _pre()
        _post(base)
        _close_turn()
        a = _span_by_name(exporter.get_finished_spans(), "api.gpt-4o-mini").attributes
        assert "input.value" not in a
        assert "output.value" not in a
        assert "gen_ai.system_instructions" not in a

    def test_is_not_pushed_as_a_parent(self, inmemory_otel_setup):
        # Nothing runs *inside* an auxiliary attempt: a main-loop api request
        # that follows nests under the llm span, not under the aux span.
        exporter, _ = inmemory_otel_setup
        _open_turn()
        base = _pre()
        _post(base)
        on_pre_api_request(
            task_id="t1",
            session_id=SID,
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=2,
            tool_count=0,
            approx_input_tokens=10,
            request_char_count=40,
            max_tokens=100,
        )
        on_post_api_request(
            task_id="t1",
            session_id=SID,
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=1.0,
            finish_reason="stop",
            message_count=2,
            response_model="gpt-4",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            assistant_content_chars=5,
            assistant_tool_call_count=0,
        )
        _close_turn()
        spans = exporter.get_finished_spans()
        main = _span_by_name(spans, "api.gpt-4")
        llm = _span_by_name(spans, "llm.gpt-4")
        assert _parent_span_id(main) == llm.context.span_id
        assert main.attributes["hermes.query_source"] == "main"


class TestOutcomes:
    def test_failed_attempt_is_error_with_exception_event(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_turn()
        base = _pre(request_id="req-err")
        _post(base, error="RateLimitError: 429 slow down")
        _close_turn()

        spans = exporter.get_finished_spans()
        aux = _span_by_name(spans, "api.gpt-4o-mini")
        assert aux.status.status_code == StatusCode.ERROR
        assert aux.attributes["error.type"] == "RateLimitError"
        assert "gen_ai.usage.input_tokens" not in aux.attributes
        exc = [e for e in aux.events if e.name == "exception"]
        assert exc and exc[0].attributes["exception.type"] == "RateLimitError"
        root = _span_by_name(spans, "agent")
        assert root.attributes["hermes.turn.auxiliary.api_call_count"] == 1
        assert root.attributes["hermes.turn.auxiliary.error_count"] == 1
        # A failed auxiliary call is not the turn's failure.
        assert root.status.status_code == StatusCode.OK
        assert "error.type" not in root.attributes

    def test_streamed_attempt_reports_no_usage(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_turn()
        base = _pre(task="moa_aggregator", streaming=True)
        _post(base)
        _close_turn()
        aux = _span_by_name(exporter.get_finished_spans(), "api.gpt-4o-mini")
        assert aux.status.status_code == StatusCode.OK
        assert aux.attributes["gen_ai.request.stream"] is True
        assert "gen_ai.usage.input_tokens" not in aux.attributes

    def test_post_without_pre_still_records_the_spend(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_turn()
        _post(_base(request_id="late"))
        _close_turn()
        aux = _span_by_name(exporter.get_finished_spans(), "api.gpt-4o-mini")
        assert aux.attributes["gen_ai.usage.input_tokens"] == 12
        assert aux.attributes["hermes.auxiliary.task"] == "title_generation"

    def test_fail_open_on_garbage(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        on_pre_auxiliary_call()  # no fields at all
        on_post_auxiliary_call(usage="not a dict", api_duration="soon", started_at=object())
        on_post_auxiliary_call(aux_task=None, session_id=None)
        assert exporter.get_finished_spans() is not None


class TestTurnRollup:
    def test_root_reports_auxiliary_beside_the_turn_totals(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_turn()
        on_pre_api_request(
            task_id="t1",
            session_id=SID,
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=2,
            tool_count=0,
            approx_input_tokens=10,
            request_char_count=40,
            max_tokens=100,
        )
        on_post_api_request(
            task_id="t1",
            session_id=SID,
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=1.0,
            finish_reason="stop",
            message_count=2,
            response_model="gpt-4",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            assistant_content_chars=5,
            assistant_tool_call_count=0,
        )
        for i, task in enumerate(("moa_reference", "moa_reference", "title_generation")):
            base = _pre(task=task, request_id=f"r{i}")
            _post(base)
        _close_turn()

        root = _span_by_name(exporter.get_finished_spans(), "agent")
        a = root.attributes
        # Main-loop totals untouched by the three auxiliary attempts.
        assert a["gen_ai.usage.input_tokens"] == 100
        assert a["gen_ai.usage.output_tokens"] == 50
        assert a["hermes.turn.api_call_count"] == 1
        assert a["hermes.turn.auxiliary.api_call_count"] == 3
        assert a["hermes.turn.auxiliary.input_tokens"] == 36
        assert a["hermes.turn.auxiliary.output_tokens"] == 9
        assert "hermes.turn.auxiliary.error_count" not in a

    def test_after_turn_call_does_not_bleed_into_the_next_turn(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        T1 = f"{SID}:t:1"
        _open_turn(turn_id=T1)
        _close_turn()
        _post(_pre(request_id="req-title", turn_id=T1))
        # Accounted nowhere: turn 1's roll-up is exported, turn 2 is not its owner.
        assert plugin.sessions.peek(SID) is None
        _open_turn(turn_id=f"{SID}:t:2", first=False)
        _close_turn()

        roots = [s for s in exporter.get_finished_spans() if s.name == "agent"]
        assert len(roots) == 2
        for root in roots:
            assert "hermes.turn.auxiliary.api_call_count" not in root.attributes
        aux = _span_by_name(exporter.get_finished_spans(), "api.gpt-4o-mini")
        assert aux.attributes["hermes.span.after_turn"] is True

    def test_after_finalize_call_does_not_recreate_the_session(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        _open_turn()
        _close_turn()
        on_session_finalize(session_id=SID, platform="cli", reason="shutdown")
        before = plugin.sessions.active_count()
        _post(_pre(request_id="req-late"))
        _post(_base(request_id="req-late-post-only"))
        assert plugin.sessions.peek(SID) is None
        assert plugin.sessions.active_count() == before
        aux = [s for s in _aux_spans(exporter)]
        assert len(aux) == 2
        assert all(s.attributes["hermes.span.after_turn"] is True for s in aux)

    def test_next_turns_preflight_compression_counts_on_that_turn(self, inmemory_otel_setup):
        """Preflight compression runs before the next turn's root opens; its
        turn_id names the new turn, so it is not an after-turn call."""
        exporter, _ = inmemory_otel_setup
        _open_turn(turn_id=f"{SID}:t:1")
        _close_turn()
        T2 = f"{SID}:t:2"
        _post(_pre(task="compression", request_id="req-compress", turn_id=T2))
        _open_turn(turn_id=T2, first=False)
        _close_turn()

        aux = _span_by_name(exporter.get_finished_spans(), "api.gpt-4o-mini")
        assert "hermes.span.after_turn" not in aux.attributes
        assert not aux.links
        roots = [s for s in exporter.get_finished_spans() if s.name == "agent"]
        turn2 = max(roots, key=lambda r: r.attributes["hermes.turn.number"])
        assert turn2.attributes["hermes.turn.auxiliary.api_call_count"] == 1

    def test_moa_fan_out_lands_under_one_turn(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        _open_turn()
        for i, (task, model) in enumerate(
            (
                ("moa_reference", "ref-a"),
                ("moa_reference", "ref-b"),
                ("moa_aggregator", "agg"),
            )
        ):
            base = _pre(task=task, request_id=f"m{i}", model=model)
            _post(base)
        _close_turn()
        spans = exporter.get_finished_spans()
        root = _span_by_name(spans, "agent")
        aux = _aux_spans(exporter)
        assert len(aux) == 3
        assert {s.context.trace_id for s in aux} == {root.context.trace_id}
        assert sorted(s.attributes["hermes.auxiliary.task"] for s in aux) == [
            "moa_aggregator",
            "moa_reference",
            "moa_reference",
        ]
        assert {s.name for s in aux} == {"api.ref-a", "api.ref-b", "api.agg"}


class TestMetricsAndEvents:
    def _points(self, reader, name):
        out = []
        for rm in reader.get_metrics_data().resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == name:
                        for dp in m.data.data_points:
                            out.append((dict(dp.attributes), dp.value))
        return out

    def test_auxiliary_counter_and_query_source_labels(self, inmemory_otel_with_metrics):
        _, reader, _ = inmemory_otel_with_metrics
        _open_turn()
        on_pre_api_request(
            task_id="t1",
            session_id=SID,
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=2,
            tool_count=0,
            approx_input_tokens=10,
            request_char_count=40,
            max_tokens=100,
        )
        on_post_api_request(
            task_id="t1",
            session_id=SID,
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=1.0,
            finish_reason="stop",
            message_count=2,
            response_model="gpt-4",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            assistant_content_chars=5,
            assistant_tool_call_count=0,
        )
        base = _pre(request_id="ok")
        _post(base)
        base = _pre(request_id="bad", started_at=101.0)
        _post(base, error="APIError: boom")
        _close_turn()

        counts = self._points(reader, "hermes.auxiliary.count")
        by_status = {a["status"]: v for a, v in counts}
        assert by_status == {"ok": 1, "error": 1}
        assert all(a["aux_task"] == "title_generation" for a, _ in counts)
        assert all(a["model"] == "gpt-4o-mini" and a["provider"] == "openai" for a, _ in counts)
        assert all(
            set(a) == {"aux_task", "status", "model", "provider", "profile"} for a, _ in counts
        )

        tokens = self._points(reader, "hermes.token.usage")
        by_source = {}
        for a, v in tokens:
            by_source.setdefault(a["query_source"], 0)
            by_source[a["query_source"]] += v
        assert by_source == {"main": 150, "auxiliary": 15}

        usage = self._points(reader, "hermes.model.usage")
        assert sorted(a["query_source"] for a, _ in usage) == ["auxiliary", "main"]

    def test_subagent_sessions_report_query_source_subagent(self, inmemory_otel_with_metrics):
        exporter, reader, _ = inmemory_otel_with_metrics
        _open_turn("parent")
        on_pre_api_request(
            task_id="p1",
            session_id="parent",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=2,
            tool_count=1,
            approx_input_tokens=10,
            request_char_count=40,
            max_tokens=100,
        )
        on_subagent_start(
            parent_session_id="parent",
            child_session_id="child",
            child_role="researcher",
            child_goal="look things up",
        )
        _open_turn("child", model="gpt-4o-mini")
        on_pre_api_request(
            task_id="c1",
            session_id="child",
            platform="subagent",
            model="gpt-4o-mini",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=2,
            tool_count=0,
            approx_input_tokens=10,
            request_char_count=40,
            max_tokens=100,
        )
        on_post_api_request(
            task_id="c1",
            session_id="child",
            platform="subagent",
            model="gpt-4o-mini",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.5,
            finish_reason="stop",
            message_count=2,
            response_model="gpt-4o-mini",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            assistant_content_chars=5,
            assistant_tool_call_count=0,
        )
        _close_turn("child", model="gpt-4o-mini")
        child_api = _span_by_name(exporter.get_finished_spans(), "api.gpt-4o-mini")
        assert child_api.attributes["hermes.query_source"] == "subagent"
        tokens = self._points(reader, "hermes.token.usage")
        assert {a["query_source"] for a, _ in tokens} == {"subagent"}

    def test_event_per_attempt(self, inmemory_otel_setup, monkeypatch):
        from unittest.mock import patch

        from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor
        from opentelemetry.sdk.resources import Resource

        from hermes_otel import log_handler as lh
        from hermes_otel import redaction
        from hermes_otel.plugin_config import HermesOtelConfig
        from hermes_otel.testing import attach_logs

        monkeypatch.setattr(redaction, "_hermes_resolved", True)
        monkeypatch.setattr(redaction, "_hermes_redactor", None)
        _exporter, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(log_events=True)
        log_exporter = InMemoryLogExporter()
        with patch.object(
            lh,
            "build_log_processors",
            return_value=[(SimpleLogRecordProcessor(log_exporter), object())],
        ):
            attach_logs(plugin, [], resource=Resource.create({"service.name": "t"}))
        try:
            _open_turn()
            base = _pre(request_id="ev-ok")
            _post(base)
            base = _pre(request_id="ev-bad", started_at=101.0)
            _post(base, error="APIError: boom")
            _close_turn()
            recs = [
                r.log_record
                for r in log_exporter.get_finished_logs()
                if r.log_record.event_name == "hermes.auxiliary.call"
            ]
            assert len(recs) == 2
            ok, bad = recs
            assert ok.severity_text == "INFO"
            assert ok.body == "auxiliary title_generation gpt-4o-mini (12/3 tokens)"
            assert ok.attributes["hermes.auxiliary.task"] == "title_generation"
            assert ok.attributes["gen_ai.usage.output_tokens"] == 3
            assert bad.severity_text == "ERROR"
            assert bad.attributes["error.type"] == "APIError"
            assert bad.body.endswith(": APIError")
        finally:
            lh.uninstall_handler(None)


@pytest.mark.parametrize("fn", [on_pre_auxiliary_call, on_post_auxiliary_call])
def test_disabled_tracer_is_a_no_op(fn, monkeypatch):
    from unittest.mock import MagicMock, patch

    from hermes_otel.plugin_config import HermesOtelConfig

    tracer = MagicMock()
    tracer.is_enabled = False
    tracer.config = HermesOtelConfig()
    with patch("hermes_otel.tracer.get_tracer", return_value=tracer):
        fn(**_base())
    tracer.start_span.assert_not_called()
    tracer.end_span.assert_not_called()
