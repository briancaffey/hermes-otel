"""Structured events from the hooks (#267): emitted on the plugin's own scope, gated by
``logs.events``, carrying the turn's trace context, with content governed per backend."""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from hermes_otel import hooks
from hermes_otel import log_events as EV
from hermes_otel import log_handler as lh
from hermes_otel.plugin_config import HermesOtelConfig


def _api_kwargs(session_id, task, **extra):
    base = dict(
        task_id=task,
        session_id=session_id,
        platform="cli",
        model="m",
        provider="openrouter",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        message_count=1,
        tool_count=0,
        approx_input_tokens=1,
        request_char_count=1,
        max_tokens=1,
    )
    base.update(extra)
    return base


def _post_api_kwargs(session_id, task, **extra):
    base = dict(
        task_id=task,
        session_id=session_id,
        platform="cli",
        model="m",
        provider="openrouter",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        message_count=1,
        response_model="m",
        api_duration=0.2,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        finish_reason="stop",
        assistant_content_chars=5,
        assistant_tool_call_count=0,
        response_content="hello",
    )
    base.update(extra)
    return base


@pytest.fixture()
def events(inmemory_otel_setup, monkeypatch):
    """A live plugin whose log pipeline exports to an in-memory log exporter, events on, stdlib bridge off."""
    from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    from hermes_otel import redaction

    monkeypatch.setattr(redaction, "_hermes_resolved", True)
    monkeypatch.setattr(redaction, "_hermes_redactor", None)
    _exporter, plugin = inmemory_otel_setup

    def build(cfg: HermesOtelConfig):
        plugin.config = cfg
        log_exporter = InMemoryLogExporter()
        with patch.object(
            lh,
            "build_log_processors",
            return_value=[(SimpleLogRecordProcessor(log_exporter), object())],
        ):
            plugin._init_logs_pipeline(Resource.create({"service.name": "t"}), [])
        assert plugin._logger_provider is not None
        return log_exporter

    try:
        yield plugin, build
    finally:
        lh.uninstall_handler(None)


def _events(exporter, name=None):
    recs = [r for r in exporter.get_finished_logs() if r.log_record.event_name]
    if name:
        recs = [r for r in recs if r.log_record.event_name == name]
    return recs


class TestSwitchAndScope:
    def test_off_by_default_and_no_record_when_off(self, events):
        plugin, build = events
        assert HermesOtelConfig().log_events is False
        exporter = build(HermesOtelConfig(capture_logs=True, log_events=False))
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        hooks.on_pre_llm_call(
            session_id="s",
            user_message="hi",
            conversation_history=[],
            is_first_turn=True,
            model="m",
            platform="cli",
        )
        assert _events(exporter) == []

    def test_events_without_the_stdlib_bridge(self, events, caplog):
        plugin, build = events
        exporter = build(HermesOtelConfig(capture_logs=False, log_events=True))
        # no LoggingHandler of ours on the root logger
        assert not [
            h for h in logging.getLogger().handlers if getattr(h, lh._HANDLER_MARKER, False)
        ]
        logging.getLogger("test.events.noise").info("stdlib line")
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        hooks.on_pre_llm_call(
            session_id="s",
            user_message="hi",
            conversation_history=[],
            is_first_turn=True,
            model="m",
            platform="cli",
        )
        recs = exporter.get_finished_logs()
        assert [r.log_record.event_name for r in recs] == [
            EV.TURN_START
        ]  # and nothing from the root logger
        (rec,) = recs
        assert rec.instrumentation_scope.name == EV.SCOPE_NAME
        assert rec.instrumentation_scope.version == plugin.plugin_version
        r = rec.log_record
        assert r.body == "turn 1 started"
        assert r.severity_text == "INFO" and r.severity_number.value == 9
        assert r.attributes["hermes.turn.number"] == 1
        assert r.attributes["hermes.session_id"] == "s" == r.attributes["gen_ai.conversation.id"]
        assert r.attributes["hermes.platform"] == "cli"
        # trace context of the session root, so the enricher saw it as `context`
        root = plugin.spans.get_session_root("s")
        assert r.trace_id == root.get_span_context().trace_id
        assert r.attributes["hermes.log.attribution"] == "context"

    def test_emit_is_a_no_op_without_a_pipeline_and_never_raises(self, inmemory_otel_setup):
        _exporter, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(log_events=True)
        plugin._logger_provider = None
        assert plugin.emit_event("x", "y", {}) is False

        class Broken:
            def get_logger(self, *a, **k):
                raise RuntimeError("boom")

        plugin._logger_provider = Broken()
        assert plugin.emit_event("x", "y", {"a": None}) is False


