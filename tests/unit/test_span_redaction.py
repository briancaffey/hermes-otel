"""Secret redaction on span content (#653): which attributes, JSON validity, events, the switch."""

from __future__ import annotations

import gc
import json
import re

import pytest
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.trace import SpanContext, TraceFlags

from hermes_otel import pipeline, semconv
from hermes_otel import redaction as r
from hermes_otel.pipeline import redact_span

# Assembled at runtime so the repo never holds a credential-shaped literal.
SECRETS = {
    "openai": "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "bearer": "eyJhbGciOiJIUzI1NiJ9" + ".payloadpayload.sigsigsig",
    "github": "ghp_" + "Z" * 36,
    "aws": "AKIA" + "QWERTYUIOPASDFGH",
    "postgres": "s3cr3t" + "Passw0rd",
}
TOOL_RESULT = (
    f"OPENAI_API_KEY={SECRETS['openai']}\n"
    f"Authorization: Bearer {SECRETS['bearer']}\n"
    f"token {SECRETS['github']}\n"
    f"aws {SECRETS['aws']}\n"
    f"DATABASE_URL=postgres://app:{SECRETS['postgres']}@db:5432/app\n"
)
COMMAND = (
    f"curl -H 'Authorization: Bearer {SECRETS['bearer']}' https://api.example "
    f"&& export GITHUB_TOKEN={SECRETS['github']}"
)


@pytest.fixture(autouse=True)
def _builtin_redactor(monkeypatch):
    """The plugin's built-in set, switched on, whatever the host has installed."""
    monkeypatch.setattr(r, "_hermes_resolved", True)
    monkeypatch.setattr(r, "_hermes_redactor", None)
    monkeypatch.delenv("HERMES_REDACT_SECRETS", raising=False)


def _span(attrs=None, events=()):
    return ReadableSpan(
        name="x",
        context=SpanContext(1, 2, is_remote=False, trace_flags=TraceFlags(1)),
        attributes=attrs or {},
        events=list(events),
    )


def _leaked(value) -> list:
    text = value if isinstance(value, str) else json.dumps(list(value))
    return [name for name, secret in SECRETS.items() if secret in text]


# One entry per attribute family the hooks emit with free text in it.
FAMILIES = {
    "tool.*": {
        "input.value": json.dumps({"command": COMMAND}),
        "gen_ai.tool.call.arguments": json.dumps({"command": COMMAND}),
        "output.value": json.dumps({"output": TOOL_RESULT, "exit_code": 0}),
        "gen_ai.tool.call.result": json.dumps({"output": TOOL_RESULT, "exit_code": 0}),
        "hermes.tool.command": COMMAND,
        "hermes.tool.target": f"https://app:{SECRETS['postgres']}@host/x",
        "error.message": TOOL_RESULT,
    },
    "api.* (both conventions)": {
        "input.value": json.dumps([{"role": "tool", "content": TOOL_RESULT}]),
        "gen_ai.input.messages": json.dumps(
            [{"role": "tool", "parts": [{"type": "text", "content": TOOL_RESULT}]}]
        ),
        "gen_ai.system_instructions": json.dumps([{"type": "text", "content": TOOL_RESULT}]),
        "output.value": TOOL_RESULT,
        "gen_ai.output.messages": json.dumps(
            [{"role": "assistant", "parts": [{"type": "text", "content": TOOL_RESULT}]}]
        ),
    },
    "llm.*": {
        "input.value": json.dumps([{"role": "user", "content": TOOL_RESULT}]),
        "gen_ai.input.messages": json.dumps([{"role": "user", "content": TOOL_RESULT}]),
        "output.value": TOOL_RESULT,
    },
    "agent root": {
        "input.value": TOOL_RESULT,
        "hermes.turn.tool_commands": COMMAND,
        "hermes.turn.tool_targets": f"postgres://app:{SECRETS['postgres']}@db/app",
    },
    "approval / human input / subagent / kanban": {
        "hermes.approval.command": COMMAND,
        "hermes.approval.description": TOOL_RESULT,
        "hermes.human_input.prompt": TOOL_RESULT,
        "hermes.subagent.goal": TOOL_RESULT,
        "hermes.subagent.summary": TOOL_RESULT,
        "hermes.kanban.summary": TOOL_RESULT,
        "hermes.kanban.reason": TOOL_RESULT,
    },
}


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_every_secret_is_masked_in_each_attribute_family(family):
    attrs = FAMILIES[family]
    out = redact_span(_span(attrs)).attributes
    for key, value in attrs.items():
        assert _leaked(value), f"fixture for {key} carries no secret"
        assert _leaked(out[key]) == [], f"{family} {key} leaked {_leaked(out[key])}"
        if value.lstrip().startswith(("{", "[")):
            json.loads(out[key])  # still a JSON document


def test_ids_model_names_and_numbers_are_never_touched():
    attrs = {
        "input.value": TOOL_RESULT,
        "gen_ai.request.model": "token-name",
        "gen_ai.response.id": SECRETS["openai"],
        "hermes.session_id": "password=" + "hunter2hunter2",
        "gen_ai.usage.input_tokens": 12,
    }
    out = redact_span(_span(attrs)).attributes
    assert _leaked(out["input.value"]) == []
    for key in ("gen_ai.request.model", "gen_ai.response.id", "hermes.session_id"):
        assert out[key] == attrs[key]
    assert out["gen_ai.usage.input_tokens"] == 12


