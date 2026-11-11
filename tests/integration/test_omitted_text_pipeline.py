"""Integration (#669): with ``content_capture: off`` and the three switches
false, a turn's command lines, paths, URLs and error text reach no backend
exporter and no live-store row, as spans or as events.

The settings are read from a ``hermes_otel.yaml`` by the real loader and the
plugin is started through ``HermesOTelPlugin.init()`` (the real
``build_pipeline`` wiring: the live store, then a batch processor per backend)
with only the OTLP exporter classes replaced by collectors. The hooks are
driven with the kwargs Hermes passes.
"""

from __future__ import annotations

import json

import pytest

from hermes_otel import hooks
from hermes_otel import redaction as r
from hermes_otel.plugin_config import HermesOtelConfig, load_config

COMMAND = "grep -r acme-payroll /srv/customers/acme-corp"
PATH = "/srv/customers/acme-corp/ledger-2026.csv"
URL = "https://intranet.acme-corp.example/payroll?id=7781"
TOOL_ERROR = "permission denied: /srv/customers/acme-corp/vault.key"
API_ERROR = "tenant acme-corp exceeded its quota"
SUMMARY = "acme-corp ledger could not be reconciled"
MARKERS = (COMMAND, PATH, URL, TOOL_ERROR, API_ERROR, SUMMARY, "acme-corp", "acme-payroll")
STRICT = dict(capture_tool_command=False, capture_tool_target=False, capture_error_message=False)
YAML = """\
host_metrics: false
logs:
  events:
    enabled: true
backends:
  - type: otlp
    endpoint: http://127.0.0.1:9
"""


class _Collector:
    exported: list = []

    def __init__(self, **kwargs):
        pass

    def export(self, batch):
        type(self).exported.extend(batch)
        return None

    def force_flush(self, *a, **k):
        return True

    def shutdown(self, *a, **k):
        return None


class _Spans(_Collector):
    exported: list = []


class _Logs(_Collector):
    exported: list = []


class _Metrics(_Collector):
    exported: list = []


