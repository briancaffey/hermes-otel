"""The hook-layer golden scenarios (#407): fixed sequences of Hermes hook calls
whose complete telemetry (spans, metric points, events) is recorded under
``tests/golden/hooks/<scenario>__<config>.json``.

Record or refresh with ``UPDATE_GOLDEN=1 uv run --extra dev pytest
tests/unit/test_golden_hooks.py``; a refactor PR may not change a fixture
without listing every changed line (CONTRIBUTING.md, "Golden fixtures").
"""

from __future__ import annotations

import dataclasses
import json
import time
from typing import Any, Callable, Dict, Optional

from helpers.golden import GOLDEN_ROOT, Pipeline, config_for, normalize, three_signal_plugin
from helpers.turns import api_call, api_request_kwargs, api_response_kwargs, run_turn, tool_call

from hermes_otel.hooks import (
    on_api_request_error,
    on_kanban_task_blocked,
    on_kanban_task_claimed,
    on_kanban_task_completed,
    on_kanban_worker_exited,
    on_kanban_worker_spawned,
    on_kanban_worker_stale_claim,
    on_post_api_request,
    on_post_approval_response,
    on_post_auxiliary_call,
    on_post_llm_call,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_approval_request,
    on_pre_auxiliary_call,
    on_pre_llm_call,
    on_pre_tool_call,
    on_session_end,
    on_session_finalize,
    on_session_reset,
    on_session_start,
    on_subagent_start,
    on_subagent_stop,
    on_transform_llm_output,
    on_transform_terminal_output,
)
from hermes_otel.plugin_config import HermesOtelConfig

SID = "20261012_090000_golden"
TURN = f"{SID}:task-1:abc123"

CONFIGS: Dict[str, Callable[[], HermesOtelConfig]] = {
    "full": lambda: config_for("full"),
    "preview": lambda: config_for("preview"),
    "off": lambda: config_for("off"),
    "events": lambda: config_for("preview", log_events=True),
}


def _open_turn(session_id: str = SID, model: str = "gpt-4", first: bool = True) -> None:
    if first:
        on_session_start(session_id=session_id, model=model, platform="cli")
    on_pre_llm_call(
        session_id=session_id,
        user_message="hello",
        conversation_history=[],
        is_first_turn=first,
        model=model,
        platform="cli",
    )


def _close_turn(session_id: str = SID, model: str = "gpt-4", response: str = "hi") -> None:
    on_post_llm_call(
        session_id=session_id,
        user_message="hello",
        assistant_response=response,
        conversation_history=[],
        model=model,
        platform="cli",
    )
    on_session_end(
        session_id=session_id, completed=True, interrupted=False, model=model, platform="cli"
    )


# ── scenarios ──────────────────────────────────────────────────────────


def one_shot_turn(p: Pipeline) -> None:
    """A one-shot CLI turn: root, llm, api, two tools, then the session's end."""
    run_turn(SID, tools=("terminal", "read_file"))
    on_session_finalize(session_id=SID, platform="cli", reason="cli_exit")


def continuation_turn(p: Pipeline) -> None:
    """Turn 2 of a session: no on_session_start, the root is synthesised."""
    run_turn(SID, first=True)
    run_turn(SID, first=False, assistant_response="again")


def api_error_then_retry(p: Pipeline) -> None:
    """A 429 on attempt one, the retried attempt succeeds; then an error with no in-flight span."""
    _open_turn()
    on_pre_api_request(**_api_req(SID, "a1"))
    on_api_request_error(
        task_id="a1",
        session_id=SID,
        platform="cli",
        model="gpt-4",
        provider="openai",
        api_duration=1.25,
        status_code=429,
        retry_count=1,
        max_retries=5,
        retryable=True,
        reason="rate limited",
        error={"type": "RateLimitError", "message": "429 Too Many Requests"},
    )
    api_call(SID, "a1")
    on_api_request_error(
        task_id="never-opened",
        session_id=SID,
        model="gpt-4",
        provider="openai",
        status_code=401,
        retryable=False,
        error={"type": "AuthenticationError", "message": "invalid api key"},
    )
    _close_turn()


