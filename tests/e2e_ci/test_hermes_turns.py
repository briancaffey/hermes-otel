"""The real ``hermes`` binary through this checkout's plugin (#482).

Three turn shapes, each a separate ``hermes chat`` process against the
scripted provider, asserted on what the OTLP receiver decoded:

* one turn → the ``agent`` root, ``llm.<model>`` under it, ``api.<model>``
  under that, token metrics and the ``hermes.turn.*`` log events;
* a tool-call turn → ``tool.terminal`` inside the trace, the
  ``hermes.tool.call`` event and the tool duration histogram;
* a resumed turn (``chat -r <session>`` in a new process) →
  ``hermes.turn.number == 2`` on the new trace (#466).
"""

from __future__ import annotations

from typing import List, Optional

from e2e_ci._servers import MODEL_ID, OtlpReceiver, Span, text, tool_call

HELLO = "Hello from the scripted provider."


def _root(receiver: OtlpReceiver, session_id: str, turn) -> Span:
    """The session root span of ``session_id``: named ``agent``, no parent."""
    spans = receiver.spans_for_session(session_id)
    roots = [s for s in spans if s.name == "agent" and s.parent_span_id is None]
    assert len(roots) == 1, (
        f"expected one agent root for {session_id}, got {[s.name for s in spans]}\n"
        + turn.describe()
    )
    return roots[0]


def _children(receiver: OtlpReceiver, parent: Span, name: Optional[str] = None) -> List[Span]:
    return [
        s
        for s in receiver.spans
        if s.parent_span_id == parent.span_id and (name is None or s.name == name)
    ]


def _ancestors(receiver: OtlpReceiver, span: Span) -> List[str]:
    by_id = {s.span_id: s for s in receiver.spans}
    names: List[str] = []
    cur = span
    while cur.parent_span_id and cur.parent_span_id in by_id:
        cur = by_id[cur.parent_span_id]
        names.append(cur.name)
    return names


def _ok(turn) -> None:
    assert turn.exit_code == 0, turn.describe()
    assert "OpenTelemetry packages not available" not in turn.stderr, (
        "the Hermes environment lacks the plugin's dependencies (hermes_otel/requirements.txt); "
        "a pip-installed Hermes manages no plugin venv\n" + turn.describe()
    )
    assert turn.session_id, "no `session_id:` line from hermes chat -Q\n" + turn.describe()


def _sum_points(receiver: OtlpReceiver, name: str, **labels):
    return [
        p
        for p in receiver.points
        if p.name == name and all(p.attributes.get(k) == v for k, v in labels.items())
    ]


def _events(receiver: OtlpReceiver, name: str, session_id: str):
    return [
        r
        for r in receiver.logs
        if r.event_name == name and r.attributes.get("hermes.session_id") == session_id
    ]


def test_one_turn_exports_spans_metrics_and_events(hermes):
    turn = hermes.turn("Say hello.", text(HELLO))
    _ok(turn)
    assert HELLO in turn.answer, turn.describe()
    receiver = hermes.receiver
    assert receiver.bad_requests == [], receiver.bad_requests
    sid = turn.session_id

    # The agent loop made exactly one model request; it streamed and asked
    # for usage, which is what the api.* span's token attributes come from.
    agent_requests = [
        r for r in hermes.provider.chat_requests() if not r.body.get("response_format")
    ]
    assert len(agent_requests) == 1, [r.body.get("stream") for r in agent_requests]
    assert agent_requests[0].body.get("model") == MODEL_ID

    root = _root(receiver, sid, turn)
    assert root.attributes["hermes.turn.number"] == 1
    assert root.attributes["hermes.turn.trigger"] == "oneshot"
    assert root.attributes["gen_ai.request.model"] == MODEL_ID
    assert root.attributes["gen_ai.usage.input_tokens"] == 12
    assert root.attributes["gen_ai.usage.output_tokens"] == 7
    assert root.status == "STATUS_CODE_OK"

    (llm,) = _children(receiver, root, f"llm.{MODEL_ID}")
    assert llm.attributes["hermes.turn.number"] == 1
    assert llm.attributes["gen_ai.provider.name"] == "custom"
    assert llm.trace_id == root.trace_id

    (api,) = _children(receiver, llm, f"api.{MODEL_ID}")
    assert api.attributes["gen_ai.usage.input_tokens"] == 12
    assert api.attributes["gen_ai.usage.output_tokens"] == 7
    assert api.attributes["openinference.span.kind"] == "LLM"
    assert api.status == "STATUS_CODE_OK"

    # Token metrics for the main query, by token type (cumulative sums; every
    # export of the process carries the same final value).
    inputs = _sum_points(
        receiver, "hermes.token.usage", model=MODEL_ID, query_source="main", token_type="input"
    )
    outputs = _sum_points(
        receiver, "hermes.token.usage", model=MODEL_ID, query_source="main", token_type="output"
    )
    assert inputs and {p.value for p in inputs} == {12}, inputs
    assert outputs and {p.value for p in outputs} == {7}, outputs
    assert _sum_points(receiver, "hermes.session.count", platform="cli"), [
        p.name for p in receiver.points
    ]

    # Log events: turn start and end, carrying the session and turn number.
    (start,) = _events(receiver, "hermes.turn.start", sid)
    assert start.attributes["hermes.turn.number"] == 1
    assert start.body == "turn 1 started"
    (end,) = _events(receiver, "hermes.turn.end", sid)
    assert end.body == "turn 1 completed"
    assert end.attributes["hermes.turn.final_status"] == "completed"
    # Captured Hermes/plugin logging travels too, as plain records.
    assert any(r.event_name is None for r in receiver.logs)


