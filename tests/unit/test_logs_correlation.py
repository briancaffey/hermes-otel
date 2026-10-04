"""The test that would have caught D1 (#240): a line logged during a turn carries the
turn's trace id and the api span's span id, the session, no host internals, no secrets."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from hermes_otel import hooks
from hermes_otel import log_handler as lh
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin


def _fake(prefix: str, body: str) -> str:
    """A credential-shaped string assembled at runtime (never a literal key in source)."""
    return prefix + body


def _api_kwargs(session_id, task):
    return dict(
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


@pytest.fixture()
def turn(inmemory_otel_setup, monkeypatch):
    """A live plugin whose logs pipeline exports to an in-memory log exporter."""
    from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    from hermes_otel import redaction

    monkeypatch.setattr(redaction, "_hermes_resolved", True)
    monkeypatch.setattr(redaction, "_hermes_redactor", None)
    _exporter, plugin = inmemory_otel_setup
    plugin.config = HermesOtelConfig(capture_logs=True, log_level="INFO")
    log_exporter = InMemoryLogExporter()
    fake_backend = object()
    with patch.object(
        lh,
        "build_log_processors",
        return_value=[(SimpleLogRecordProcessor(log_exporter), fake_backend)],
    ):
        plugin._init_logs_pipeline(Resource.create({"service.name": "t"}), [])
    assert plugin._logger_provider is not None
    lg = logging.getLogger("test.correlation.agent")
    lg.propagate = True
    try:
        yield plugin, log_exporter, lg
    finally:
        lh.uninstall_handler(None)


def test_a_line_logged_during_a_turn_is_correlated_and_clean(turn):
    plugin, log_exporter, lg = turn
    hooks.on_session_start(session_id="s-1", model="m", platform="cli")
    hooks.on_pre_api_request(**_api_kwargs("s-1", "t-1"))
    api_span = plugin.spans.get_current_parent("s-1")
    assert api_span is not None
    lg.info(
        "calling openrouter with Authorization: Bearer "
        + _fake("sk-or-v1-", "abcdefghijklmnopqrstuvwxyz0123"),
        extra={"hermes_home": "/Users/me/.hermes", "session_tag": " [s-1]"},
    )
    (rec,) = [
        r for r in log_exporter.get_finished_logs() if r.log_record.body.startswith("calling")
    ]
    r = rec.log_record
    ctx = api_span.get_span_context()
    assert format(r.trace_id, "032x") == format(ctx.trace_id, "032x")
    assert r.span_id == ctx.span_id
    assert r.attributes["hermes.session_id"] == "s-1"
    assert r.attributes["gen_ai.conversation.id"] == "s-1"
    assert r.attributes["hermes.platform"] == "cli"
    assert r.attributes["hermes.log.attribution"] == "session_tag"
    assert "hermes_home" not in r.attributes and "session_tag" not in r.attributes
    assert "abcdefghijklmnopqrstuvwxyz0123" not in r.body
    assert r.body.startswith("calling openrouter with Authorization: Bearer sk-or-")


def test_a_worker_line_under_two_sessions_stays_unattributed(turn):
    plugin, log_exporter, lg = turn
    for sid in ("a", "b"):
        hooks.on_session_start(session_id=sid, model="m", platform="cli")
        hooks.on_pre_api_request(**_api_kwargs(sid, f"t-{sid}"))
    assert plugin.spans.single_active_session() is None
    lg.info("thread pool line with no session on it")
    (rec,) = [r for r in log_exporter.get_finished_logs() if "thread pool" in r.log_record.body]
    r = rec.log_record
    assert not r.trace_id
    assert "hermes.session_id" not in r.attributes
    assert "hermes.log.attribution" not in r.attributes
    # but a line Hermes tagged with its session is attributed exactly
    lg.info("tagged line", extra={"session_tag": " [b]"})
    (tagged,) = [r for r in log_exporter.get_finished_logs() if r.log_record.body == "tagged line"]
    assert tagged.log_record.attributes["hermes.session_id"] == "b"
    assert tagged.log_record.attributes["hermes.log.attribution"] == "session_tag"
    assert format(tagged.log_record.trace_id, "032x") == format(
        plugin.spans.get_session_root("b").get_span_context().trace_id, "032x"
    )