def test_sequence_valued_content_is_masked_element_by_element():
    out = redact_span(_span({"gen_ai.input.messages": (TOOL_RESULT, "plain")})).attributes
    assert _leaked(out["gen_ai.input.messages"]) == []
    assert out["gen_ai.input.messages"][1] == "plain"


def test_a_clean_span_is_returned_as_the_same_object_with_its_json_untouched():
    clean = json.dumps([{"role": "user", "content": "héllo"}])  # default separators, \u escape
    span = _span({"input.value": clean, "gen_ai.input.messages": clean})
    assert redact_span(span) is span
    assert span.attributes["input.value"] == clean


def test_redaction_runs_once_per_span_and_the_cache_lets_spans_go(monkeypatch):
    calls = []
    real = r.redact_text
    monkeypatch.setattr(r, "redact_text", lambda t: calls.append(1) or real(t))
    dirty, clean = _span({"output.value": TOOL_RESULT}), _span({"output.value": "ok"})
    first = redact_span(dirty)
    assert redact_span(dirty) is first  # live store, backend A, backend B: one pass
    assert redact_span(clean) is clean and redact_span(clean) is clean
    assert len(calls) == 2
    del dirty, clean, first
    gc.collect()
    assert len(pipeline._redacted) == 0


def test_span_event_attributes_are_masked():
    ev = Event("exception", {"exception.message": TOOL_RESULT, "exception.type": "HTTPError"})
    out = redact_span(_span({}, [ev]))
    attrs = dict(out.events[0].attributes)
    assert _leaked(attrs["exception.message"]) == []
    assert attrs["exception.type"] == "HTTPError"
    assert out.events[0].timestamp == ev.timestamp


def test_redact_secrets_false_turns_it_off(monkeypatch):
    monkeypatch.setenv("HERMES_REDACT_SECRETS", "false")
    span = _span({"output.value": TOOL_RESULT, "hermes.tool.command": COMMAND})
    assert redact_span(span) is span


def test_span_content_is_redacted_but_metadata_is_preserved():
    secret = "Authorization: Bearer " + "x" * 30
    json_secret = '{"authorization": "' + secret + '"}'
    span = _span(
        {
            "input.value": json_secret,
            "gen_ai.input.messages": [secret],
            "gen_ai.request.model": "token-name",
            "gen_ai.response.id": "sk-proj-not-content",
        }
    )

    redacted = redact_span(span)
    assert "xxxxxxxx" not in redacted.attributes["input.value"]
    assert "xxxxxxxx" not in redacted.attributes["gen_ai.input.messages"][0]
    assert json.loads(redacted.attributes["input.value"])["authorization"] != secret
    assert redacted.attributes["gen_ai.request.model"] == "token-name"
    assert redacted.attributes["gen_ai.response.id"] == "sk-proj-not-content"


class TestRedactContent:
    """``redaction.redact_content``: the JSON-safe wrapper the span and log paths share."""

    def test_json_keys_give_context_to_the_values_under_them(self):
        args = json.dumps(
            {"command": "deploy", "api_key": "Zq8opaqueValue9x", "password": "hunter2hunter2"}
        )
        out = json.loads(r.redact_content(args))
        assert out["command"] == "deploy"
        assert "Zq8opaqueValue9x" not in out["api_key"]
        assert "hunter2hunter2" not in out["password"]

    def test_a_greedy_redactor_cannot_break_the_document(self, monkeypatch):
        """Hermes's ``KEY=\\S+`` rule runs through an escaped newline and the closing quote."""
        greedy = re.compile(r"([A-Z_]*(?:KEY|TOKEN))=\S+")
        monkeypatch.setattr(r, "_hermes_redactor", lambda t: greedy.sub(r"\1=***", t))
        result = json.dumps(
            {
                "output": f"OPENAI_API_KEY={SECRETS['openai']}\nGITHUB_TOKEN={SECRETS['github']}\nok",
                "exit_code": 0,
                "error": None,
            }
        )
        with pytest.raises(ValueError):  # the raw pass alone breaks the document
            json.loads(greedy.sub(r"\1=***", result))
        out = json.loads(r.redact_content(result))
        assert out["exit_code"] == 0 and out["error"] is None
        assert out["output"].endswith("\nok")  # the lines after a secret survive
        assert _leaked(out["output"]) == []

    def test_plain_text_and_non_json_brackets(self):
        assert _leaked(r.redact_content(TOOL_RESULT)) == []
        assert _leaked(r.redact_content("[not json " + TOOL_RESULT)) == []
        assert r.redact_content("nothing here") == "nothing here"

    def test_log_attributes_keep_json_valid_too(self, monkeypatch):
        greedy = re.compile(r"([A-Z_]*KEY)=\S+")
        monkeypatch.setattr(r, "_hermes_redactor", lambda t: greedy.sub(r"\1=***", t))
        attrs = {"gen_ai.tool.call.result": json.dumps({"o": "API_KEY=abc123456789\nx", "n": 1})}
        r.redact_attributes(attrs)
        assert json.loads(attrs["gen_ai.tool.call.result"])["n"] == 1


def test_sensitive_attributes_are_registry_names_and_cover_the_content_rows():
    names = set(semconv.ATTRIBUTE_NAMES)
    assert semconv.SENSITIVE_ATTRIBUTES <= names
    for row in semconv.ROWS:
        if row.group != "content" or not row.type.startswith("string") or row.where == "backend":
            continue
        for name in row.names:
            if name.endswith(".mime_type"):
                continue
            assert (
                name in semconv.SENSITIVE_ATTRIBUTES
            ), f"{name} carries content but is not redacted"