def test_tool_call_turn_exports_a_tool_span(hermes):
    turn = hermes.turn(
        "Run `echo e2e-marker` in the terminal and tell me what it printed.",
        tool_call("terminal", {"command": "echo e2e-marker"}),
        text("The command printed e2e-marker."),
    )
    _ok(turn)
    receiver = hermes.receiver
    assert receiver.bad_requests == [], receiver.bad_requests
    assert hermes.provider.remaining() == 0, "the scripted tool call was never followed up"
    sid = turn.session_id

    # The tool really ran: Hermes sent its output back as the tool message.
    agent_requests = [
        r for r in hermes.provider.chat_requests() if not r.body.get("response_format")
    ]
    assert len(agent_requests) == 2, [len(r.body.get("messages", [])) for r in agent_requests]
    tool_messages = [m for m in agent_requests[1].body["messages"] if m.get("role") == "tool"]
    assert tool_messages and "e2e-marker" in tool_messages[0].get("content", ""), tool_messages

    root = _root(receiver, sid, turn)
    tools = [s for s in receiver.spans if s.name == "tool.terminal" and s.trace_id == root.trace_id]
    assert len(tools) == 1, [s.name for s in receiver.spans]
    (tool,) = tools
    assert "agent" in _ancestors(receiver, tool), _ancestors(receiver, tool)
    assert tool.attributes["gen_ai.tool.name"] == "terminal"
    assert tool.attributes["hermes.tool.outcome"] == "completed"
    assert tool.attributes["openinference.span.kind"] == "TOOL"
    assert tool.status == "STATUS_CODE_OK"

    # Two model requests → two api.* spans in the trace; the root rolls up both.
    apis = [
        s for s in receiver.spans if s.name == f"api.{MODEL_ID}" and s.trace_id == root.trace_id
    ]
    assert len(apis) >= 2, [s.name for s in receiver.spans]
    assert root.attributes["gen_ai.usage.input_tokens"] == 24
    assert root.attributes["hermes.turn.tool_count"] == 1

    (event,) = _events(receiver, "hermes.tool.call", sid)
    assert event.body == "tool terminal completed"
    assert event.attributes["gen_ai.tool.name"] == "terminal"
    durations = [p for p in receiver.points if p.name == "hermes.tool.duration"]
    assert durations and all(p.attributes.get("tool_name") == "terminal" for p in durations)
    assert {p.value["count"] for p in durations} == {1}


def test_resumed_turn_is_turn_two(hermes):
    """#466: a second ``hermes chat -r <session>`` process numbers its turn 2."""
    first_session = hermes.completed_session()
    if first_session is None:
        first = hermes.turn("Say hello.", text(HELLO))
        _ok(first)
        first_session = first.session_id

    turn = hermes.turn("Say it again.", text("Hello again."), resume=first_session)
    _ok(turn)
    assert turn.session_id == first_session, turn.describe()
    receiver = hermes.receiver

    # Hermes replayed the earlier exchange to the model before the new prompt.
    (request,) = [r for r in hermes.provider.chat_requests() if not r.body.get("response_format")]
    roles = [m.get("role") for m in request.body["messages"]]
    assert roles[-1] == "user" and "assistant" in roles[:-1], roles

    root = _root(receiver, first_session, turn)
    assert root.attributes["hermes.turn.number"] == 2, root.attributes
    (llm,) = _children(receiver, root, f"llm.{MODEL_ID}")
    assert llm.attributes["hermes.turn.number"] == 2
    (start,) = _events(receiver, "hermes.turn.start", first_session)
    assert start.body == "turn 2 started"
    turns = [p for p in receiver.points if p.name == "hermes.session.turns"]
    assert turns and {p.value["sum"] for p in turns} == {2.0}, turns