class TestEventCatalogue:
    def test_tool_call_severity_and_attributes(self, events):
        plugin, build = events
        exporter = build(HermesOtelConfig(log_events=True))
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        for status, outcome, sev in (
            ("ok", "completed", "INFO"),
            ("blocked", "blocked", "WARN"),
            ("error", "error", "ERROR"),
        ):
            hooks.on_pre_tool_call(
                tool_name="bash", args={"cmd": "ls"}, task_id=f"t-{status}", session_id="s"
            )
            hooks.on_post_tool_call(
                tool_name="bash",
                args={"cmd": "ls"},
                result='{"output": "x"}',
                task_id=f"t-{status}",
                status=status,
                session_id="s",
            )
        recs = _events(exporter, EV.TOOL_CALL)
        assert [(r.log_record.body, r.log_record.severity_text) for r in recs] == [
            ("tool bash completed", "INFO"),
            ("tool bash blocked", "WARN"),
            ("tool bash error", "ERROR"),
        ]
        first = recs[0].log_record.attributes
        assert first["gen_ai.tool.name"] == "bash" and first["hermes.tool.outcome"] == "completed"
        assert isinstance(first[EV.ATTR_TOOL_DURATION_S], float)
        assert first["gen_ai.tool.call.result"]  # content under the default full mode
        # the event sits on the tool span
        tool_spans = (
            [s for s in plugin.spans._spans.values()] if hasattr(plugin.spans, "_spans") else []
        )
        assert (
            recs[0].log_record.trace_id
            == plugin.spans.get_session_root("s").get_span_context().trace_id
        )

    def test_turn_end_severity_follows_final_status(self, events):
        plugin, build = events
        exporter = build(HermesOtelConfig(log_events=True))
        for sid, kwargs, sev, status in (
            ("a", {"completed": True, "interrupted": False}, "INFO", "completed"),
            ("b", {"completed": False, "interrupted": True}, "WARN", "interrupted"),
            ("c", {"completed": False, "interrupted": False, "failed": True}, "ERROR", "failed"),
        ):
            hooks.on_session_start(session_id=sid, model="m", platform="cli")
            hooks.on_pre_llm_call(
                session_id=sid,
                user_message="hi",
                conversation_history=[],
                is_first_turn=True,
                model="m",
                platform="cli",
            )
            hooks.on_session_end(session_id=sid, model="m", platform="cli", **kwargs)
            (rec,) = _events(exporter, EV.TURN_END)[-1:]
            r = rec.log_record
            assert r.severity_text == sev, sid
            assert r.body == f"turn 1 {status}", r.body
            assert r.attributes["hermes.turn.final_status"] == status
            assert "input.value" not in r.attributes and "output.value" not in r.attributes

    def test_approval_denied_is_warn(self, events):
        plugin, build = events
        exporter = build(HermesOtelConfig(log_events=True))
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        for choice, sev in (("once", "INFO"), ("deny", "WARN")):
            hooks.on_pre_approval_request(
                pattern_key=f"rm -rf {choice}", tool_name="bash", session_id="s", task_id="t"
            )
            hooks.on_post_approval_response(
                pattern_key=f"rm -rf {choice}",
                choice=choice,
                tool_name="bash",
                session_id="s",
                task_id="t",
            )
        recs = _events(exporter, EV.APPROVAL_DECISION)
        assert [r.log_record.severity_text for r in recs] == ["INFO", "WARN"]
        assert recs[1].log_record.attributes["hermes.approval.granted"] is False
        assert "pattern_key" not in recs[1].log_record.attributes

    def test_api_error_warn_when_retryable_else_error(self, events):
        plugin, build = events
        exporter = build(HermesOtelConfig(log_events=True))
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        hooks.on_api_request_error(
            task_id="t1",
            session_id="s",
            platform="cli",
            model="m",
            provider="openrouter",
            error={"type": "RateLimitError", "message": "slow down"},
            status_code=429,
            retryable=True,
            retry_count=1,
        )
        hooks.on_api_request_error(
            task_id="t2",
            session_id="s",
            platform="cli",
            model="m",
            provider="openrouter",
            error={
                "type": "AuthenticationError",
                "message": "bad key sk-abcdefghijklmnopqrstuvwxyz",
            },
            status_code=401,
            retryable=False,
        )
        recs = _events(exporter, EV.API_ERROR)
        assert [r.log_record.severity_text for r in recs] == ["WARN", "ERROR"]
        a, b = (r.log_record for r in recs)
        assert (
            a.attributes["error.type"] == "RateLimitError"
            and a.attributes["http.response.status_code"] == 429
        )
        assert (
            a.attributes["hermes.retryable"] is True
            and a.attributes["exception.type"] == "RateLimitError"
        )
        assert (
            "abcdefghijklmnopqrstuvwxyz" not in b.body
            and "abcdefghijklmnopqrstuvwxyz" not in b.attributes["exception.message"]
        )

    def test_subagent_start_and_stop(self, events):
        plugin, build = events
        exporter = build(HermesOtelConfig(log_events=True))
        hooks.on_session_start(session_id="p", model="m", platform="cli")
        hooks.on_subagent_start(
            child_session_id="c1",
            parent_session_id="p",
            child_role="worker",
            child_goal="do the thing",
        )
        hooks.on_subagent_stop(
            child_session_id="c1", parent_session_id="p", child_status="failed", duration_ms=120
        )
        start = _events(exporter, EV.SUBAGENT_START)
        stop = _events(exporter, EV.SUBAGENT_STOP)
        assert len(start) == 1 and len(stop) == 1
        assert start[0].log_record.body == "sub-agent worker started"
        assert start[0].log_record.attributes.get("hermes.subagent.goal") == "do the thing", dict(
            start[0].log_record.attributes
        )
        assert stop[0].log_record.severity_text == "ERROR"
        assert stop[0].log_record.attributes["hermes.subagent.child_session_id"] == "c1"

    def test_session_finalize(self, events):
        plugin, build = events
        exporter = build(HermesOtelConfig(log_events=True))
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        hooks.on_pre_llm_call(
            session_id="s",
            user_message="hi",
            conversation_history=[],
            is_first_turn=True,
            model="m",
            platform="cli",
        )
        hooks.on_session_end(
            session_id="s", model="m", platform="cli", completed=True, interrupted=False
        )
        hooks.on_session_finalize(session_id="s", platform="cli", reason="cli_exit")
        (rec,) = _events(exporter, EV.SESSION_FINALIZE)
        r = rec.log_record
        assert r.body.startswith("session s finalized (cli_exit): 1 turn(s)")
        assert r.attributes["hermes.session.turn_count"] == 1
        assert r.attributes["hermes.session.finalize_reason"] == "cli_exit"

    def test_inference_details_event_carries_request_and_response(self, events):
        plugin, build = events
        exporter = build(HermesOtelConfig(log_events=True))
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        hooks.on_pre_llm_call(
            session_id="s",
            user_message="hi",
            conversation_history=[],
            is_first_turn=True,
            model="m",
            platform="cli",
        )
        hooks.on_pre_api_request(
            **_api_kwargs("s", "t1", messages=[{"role": "user", "content": "hi there"}])
        )
        hooks.on_post_api_request(**_post_api_kwargs("s", "t1"))
        (rec,) = _events(exporter, EV.INFERENCE_DETAILS)
        r = rec.log_record
        assert r.body == "chat m (10/5 tokens)"
        a = r.attributes
        assert a["gen_ai.operation.name"] == "chat" and a["gen_ai.request.model"] == "m"
        assert a["gen_ai.usage.input_tokens"] == 10 and a["gen_ai.usage.output_tokens"] == 5
        assert a["gen_ai.response.finish_reasons"] == ["stop"] or list(
            a["gen_ai.response.finish_reasons"]
        ) == ["stop"]
        assert json.loads(a["gen_ai.input.messages"])[0]["content"] == "hi there"
        assert json.loads(a["gen_ai.output.messages"])[0]["content"] == "hello"
        assert "llm.response.duration_ms" not in a  # OpenInference-only names stay on the span