def _run_turn(tmp_path, monkeypatch, dashboard_live=True, **overrides):
    """One turn with a shell command, a file read, a URL fetch, a failing
    tool, a failed API request and a failed sub-agent. Returns what was
    exported and what the live store holds."""
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    import hermes_otel.live_store_registry as ls
    import hermes_otel.log_handler as log_handler
    import hermes_otel.pipeline as pipeline_mod
    import hermes_otel.tracer as tracer_mod

    for cls in (_Spans, _Logs, _Metrics):
        cls.exported = []
    monkeypatch.setattr(r, "_hermes_resolved", True)
    monkeypatch.setattr(r, "_hermes_redactor", None)
    monkeypatch.setattr(pipeline_mod, "OTLPSpanExporter", _Spans)
    monkeypatch.setattr(pipeline_mod, "OTLPMetricExporter", _Metrics)
    monkeypatch.setattr(log_handler, "OTLPLogExporter", _Logs)
    monkeypatch.setattr(
        pipeline_mod, "PeriodicExportingMetricReader", lambda exporter, **kw: InMemoryMetricReader()
    )
    monkeypatch.setattr("hermes_otel.live_store._default_db_path", lambda: str(tmp_path / "l.db"))
    monkeypatch.setattr(ls, "_LIVE_STORE", None)

    path = tmp_path / "hermes_otel.yaml"
    overrides["dashboard_live"] = dashboard_live
    path.write_text(YAML + "".join(f"{k}: {json.dumps(v)}\n" for k, v in overrides.items()))
    cfg = load_config(path=path)
    assert cfg.log_events and len(cfg.backends) == 1
    plugin = tracer_mod.HermesOTelPlugin(config=cfg)
    prev = tracer_mod._tracer
    try:
        assert plugin.init() is True
        tracer_mod._tracer = plugin
        sid = "s-669"
        hooks.on_session_start(session_id=sid, model="gpt-4", platform="cli")
        hooks.on_pre_llm_call(
            session_id=sid,
            user_message="reconcile the ledger",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        calls = (
            ("terminal", {"command": COMMAND}, json.dumps({"output": "3 matches", "exit_code": 0})),
            ("read_file", {"path": PATH}, json.dumps({"content": "a,b,c"})),
            ("web_extract", {"url": URL}, json.dumps({"content": "ok"})),
            ("write_file", {"path": PATH, "content": "x"}, json.dumps({"error": TOOL_ERROR})),
        )
        for i, (tool, args, result) in enumerate(calls):
            hooks.on_pre_tool_call(tool_name=tool, args=args, task_id=f"t{i}", session_id=sid)
            hooks.on_post_tool_call(
                tool_name=tool, args=args, result=result, task_id=f"t{i}", session_id=sid
            )
        hooks.on_api_request_error(
            task_id="never-opened",
            session_id=sid,
            model="gpt-4",
            provider="openai",
            status_code=429,
            retryable=False,
            error={"type": "RateLimitError", "message": API_ERROR},
        )
        hooks.on_subagent_start(
            parent_session_id=sid,
            child_session_id="child-669",
            child_role="builder",
            child_goal="reconcile acme-corp",
        )
        hooks.on_subagent_stop(
            parent_session_id=sid,
            child_session_id="child-669",
            child_role="builder",
            child_summary=SUMMARY,
            child_status="failed",
            duration_ms=10,
        )
        hooks.on_post_llm_call(
            session_id=sid,
            user_message="reconcile the ledger",
            assistant_response="done",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        hooks.on_session_end(
            session_id=sid, completed=True, interrupted=False, model="gpt-4", platform="cli"
        )
        plugin.pipeline.tracer_provider.force_flush()
        plugin.pipeline.logger_provider.force_flush()
        store = ls.get_live_store() if cfg.dashboard_live else None
        if store is not None:
            store.flush()
        return {
            "spans": list(_Spans.exported),
            "logs": list(_Logs.exported),
            "store_spans": store.spans() if store is not None else [],
            "store_logs": store.logs() if store is not None else [],
        }
    finally:
        for cls in (_Spans, _Logs, _Metrics):
            cls.exported = []  # hold no span past the test
        tracer_mod._tracer = prev
        plugin.shutdown()
        if ls._LIVE_STORE is not None:
            ls._LIVE_STORE.close()
        ls._LIVE_STORE = None


def _span_text(span) -> str:
    """Everything of a finished span that an OTLP exporter serialises."""
    return json.dumps(
        {
            "name": span.name,
            "attributes": dict(span.attributes or {}),
            "status": span.status.description,
            "events": [[ev.name, dict(ev.attributes or {})] for ev in span.events],
        },
        default=str,
    )


def _log_text(rec) -> str:
    record = getattr(rec, "log_record", rec)
    return json.dumps([str(record.body), dict(record.attributes or {})], default=str)


def _dump(out) -> dict:
    return {
        "exported spans": "\n".join(_span_text(s) for s in out["spans"]),
        "exported events": "\n".join(_log_text(rec) for rec in out["logs"]),
        "live-store spans": json.dumps(out["store_spans"], default=str),
        "live-store events": json.dumps(out["store_logs"], default=str),
    }


def test_no_command_path_url_or_error_text_reaches_a_backend_or_the_store(
    tmp_path, monkeypatch, caplog
):
    with caplog.at_level("WARNING", logger="hermes_otel"):
        out = _run_turn(tmp_path, monkeypatch, content_capture="off", **STRICT)
    assert out["spans"] and out["logs"] and out["store_spans"] and out["store_logs"]
    _assert_content_free(out)
    # The startup log names what is switched off, so it is never a silent setting.
    assert "not recorded: tool commands, tool targets, error messages" in caplog.text


def test_the_backend_exporter_omits_on_its_own_when_the_live_store_is_off(tmp_path, monkeypatch):
    """No live store means no earlier pass over the span to share: the
    exporter wrapper has to remove the text itself."""
    out = _run_turn(tmp_path, monkeypatch, content_capture="off", dashboard_live=False, **STRICT)
    assert out["spans"] and out["logs"] and not out["store_spans"]
    _assert_content_free(out)


def _assert_content_free(out) -> None:
    for where, text in _dump(out).items():
        for marker in MARKERS:
            assert marker not in text, f"{marker!r} reached the {where}"

    # What a trace needs is all still there: names, outcomes, error classes, model, session.
    by_name = {s.name: s for s in out["spans"]}
    assert {"tool.terminal", "tool.read_file", "tool.web_extract", "tool.write_file"} <= set(
        by_name
    )
    failed = by_name["tool.write_file"]
    assert failed.attributes["tool.name"] == "write_file"
    assert failed.attributes["hermes.tool.outcome"] == "error"
    assert failed.status.status_code.name == "ERROR" and not failed.status.description
    assert "error.message" not in failed.attributes
    root = next(s for s in out["spans"] if s.parent is None and s.name.startswith("agent"))
    assert root.attributes["hermes.turn.tool_count"] == 4
    assert "terminal" in root.attributes["hermes.turn.tools"]
    assert "hermes.turn.tool_commands" not in root.attributes
    assert "hermes.turn.tool_targets" not in root.attributes
    assert root.attributes["session.id"] == "s-669"
    api = next(s for s in out["spans"] if s.name.startswith("api."))
    assert api.attributes["error.type"] and api.status.status_code.name == "ERROR"
    exc = next(ev for ev in api.events if ev.name == "exception")
    assert exc.attributes["exception.type"] == "RateLimitError"
    assert "exception.message" not in exc.attributes
    bodies = [str(getattr(rec, "log_record", rec).body) for rec in out["logs"]]
    assert any(b.startswith("api error RateLimitError") for b in bodies)
    assert any(b.startswith("tool write_file error") for b in bodies)


@pytest.mark.parametrize(
    "switch,gone,kept",
    [
        ("capture_tool_command", (COMMAND,), (PATH, URL, TOOL_ERROR, API_ERROR)),
        ("capture_tool_target", (PATH, URL), (COMMAND, API_ERROR)),
        ("capture_error_message", (TOOL_ERROR, API_ERROR), (COMMAND, PATH, URL)),
    ],
)
def test_each_switch_removes_only_its_own_text(tmp_path, monkeypatch, switch, gone, kept):
    out = _run_turn(tmp_path, monkeypatch, content_capture="off", **{switch: False})
    dump = _dump(out)
    for where, text in dump.items():
        for marker in gone:
            assert marker not in text, f"{marker!r} reached the {where}"
    for marker in kept:
        assert marker in dump["exported spans"], f"{switch} also removed {marker!r}"
        if marker != API_ERROR:  # on a span event, which the live store does not keep
            assert marker in dump["live-store spans"], f"{switch} also removed {marker!r}"


def test_content_off_alone_keeps_them_and_keeps_the_subagent_summary_out(tmp_path, monkeypatch):
    """The default is unchanged (#669 asks for that). The failed sub-agent's
    summary is content, so ``off`` never lets it into the status description."""
    out = _run_turn(tmp_path, monkeypatch, content_capture="off")
    dump = _dump(out)
    for marker in (COMMAND, PATH, URL, TOOL_ERROR):
        assert marker in dump["exported spans"] and marker in dump["live-store spans"]
    assert API_ERROR in dump["exported spans"] and API_ERROR in dump["exported events"]
    for where, text in dump.items():
        assert SUMMARY not in text, f"sub-agent summary reached the {where}"
    sub = next(s for s in out["spans"] if s.name.startswith("subagent."))
    assert sub.status.description == "failed"


def test_the_switches_work_in_full_mode_too(tmp_path, monkeypatch):
    """Independent of ``content_capture``: tool I/O stays, the three groups go."""
    out = _run_turn(tmp_path, monkeypatch, content_capture="full", **STRICT)
    tool = next(s for s in out["spans"] if s.name == "tool.terminal")
    assert COMMAND in tool.attributes["input.value"]  # content capture is its own switch
    for s in out["spans"]:
        assert not (set(s.attributes) & HermesOtelConfig(**STRICT).omitted_attributes()), s.name
        assert not s.status.description
