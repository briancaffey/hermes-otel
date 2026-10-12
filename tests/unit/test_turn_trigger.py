"""``hermes.turn.trigger``: what started a turn (#394)."""

from __future__ import annotations

import pytest

from hermes_otel.helpers import TURN_TRIGGERS, turn_trigger


def _env(**values):
    return lambda name: values.get(name, "")


@pytest.mark.parametrize(
    "platform, env, expected",
    [
        ("cli", {}, "user"),
        ("tui", {}, "user"),
        ("telegram", {}, "user"),
        ("acp", {}, "user"),
        ("subagent", {}, "subagent"),
        ("cron", {}, "cron"),
        ("", {"HERMES_CRON_SESSION": "1"}, "cron"),
        # a kanban worker is `hermes chat -q`: platform cli, marked by env
        ("cli", {"HERMES_KANBAN_TASK": "t_1", "HERMES_SINGLE_QUERY_SESSION": "1"}, "kanban"),
        ("a2a", {}, "a2a"),
        ("cli", {"HERMES_SESSION_SOURCE": "a2a", "HERMES_SINGLE_QUERY_SESSION": "1"}, "a2a"),
        ("api_server", {}, "api"),
        ("cli", {"HERMES_SINGLE_QUERY_SESSION": "1"}, "oneshot"),
        ("curator", {}, "curator"),
        ("batch", {}, "batch"),
        ("gateway_hygiene", {}, "system"),
        ("", {}, "user"),
    ],
)
def test_classification(platform, env, expected):
    assert turn_trigger(platform, env=_env(**env)) == expected
    assert expected in TURN_TRIGGERS


def test_registry_rejoin_wins():
    assert turn_trigger("cli", is_subagent=True) == "subagent"


def test_root_span_carries_the_trigger(inmemory_otel_setup, monkeypatch):
    from hermes_otel.hooks import on_session_end, on_session_start

    exporter, _ = inmemory_otel_setup
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_9")
    on_session_start(session_id="k1", model="m", platform="cli")
    on_session_end(session_id="k1", completed=True, interrupted=False, model="m", platform="cli")
    (root,) = [s for s in exporter.get_finished_spans() if s.name == "agent"]
    assert root.attributes["hermes.turn.trigger"] == "kanban"