def approvals(p: Pipeline) -> None:
    """Granted under a tool call, denied, and timed out."""
    on_session_start(session_id=SID, model="gpt-4", platform="telegram")
    on_pre_tool_call(
        tool_name="bash", args={"command": "rm -r ./build-cache"}, task_id="tc1", session_id=SID
    )
    _approve("once", tool_call_id="tc1", command="rm -r ./build-cache")
    on_post_tool_call(
        tool_name="bash",
        args={"command": "rm -r ./build-cache"},
        result="ok",
        task_id="tc1",
        session_id=SID,
    )
    _approve("deny", tool_call_id="tc2", command="./install.sh --dry-run", pattern_key="run_script")
    _approve(
        "timeout",
        tool_call_id="tc3",
        command="git push --force origin main",
        pattern_key="force_push",
    )
    on_session_end(
        session_id=SID, completed=True, interrupted=False, model="gpt-4", platform="telegram"
    )


def subagent_in_process(p: Pipeline) -> None:
    """A delegated child whose root nests under the parent's live delegation span."""
    _open_turn("parent")
    on_pre_api_request(**_api_req("parent", "papi", tool_count=1))
    on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "research X"}, task_id="d1", session_id="parent"
    )
    on_subagent_start(
        parent_session_id="parent",
        parent_turn_id="turn-1",
        parent_subagent_id="root",
        child_session_id="child-1",
        child_subagent_id="sa-1",
        child_role="researcher",
        child_goal="research X thoroughly",
    )
    _child_turn("child-1")
    on_subagent_stop(
        parent_session_id="parent",
        child_session_id="child-1",
        child_role="researcher",
        child_summary="found the answer",
        child_status="completed",
        duration_ms=4200,
    )
    on_post_tool_call(
        tool_name="delegate_task",
        args={"goal": "research X"},
        result="found the answer",
        task_id="d1",
        session_id="parent",
    )
    on_post_api_request(**_api_resp("parent", "papi", assistant_tool_call_count=1))
    _close_turn("parent")


def subagent_cross_process(p: Pipeline) -> None:
    """A delegated child whose parent span is gone: the child links to its context."""
    _open_turn("parent")
    on_subagent_start(
        parent_session_id="parent",
        child_session_id="child-x",
        child_role="builder",
        child_goal="build it",
    )
    record_ = p.plugin.spans.get_subagent("child-x")
    record_["span"] = None  # another process: only the SpanContext travelled
    _child_turn("child-x")
    on_subagent_stop(
        parent_session_id="parent",
        child_session_id="child-x",
        child_role="builder",
        child_summary="exploded",
        child_status="failed",
        duration_ms=10,
    )
    _close_turn("parent")