class TestContentGating:
    def _run_turn(self, build, cfg):
        import uuid

        exporter = build(cfg)
        # Unique ids per run: the plugin singleton keeps span keys across tests, and a
        # reused ``api:<task>`` key would hand the event the previous test's span.
        sid, t1, t2 = (f"{p}-{uuid.uuid4().hex[:8]}" for p in ("s", "t", "u"))
        hooks.on_session_start(session_id=sid, model="m", platform="cli")
        hooks.on_pre_llm_call(
            session_id=sid,
            user_message="hi",
            conversation_history=[],
            is_first_turn=True,
            model="m",
            platform="cli",
        )
        hooks.on_pre_api_request(
            **_api_kwargs(sid, t1, messages=[{"role": "user", "content": "x" * 3000}])
        )
        hooks.on_post_api_request(**_post_api_kwargs(sid, t1, response_content="y" * 3000))
        hooks.on_pre_tool_call(tool_name="bash", args={"cmd": "ls"}, task_id=t2, session_id=sid)
        hooks.on_post_tool_call(
            tool_name="bash",
            args={"cmd": "ls"},
            result="z" * 3000,
            task_id=t2,
            status="ok",
            session_id=sid,
        )
        inf = _events(exporter, EV.INFERENCE_DETAILS)[0].log_record.attributes
        tool = _events(exporter, EV.TOOL_CALL)[0].log_record.attributes
        return inf, tool

    def test_inherit_follows_content_capture_full(self, events):
        _plugin, build = events
        inf, tool = self._run_turn(build, HermesOtelConfig(log_events=True, content_capture="full"))
        assert (
            len(inf["gen_ai.input.messages"]) > 3000 and len(inf["gen_ai.output.messages"]) > 3000
        )
        assert (
            len(tool["gen_ai.tool.call.result"]) == 1200
        )  # the span keeps a tool-output preview even under full

    def test_off_removes_content_but_keeps_metadata(self, events):
        _plugin, build = events
        inf, tool = self._run_turn(
            build, HermesOtelConfig(log_events=True, log_events_content="off")
        )
        assert "gen_ai.input.messages" not in inf and "gen_ai.output.messages" not in inf
        assert inf["gen_ai.usage.input_tokens"] == 10
        assert "gen_ai.tool.call.result" not in tool and tool["gen_ai.tool.name"] == "bash"

    def test_preview_clips_content(self, events):
        _plugin, build = events
        inf, tool = self._run_turn(
            build,
            HermesOtelConfig(log_events=True, log_events_content="preview", preview_max_chars=100),
        )
        assert len(inf["gen_ai.input.messages"]) <= 104 and inf["gen_ai.input.messages"].endswith(
            "..."
        )
        assert len(tool["gen_ai.tool.call.result"]) <= 104

    def test_content_capture_off_means_no_content_even_when_events_say_full(self, events):
        _plugin, build = events
        inf, tool = self._run_turn(
            build,
            HermesOtelConfig(
                log_events=True,
                content_capture="off",
                capture_previews=False,
                capture_full_prompts=False,
                capture_full_responses=False,
                log_events_content="full",
            ),
        )
        # the span never captured content, so there is nothing to put on the event
        content_keys = [
            k for k, v in inf.items() if isinstance(v, str) and ("yyyy" in v or "xxxx" in v)
        ]
        assert content_keys == [], content_keys
        assert "gen_ai.input.messages" not in inf and "gen_ai.tool.call.result" not in tool


