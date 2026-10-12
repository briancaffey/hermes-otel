"""Fake Hermes turns: the hook payloads a real run sends, in one place, so the
integration drivers, the e2e files and the golden recorder agree on them."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

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

DEFAULT_USAGE = {"prompt_tokens": 10, "completion_tokens": 5}


def api_request_kwargs(session_id: str, task_id: str, **over) -> Dict[str, Any]:
    """The ``pre_api_request`` payload (every documented field) with overrides."""
    base = dict(
        task_id=task_id,
        session_id=session_id,
        platform="cli",
        model="gpt-4",
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        message_count=1,
        tool_count=0,
        approx_input_tokens=10,
        request_char_count=40,
        max_tokens=0,
    )
    base.update(over)
    return base


def api_response_kwargs(session_id: str, task_id: str, **over) -> Dict[str, Any]:
    """The ``post_api_request`` payload with overrides; ``usage`` defaults to 10/5."""
    base = dict(
        task_id=task_id,
        session_id=session_id,
        platform="cli",
        model="gpt-4",
        provider="openai",
        base_url="",
        api_mode="chat",
        api_call_count=1,
        api_duration=0.1,
        finish_reason="stop",
        message_count=1,
        response_model="gpt-4",
        usage=dict(DEFAULT_USAGE),
        assistant_content_chars=5,
        assistant_tool_call_count=0,
    )
    base.update(over)
    return base


_COMMON = (
    "platform",
    "model",
    "provider",
    "base_url",
    "api_mode",
    "api_call_count",
    "message_count",
)


def api_call(
    session_id: str,
    task_id: str,
    *,
    request: Optional[Dict[str, Any]] = None,
    response: Optional[Dict[str, Any]] = None,
    **common,
) -> None:
    """One provider request: ``pre_api_request`` then ``post_api_request``.

    ``common`` sets fields both payloads share (model, provider, base_url,
    api_mode, ...); ``request`` / ``response`` override one side only."""
    shared = {k: v for k, v in common.items() if k in _COMMON}
    pre = {k: v for k, v in common.items() if k not in _COMMON}
    on_pre_api_request(
        **api_request_kwargs(session_id, task_id, **shared, **pre, **(request or {}))
    )
    on_post_api_request(**api_response_kwargs(session_id, task_id, **shared, **(response or {})))


def tool_call(
    session_id: str, task_id: str, name: str = "terminal", args=None, result="ok", **extra
):
    args = {"command": "ls"} if args is None else args
    on_pre_tool_call(tool_name=name, args=args, task_id=task_id, session_id=session_id, **extra)
    on_post_tool_call(
        tool_name=name, args=args, result=result, task_id=task_id, session_id=session_id, **extra
    )


def run_turn(
    session_id: str,
    *,
    first: bool = True,
    end: bool = True,
    llm: bool = True,
    model: str = "gpt-4",
    platform: str = "cli",
    provider: str = "openai",
    base_url: str = "",
    api_mode: str = "chat",
    user_message: str = "hello",
    assistant_response: str = "hi",
    usage: Optional[Dict[str, Any]] = None,
    max_tokens: Any = 0,
    tools: Sequence[str] = (),
    api_calls: int = 1,
    task_ids: Optional[Sequence[str]] = None,
    completed: bool = True,
    interrupted: bool = False,
) -> None:
    """One Hermes turn as the hooks see it.

    ``first`` fires ``on_session_start`` (Hermes does so only on a session's
    first turn); ``llm`` wraps the API calls in ``pre/post_llm_call``; ``tools``
    run between the first API call's request and response (one tool span per
    name, task ids ``t0, t1, ...``); ``end`` fires ``on_session_end``."""
    if first:
        on_session_start(session_id=session_id, model=model, platform=platform)
    if llm:
        on_pre_llm_call(
            session_id=session_id,
            user_message=user_message,
            conversation_history=[],
            is_first_turn=first,
            model=model,
            platform=platform,
        )
    if task_ids is None:
        task_ids = (
            [f"api-{session_id}"]
            if api_calls == 1
            else [f"api-{session_id}-{i}" for i in range(api_calls)]
        )
    common = dict(
        model=model, platform=platform, provider=provider, base_url=base_url, api_mode=api_mode
    )
    usage = dict(DEFAULT_USAGE if usage is None else usage)
    for i, task_id in enumerate(task_ids):
        on_pre_api_request(
            **api_request_kwargs(
                session_id, task_id, **common, tool_count=len(tools), max_tokens=max_tokens
            )
        )
        if i == 0:
            for n, name in enumerate(tools):
                tool_call(session_id, f"t{n}", name)
        on_post_api_request(
            **api_response_kwargs(
                session_id,
                task_id,
                **common,
                response_model=model,
                usage=dict(usage),
                assistant_content_chars=len(assistant_response),
                assistant_tool_call_count=len(tools),
            )
        )
    if llm:
        on_post_llm_call(
            session_id=session_id,
            user_message=user_message,
            assistant_response=assistant_response,
            conversation_history=[],
            model=model,
            platform=platform,
        )
    if end:
        on_session_end(
            session_id=session_id,
            completed=completed,
            interrupted=interrupted,
            model=model,
            platform=platform,
        )
