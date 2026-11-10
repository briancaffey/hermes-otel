"""Integration (#653): a terminal turn carrying credentials reaches no backend and no live-store row unmasked.

The provider is wired the way ``build_pipeline`` wires it: the live span
processor first, then one processor per backend whose exporter is wrapped in
``_ContentRedactingExporter``. The hooks are driven with the kwargs Hermes
passes.
"""

from __future__ import annotations

import json

import pytest

from hermes_otel import hooks
from hermes_otel import redaction as r
from hermes_otel.live_store import LiveStore
from hermes_otel.live_store_registry import get_live_store
from hermes_otel.plugin_config import HermesOtelConfig

BEARER = "eyJhbGciOiJIUzI1NiJ9" + ".payloadpayload.sigsigsig"
GITHUB = "ghp_" + "Z" * 36
OPENAI = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
SECRETS = (BEARER, GITHUB, OPENAI)
COMMAND = (
    f"curl -H 'Authorization: Bearer {BEARER}' https://api.example; export GITHUB_TOKEN={GITHUB}"
)
RESULT = json.dumps({"output": f"OPENAI_API_KEY={OPENAI}\ndone", "exit_code": 0, "error": None})


@pytest.fixture()
def redacting_pipeline(tmp_path, monkeypatch):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import hermes_otel.live_store_registry as ls
    import hermes_otel.tracer as tracer_mod
    from hermes_otel.pipeline import _ContentRedactingExporter, _LiveSpanProcessor
    from hermes_otel.testing import attach_pipeline
    from hermes_otel.tracer import HermesOTelPlugin

    monkeypatch.setattr(r, "_hermes_resolved", True)
    monkeypatch.setattr(r, "_hermes_redactor", None)
    monkeypatch.delenv("HERMES_REDACT_SECRETS", raising=False)

    ls._LIVE_STORE = LiveStore(db_path=str(tmp_path / "live.db"))
    store = get_live_store()
    provider = TracerProvider(resource=Resource.create({"service.name": "redaction-test"}))
    provider.add_span_processor(_LiveSpanProcessor(store))
    backends = [InMemorySpanExporter(), InMemorySpanExporter()]
    for exp in backends:
        provider.add_span_processor(SimpleSpanProcessor(_ContentRedactingExporter(exp)))

    plugin = HermesOTelPlugin()
    attach_pipeline(plugin, tracer=provider.get_tracer("redaction-test"), live_active=True)
    plugin.config = HermesOtelConfig(dashboard_live=True, content_capture="full")

    prev = tracer_mod._tracer
    tracer_mod._tracer = plugin
    try:
        yield store, backends
    finally:
        tracer_mod._tracer = prev
        if ls._LIVE_STORE is not None:
            ls._LIVE_STORE.close()
        ls._LIVE_STORE = None
        provider.shutdown()


def _leaks(attrs) -> list:
    return [
        (k, s)
        for k, v in attrs.items()
        for s in SECRETS
        if s in (v if isinstance(v, str) else json.dumps(v, default=str))
    ]


def test_terminal_turn_is_masked_in_every_backend_and_the_live_store(redacting_pipeline):
    store, backends = redacting_pipeline
    hooks.on_session_start(session_id="s1", model="m", platform="cli")
    hooks.on_pre_tool_call(
        tool_name="terminal", args={"command": COMMAND}, task_id="t1", session_id="s1"
    )
    hooks.on_post_tool_call(
        tool_name="terminal",
        args={"command": COMMAND},
        result=RESULT,
        task_id="t1",
        session_id="s1",
    )
    hooks.on_session_end(
        session_id="s1", completed=True, interrupted=False, model="m", platform="cli"
    )
    store.flush()

    for exp in backends:
        spans = exp.get_finished_spans()
        tool = next(s for s in spans if s.name.startswith("tool."))
        assert tool.attributes.get("hermes.tool.command"), "the hook set no command"
        for s in spans:
            assert _leaks(dict(s.attributes)) == [], s.name
        assert json.loads(tool.attributes["output.value"])["exit_code"] == 0

    a, b = (
        sorted(s.attributes.get("hermes.tool.command", "") for s in exp.get_finished_spans())
        for exp in backends
    )
    assert a == b  # every backend gets the same masked value

    rows = store.spans()
    assert rows, "nothing reached the live store"
    for row in rows:
        assert _leaks(row.get("attributes") or {}) == [], row.get("name")