class TestPerBackendNarrowing:
    def _provider(self, global_mode, strict_rules):
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor
        from opentelemetry.sdk.resources import Resource

        full, strict = InMemoryLogExporter(), InMemoryLogExporter()
        provider = LoggerProvider(resource=Resource.create({}))
        provider.add_log_record_processor(lh.HermesLogProcessor())
        base = lh.LogRules(events_enabled=True, events_content=global_mode)
        provider.add_log_record_processor(
            lh.BackendLogFilter(
                SimpleLogRecordProcessor(full),
                base,
                "full",
                global_events_content=global_mode,
                preview_chars=10,
            )
        )
        provider.add_log_record_processor(
            lh.BackendLogFilter(
                SimpleLogRecordProcessor(strict),
                strict_rules,
                "strict",
                global_events_content=global_mode,
                preview_chars=10,
            )
        )
        return provider, full, strict

    def _emit(self, provider, attrs, event_name=EV.TOOL_CALL):
        provider.get_logger(EV.SCOPE_NAME).emit(
            event_name=event_name, body="b", attributes=attrs, severity_number=None
        )

    def test_content_off_for_one_backend_leaves_the_other_intact(self):
        provider, full, strict = self._provider(
            "full", lh.LogRules(events_enabled=True, events_content="off")
        )
        self._emit(
            provider, {"gen_ai.tool.name": "bash", "gen_ai.tool.call.result": "secret-ish output"}
        )
        (f,) = full.get_finished_logs()
        (s,) = strict.get_finished_logs()
        assert f.log_record.attributes["gen_ai.tool.call.result"] == "secret-ish output"
        assert "gen_ai.tool.call.result" not in s.log_record.attributes
        assert s.log_record.attributes["gen_ai.tool.name"] == "bash"

    def test_preview_for_one_backend_clips(self):
        provider, full, strict = self._provider(
            "full", lh.LogRules(events_enabled=True, events_content="preview")
        )
        self._emit(provider, {"gen_ai.tool.call.result": "0123456789abcdef"})
        (f,) = full.get_finished_logs()
        (s,) = strict.get_finished_logs()
        assert f.log_record.attributes["gen_ai.tool.call.result"] == "0123456789abcdef"
        assert (
            s.log_record.attributes["gen_ai.tool.call.result"] == "0123456..."
        )  # clip_preview keeps the limit incl. the ellipsis

    def test_events_disabled_for_one_backend_drops_only_events(self):
        provider, full, strict = self._provider("full", lh.LogRules(events_enabled=False))
        self._emit(provider, {"gen_ai.tool.name": "bash"})
        provider.get_logger("agent.loop").emit(body="plain line", attributes={})
        assert len(full.get_finished_logs()) == 2
        bodies = [r.log_record.body for r in strict.get_finished_logs()]
        assert bodies == ["plain line"]

    def test_narrow_rules_per_backend_content_from_yaml_mapping(self):
        base = lh.rules_from_config(HermesOtelConfig(log_events=True, log_events_content="inherit"))
        narrowed = lh.narrow_rules(
            base, {"events": {"content": "off"}}, where="b", content_mode="full"
        )
        assert narrowed.events_content == "off" and narrowed.events_enabled is True
