"""Attributes come from reported data or are absent (#153, #155, #156)."""

from __future__ import annotations

from unittest.mock import MagicMock

from _helpers import one as _one

from hermes_otel import hooks


def _api(session_id, task, model="m", provider="openrouter", response_model=None):
    base = dict(
        task_id=task,
        session_id=session_id,
        platform="cli",
        model=model,
        provider=provider,
        base_url="",
        api_mode="chat",
        api_call_count=1,
        message_count=1,
        tool_count=0,
        approx_input_tokens=1,
        request_char_count=1,
        max_tokens=1,
    )
    hooks.on_pre_api_request(**base)
    hooks.on_post_api_request(
        task_id=task,
        session_id=session_id,
        platform="cli",
        model=model,
        provider=provider,
        base_url="",
        api_mode="chat",
        api_call_count=1,
        api_duration=0.1,
        finish_reason="stop",
        message_count=1,
        response_model=response_model,
        usage={"prompt_tokens": 1, "output_tokens": 1},
        assistant_content_chars=1,
        assistant_tool_call_count=0,
    )


def _end(sid, **kw):
    args = dict(session_id=sid, completed=True, interrupted=False, model="m", platform="cli")
    args.update(kw)
    hooks.on_session_end(**args)


class TestProviderIsNeverThePlatform:
    def test_root_carries_platform_and_gets_provider_from_api_calls(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        hooks.on_session_start(session_id="s", model="m", platform="telegram")
        _api("s", "t1", provider="anthropic", response_model="claude-x")
        _end("s", platform="telegram")
        agent = _one(exporter.get_finished_spans(), "agent")
        a = dict(agent.attributes)
        assert a["hermes.platform"] == "telegram"
        assert a["gen_ai.provider.name"] == a["gen_ai.system"] == a["llm.provider"] == "anthropic"
        assert a["gen_ai.response.model"] == "claude-x"

    def test_root_without_api_calls_has_no_provider_and_no_response_model(
        self, inmemory_otel_setup
    ):
        exporter, _ = inmemory_otel_setup
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        _end("s")
        a = dict(_one(exporter.get_finished_spans(), "agent").attributes)
        assert a["hermes.platform"] == "cli"
        for key in (
            "llm.provider",
            "gen_ai.provider.name",
            "gen_ai.system",
            "gen_ai.response.model",
        ):
            assert key not in a, key

    def test_llm_span_provider_only_once_reported(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        hooks.on_pre_llm_call(
            session_id="s",
            user_message="hi",
            conversation_history=[],
            is_first_turn=True,
            model="m",
            platform="cli",
        )
        _api("s", "t1", provider="openrouter", response_model="m-v2")
        hooks.on_post_llm_call(
            session_id="s",
            user_message="hi",
            assistant_response="ok",
            conversation_history=[],
            model="m",
            platform="cli",
        )
        _end("s")
        llm = dict(_one(exporter.get_finished_spans(), "llm.m").attributes)
        # The pre-call attributes had no provider; the post-call ones report what the API said.
        assert llm["gen_ai.provider.name"] == "openrouter"
        assert llm["gen_ai.response.model"] == "m-v2"
        assert llm.get("llm.provider") != "cli"

    def test_message_count_label_is_the_real_provider(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        _api("s", "t1", provider="anthropic")
        plugin.record_metric = MagicMock()
        hooks.on_post_llm_call(
            session_id="s",
            user_message="hi",
            assistant_response="ok",
            conversation_history=[],
            model="m",
            platform="cli",
        )
        plugin.record_metric.assert_any_call(
            "message_count", 1, {"model": "m", "provider": "anthropic"}
        )


class TestNoPlaceholders:
    def test_api_span_response_model_only_when_reported(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        _api("s", "t1", response_model=None)
        _api("s", "t2", response_model="m-served")
        _end("s")
        spans = [s for s in exporter.get_finished_spans() if s.name == "api.m"]
        assert "gen_ai.response.model" not in spans[0].attributes
        assert spans[1].attributes["gen_ai.response.model"] == "m-served"

    def test_subagent_without_role(self, inmemory_otel_setup):
        exporter, plugin = inmemory_otel_setup
        hooks.on_session_start(session_id="p", model="m", platform="cli")
        hooks.on_subagent_start(parent_session_id="p", child_session_id="c", child_goal="g")
        plugin.record_metric = MagicMock()
        hooks.on_subagent_stop(
            parent_session_id="p", child_session_id="c", child_status="completed"
        )
        _end("p")
        span = _one(exporter.get_finished_spans(), "subagent")
        assert "hermes.subagent.role" not in span.attributes
        assert "gen_ai.agent.name" not in span.attributes
        labels = next(
            c.args[2] for c in plugin.record_metric.call_args_list if c.args[0] == "subagent_count"
        )
        assert labels["role"] == "unknown"

    def test_approval_without_pattern_key(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        turn = "s:task:abcd1234"
        hooks.on_pre_approval_request(command="ls", turn_id=turn, tool_call_id="tc")
        hooks.on_post_approval_response(
            command="ls", choice="once", turn_id=turn, tool_call_id="tc"
        )
        _end("s")
        span = _one(exporter.get_finished_spans(), "approval")
        assert "hermes.approval.pattern_key" not in span.attributes
        assert span.attributes["hermes.approval.choice"] == "once"


class TestReportedTurnOutcome:
    def test_failed_turn_uses_hermes_verdict(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        _end("s", completed=True, failed=True, turn_exit_reason="session_persistence_failed")
        agent = _one(exporter.get_finished_spans(), "agent")
        a = dict(agent.attributes)
        assert a["hermes.session.failed"] is True
        assert a["hermes.turn.final_status"] == "failed"
        assert a["hermes.turn.exit_reason"] == "session_persistence_failed"
        assert agent.status.status_code.name == "ERROR"

    def test_completed_turn_is_unchanged(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        _end("s", turn_exit_reason="completed")
        agent = _one(exporter.get_finished_spans(), "agent")
        assert agent.attributes["hermes.turn.final_status"] == "completed"
        assert agent.attributes["hermes.session.failed"] is False
        assert agent.status.status_code.name == "OK"

    def test_interrupted_is_not_an_error(self, inmemory_otel_setup):
        exporter, _ = inmemory_otel_setup
        hooks.on_session_start(session_id="s", model="m", platform="cli")
        _end("s", completed=False, interrupted=True, turn_exit_reason="interrupted_by_user")
        agent = _one(exporter.get_finished_spans(), "agent")
        assert agent.attributes["hermes.turn.final_status"] == "interrupted"
        assert agent.attributes["hermes.turn.exit_reason"] == "interrupted_by_user"
        assert agent.status.status_code.name == "OK"
