"""Fitting ``conversation_history`` under ``conversation_history_max_chars`` (#650).

Both ``input.value`` and ``gen_ai.input.messages`` must stay valid JSON when
the history is over the cap: whole messages are dropped, never a slice of the
serialised string.
"""

import json

import pytest

from hermes_otel.hooks.attributes import _fit_conversation_history


def _history(n, size=300, system=True):
    msgs = [{"role": "system", "content": "be brief"}] if system else []
    roles = ("user", "assistant")
    msgs += [{"role": roles[i % 2], "content": f"m{i} " + "x" * size} for i in range(n)]
    return msgs


def test_under_cap_is_untouched():
    history = _history(3, size=10)
    fitted = _fit_conversation_history(history, 20_000)
    assert json.loads(fitted.input_value) == history
    assert fitted.dropped == 0
    assert fitted.original_chars is None
    assert json.loads(fitted.genai_messages)[1]["parts"][0]["content"].startswith("m0")


def test_empty_history_is_none():
    assert _fit_conversation_history([], 100) is None
    assert _fit_conversation_history(None, 100) is None


@pytest.mark.parametrize("max_chars", [2_000, 5_000, 20_000])
def test_over_cap_drops_oldest_whole_messages(max_chars):
    history = _history(100)
    fitted = _fit_conversation_history(history, max_chars)
    hermes = json.loads(fitted.input_value)
    genai = json.loads(fitted.genai_messages)
    assert len(fitted.input_value) <= max_chars
    assert len(fitted.genai_messages) <= max_chars
    assert fitted.original_chars == len(json.dumps(history))
    # System kept, then one placeholder, then the newest messages intact.
    assert hermes[0] == history[0]
    assert hermes[1]["content"] == f"[{fitted.dropped} earlier messages dropped by hermes-otel]"
    assert hermes[2:] == history[-(len(hermes) - 2) :]
    assert 1 + fitted.dropped + len(hermes) - 2 == len(history)
    # The GenAI shape drops the same messages.
    assert len(genai) == len(hermes)
    assert genai[1]["parts"][0]["content"] == hermes[1]["content"]
    assert genai[-1]["parts"][0]["content"] == history[-1]["content"]


def test_system_message_is_dropped_when_it_alone_is_too_big():
    history = [{"role": "system", "content": "s" * 5_000}] + _history(5, size=50, system=False)
    fitted = _fit_conversation_history(history, 1_000)
    hermes = json.loads(fitted.input_value)
    assert hermes[0]["content"].startswith("[")
    assert all(m["role"] != "system" for m in hermes[1:])
    assert hermes[-1] == history[-1]


def test_huge_newest_message_has_its_strings_clipped():
    history = _history(3, size=10) + [{"role": "user", "content": "y" * 50_000}]
    fitted = _fit_conversation_history(history, 500)
    hermes = json.loads(fitted.input_value)
    genai = json.loads(fitted.genai_messages)
    assert len(fitted.input_value) <= 500 and len(fitted.genai_messages) <= 500
    assert fitted.dropped == len(history) - 1
    assert hermes[0]["content"] == "[4 earlier messages dropped by hermes-otel]"
    assert hermes[1]["role"] == "user" and hermes[1]["content"].endswith("...")
    assert genai[1]["parts"][0]["content"].endswith("...")


def test_single_huge_message_has_no_placeholder():
    fitted = _fit_conversation_history([{"role": "user", "content": "x" * 5_000}], 200)
    assert fitted.dropped == 0
    assert len(json.loads(fitted.input_value)) == 1


def test_nothing_fits_returns_none():
    assert _fit_conversation_history([{"role": "user", "content": "x" * 50}], 5) is None


def test_one_dropped_message_is_singular():
    history = [{"role": "user", "content": "x" * 400}, {"role": "user", "content": "hi"}]
    fitted = _fit_conversation_history(history, 200)
    assert (
        json.loads(fitted.input_value)[0]["content"] == "[1 earlier message dropped by hermes-otel]"
    )


def test_non_list_history_is_wrapped():
    fitted = _fit_conversation_history("just a string", 1_000)
    assert json.loads(fitted.input_value) == ["just a string"]