def auxiliary_calls(p: Pipeline) -> None:
    """Titling under a turn, a failed compression, and a turn-less call."""
    _open_turn()
    _aux(
        "title_generation",
        "r1",
        usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    )
    _aux("compression", "r2", error="RateLimitError: slow down")
    _aux(
        "kanban_decomposer",
        "r3",
        session_id="",
        usage={"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
    )
    _close_turn()


def kanban(p: Pipeline) -> None:
    """Dispatcher-side claim/spawn/exit/stale-claim; worker-side complete and block."""
    card = dict(
        task_id="t_abc123", board="default", assignee="minimal", run_id=4, profile_name="default"
    )
    on_kanban_task_claimed(**card)
    on_kanban_worker_spawned(**card, worker_pid=4242, workspace_path="/tmp/ws")
    on_kanban_worker_exited(
        **card,
        worker_pid=4242,
        exit_kind="signaled",
        exit_code=-15,
        outcome="timed_out",
        retry_status="retry",
    )
    on_kanban_worker_stale_claim(
        **card, worker_pid=4242, heartbeat_stale=True, retry_status="retry"
    )
    for sid, finish, tool in (
        (
            "worker-a",
            lambda: on_kanban_task_completed(**card, summary="schema designed"),
            "kanban_complete",
        ),
        (
            "worker-b",
            lambda: on_kanban_task_blocked(**card, reason="needs a human"),
            "kanban_block",
        ),
    ):
        on_session_start(session_id=sid, model="gpt-4", platform="cli")
        on_pre_tool_call(tool_name=tool, args={}, task_id="t1", tool_call_id="tc1", session_id=sid)
        finish()
        on_post_tool_call(
            tool_name=tool,
            args={},
            result='{"ok": true}',
            task_id="t1",
            tool_call_id="tc1",
            session_id=sid,
        )
        on_session_end(
            session_id=sid, completed=True, interrupted=False, model="gpt-4", platform="cli"
        )


def skills_and_transforms(p: Pipeline) -> None:
    """A skill load, a terminal tool with the raw-output observer, the llm-output observer."""
    _open_turn()
    on_pre_api_request(**_api_req(SID, "a1", tool_count=2))
    tool_call(SID, "sk1", "skill_view", args={"name": "golden_skill"}, result='{"success": true}')
    on_pre_tool_call(
        tool_name="terminal",
        args={"command": "seq 1 100000"},
        task_id="t1",
        tool_call_id="tc1",
        session_id=SID,
    )
    on_transform_terminal_output(
        command="seq 1 100000",
        output="x" * 200_000,
        returncode=0,
        task_id="t1",
        env_type="local",
        tool_call_id="tc1",
    )
    on_post_tool_call(
        tool_name="terminal",
        args={"command": "seq 1 100000"},
        result=json.dumps({"output": "x" * 30_000, "returncode": 0}),
        task_id="t1",
        tool_call_id="tc1",
        session_id=SID,
    )
    on_post_api_request(**_api_resp(SID, "a1", assistant_tool_call_count=2))
    on_transform_llm_output(
        response_text="final answer", session_id=SID, model="gpt-4", platform="cli", turn_id=TURN
    )
    _close_turn(response="final answer")


def session_reset_chain(p: Pipeline) -> None:
    """``/new``: the old session finalizes, the new one names and links to it."""
    run_turn("old")
    on_session_finalize(session_id="old", platform="cli", reason="new_command")
    on_session_reset(
        session_id="new", reason="new", platform="cli", old_session_id="old", new_session_id="new"
    )
    run_turn("new")


def orphan_sweep(p: Pipeline) -> None:
    """A turn whose end hook never came is finalized as timed_out by the next hook."""
    p.plugin.config = dataclasses.replace(p.plugin.config, root_span_ttl_ms=1_000)
    on_session_start(session_id="s_dead", model="gpt-4", platform="cli")
    on_pre_api_request(**_api_req("s_dead", "dead-api"))
    p.plugin.register_turn("s_dead", started_at=time.perf_counter() - 10.0)
    run_turn("s_live")


def parallel_tools_and_big_prompt(p: Pipeline) -> None:
    """Two tools open at once under one api span; a 30 kB prompt and a 5 kB answer."""
    on_session_start(session_id=SID, model="gpt-4", platform="cli")
    on_pre_llm_call(
        session_id=SID,
        user_message="x" * 30_000,
        conversation_history=[],
        is_first_turn=True,
        model="gpt-4",
        platform="cli",
    )
    on_pre_api_request(
        **_api_req(SID, "a1", tool_count=2, request_char_count=30_000, approx_input_tokens=7_500)
    )
    on_pre_tool_call(tool_name="terminal", args={"command": "ls"}, task_id="t1", session_id=SID)
    on_pre_tool_call(tool_name="read_file", args={"path": "a.txt"}, task_id="t2", session_id=SID)
    on_post_tool_call(
        tool_name="read_file",
        args={"path": "a.txt"},
        result="contents",
        task_id="t2",
        session_id=SID,
    )
    on_post_tool_call(
        tool_name="terminal", args={"command": "ls"}, result="ok", task_id="t1", session_id=SID
    )
    on_post_api_request(**_api_resp(SID, "a1", assistant_tool_call_count=2))
    _close_turn(response="y" * 5_000)


SCENARIOS: Dict[str, Callable[[Pipeline], None]] = {
    "one_shot_turn": one_shot_turn,
    "continuation_turn": continuation_turn,
    "api_error_then_retry": api_error_then_retry,
    "approvals": approvals,
    "subagent_in_process": subagent_in_process,
    "subagent_cross_process": subagent_cross_process,
    "auxiliary_calls": auxiliary_calls,
    "kanban": kanban,
    "skills_and_transforms": skills_and_transforms,
    "session_reset_chain": session_reset_chain,
    "orphan_sweep": orphan_sweep,
    "parallel_tools_and_big_prompt": parallel_tools_and_big_prompt,
}


# ── payload helpers ────────────────────────────────────────────────────


def _api_req(session_id: str, task_id: str, **over: Any) -> Dict[str, Any]:
    return api_request_kwargs(session_id, task_id, max_tokens=1024, **over)


def _api_resp(session_id: str, task_id: str, **over: Any) -> Dict[str, Any]:
    return api_response_kwargs(session_id, task_id, **over)


def _child_turn(child: str) -> None:
    on_session_start(session_id=child, model="gpt-4", platform="cli")
    on_pre_llm_call(
        session_id=child,
        user_message="child work",
        conversation_history=[],
        is_first_turn=True,
        model="gpt-4",
        platform="cli",
    )
    api_call(
        child,
        f"{child}-api",
        response={"usage": {"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150}},
    )
    on_post_llm_call(
        session_id=child,
        user_message="child work",
        assistant_response="child result",
        conversation_history=[],
        model="gpt-4",
        platform="cli",
    )
    on_session_end(
        session_id=child, completed=True, interrupted=False, model="gpt-4", platform="cli"
    )


def _approve(choice: str, *, tool_call_id: str, command: str, pattern_key: str = "rm_rf") -> None:
    common = dict(
        command=command,
        pattern_key=pattern_key,
        pattern_keys=[pattern_key, "danger"],
        session_key="agent:main:telegram:dm:5490634439",
        surface="gateway",
        turn_id=TURN,
        tool_call_id=tool_call_id,
    )
    on_pre_approval_request(description="Delete a directory tree", **common)
    on_post_approval_response(choice=choice, **common)


def _aux(
    task: str,
    request_id: str,
    *,
    session_id: Optional[str] = SID,
    usage: Optional[Dict[str, int]] = None,
    error: Optional[str] = None,
) -> None:
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
        started_at=100.0,
        message_count=2,
    )
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
    payload: Dict[str, Any] = dict(
        base,
        ended_at=100.25,
        api_duration=0.25,
        error=error,
        error_type=None if error is None else error.split(":")[0],
    )
    if error is not None:
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
            usage=usage,
            response={
                "model": "gpt-4o-mini-2024",
                "finish_reason": "stop",
                "assistant_message": {"role": "assistant", "content": "Chat title"},
            },
            assistant_content_chars=10,
            assistant_tool_call_count=0,
        )
    on_post_auxiliary_call(**payload)


# ── recording ──────────────────────────────────────────────────────────


def record(scenario: str, config: str) -> Dict[str, Any]:
    """Run one scenario under one config and return the normalised document."""
    with three_signal_plugin(CONFIGS[config]()) as p:
        SCENARIOS[scenario](p)
        p.plugin.spans.end_all()
        return normalize(
            p.spans.get_finished_spans(), p.metrics.get_metrics_data(), p.logs.get_finished_logs()
        )


def fixture_path(scenario: str, config: str):
    return GOLDEN_ROOT / "hooks" / f"{scenario}__{config}.json"
