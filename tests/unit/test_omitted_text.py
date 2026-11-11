"""``capture_tool_command`` / ``capture_tool_target`` / ``capture_error_message`` (#669).

Tool commands, tool targets and error text are recorded in every
``content_capture`` mode by default. Each switch, set to false, removes its
attributes from every span, span event and event record, and error text also
leaves the span status description and the ``hermes.api.error`` event body.
"""

from __future__ import annotations

import dataclasses

import pytest
from golden.record_hooks import SCENARIOS, config_for
from helpers.golden import three_signal_plugin
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.trace import SpanContext, Status, StatusCode, TraceFlags

from hermes_otel import pipeline, semconv
from hermes_otel import redaction as r
from hermes_otel.pipeline import redact_span
from hermes_otel.plugin_config import HermesOtelConfig, load_config

SWITCHES = {
    "capture_tool_command": semconv.TOOL_COMMAND_KEYS,
    "capture_tool_target": semconv.TOOL_TARGET_KEYS,
    "capture_error_message": semconv.ERROR_TEXT_KEYS,
}
ALL_OFF = {name: False for name in SWITCHES}
BEARER = "eyJhbGciOiJIUzI1NiJ9" + ".payloadpayload.sigsigsig"


@pytest.fixture(autouse=True)
def _builtin_redactor(monkeypatch):
    monkeypatch.setattr(r, "_hermes_resolved", True)
    monkeypatch.setattr(r, "_hermes_redactor", None)
    monkeypatch.delenv("HERMES_REDACT_SECRETS", raising=False)


def _span(attrs=None, events=(), status=None):
    return ReadableSpan(
        name="x",
        context=SpanContext(1, 2, is_remote=False, trace_flags=TraceFlags(1)),
        attributes=attrs or {},
        events=list(events),
        status=status or Status(StatusCode.UNSET),
    )


class TestConfig:
    def test_everything_is_captured_by_default(self, tmp_path):
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert [getattr(cfg, name) for name in SWITCHES] == [True, True, True]
        assert cfg.omitted_attributes() == frozenset()

    @pytest.mark.parametrize("name", sorted(SWITCHES))
    def test_each_switch_omits_its_own_group_only(self, name):
        cfg = HermesOtelConfig(**{name: False})
        assert cfg.omitted_attributes() == frozenset(SWITCHES[name])

    def test_all_three_together(self):
        want = {key for keys in SWITCHES.values() for key in keys}
        assert HermesOtelConfig(**ALL_OFF).omitted_attributes() == frozenset(want)

    @pytest.mark.parametrize("name", sorted(SWITCHES))
    def test_env_var(self, name, monkeypatch, tmp_path):
        monkeypatch.setenv(f"HERMES_OTEL_{name.upper()}", "false")
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert getattr(cfg, name) is False

    def test_yaml(self, tmp_path):
        pytest.importorskip("yaml")
        path = tmp_path / "config.yaml"
        path.write_text("".join(f"{name}: false\n" for name in SWITCHES))
        cfg = load_config(path=path)
        assert [getattr(cfg, name) for name in SWITCHES] == [False, False, False]

    def test_independent_of_content_capture(self):
        # ``off`` alone keeps them (the documented default); the switches work in ``full`` too.
        assert HermesOtelConfig(content_capture="off").omitted_attributes() == frozenset()
        assert HermesOtelConfig(content_capture="full", **ALL_OFF).omitted_attributes()

    def test_every_omittable_name_is_a_registered_sensitive_attribute(self):
        for keys in SWITCHES.values():
            assert set(keys) <= semconv.SENSITIVE_ATTRIBUTES
            assert set(keys) <= set(semconv.ATTRIBUTE_NAMES)


class TestRedactSpan:
    OMIT = HermesOtelConfig(**ALL_OFF).omitted_attributes()

    def test_omitted_attributes_leave_the_span_and_the_rest_stays(self):
        attrs = {
            "tool.name": "terminal",
            "hermes.tool.outcome": "error",
            "hermes.tool.command": "cat /srv/acme/secrets.env",
            "hermes.tool.target": "/srv/acme/secrets.env",
            "hermes.turn.tool_commands": "cat /srv/acme/secrets.env|ls",
            "hermes.turn.tool_targets": "/srv/acme/secrets.env",
            "error.message": "no such file: /srv/acme/secrets.env",
        }
        out = redact_span(_span(attrs), self.OMIT)
        assert dict(out.attributes) == {"tool.name": "terminal", "hermes.tool.outcome": "error"}

    def test_exception_event_keeps_its_type_and_loses_its_text(self):
        ev = Event(
            "exception",
            {
                "exception.type": "RateLimitError",
                "exception.message": "429 for acme-corp",
                "exception.stacktrace": "Traceback ...",
                "exception.escaped": True,
            },
            timestamp=1,
        )
        out = redact_span(_span({}, [ev]), self.OMIT)
        assert dict(out.events[0].attributes) == {
            "exception.type": "RateLimitError",
            "exception.escaped": True,
        }
        assert out.events[0].name == "exception" and out.events[0].timestamp == 1

    def test_status_description_is_emptied_and_the_error_code_stays(self):
        span = _span(status=Status(StatusCode.ERROR, "no such file: /srv/acme/x"))
        out = redact_span(span, self.OMIT)
        assert out.status.status_code is StatusCode.ERROR
        assert not out.status.description

    def test_status_description_stays_when_only_tool_text_is_omitted(self):
        omit = HermesOtelConfig(capture_tool_command=False).omitted_attributes()
        span = _span({"hermes.tool.command": "ls"}, status=Status(StatusCode.ERROR, "boom"))
        out = redact_span(span, omit)
        assert out.status.description == "boom" and "hermes.tool.command" not in out.attributes

    def test_a_span_with_nothing_to_omit_is_the_same_object(self):
        span = _span({"tool.name": "terminal"}, status=Status(StatusCode.OK))
        assert redact_span(span, self.OMIT) is span

    def test_a_pass_that_omitted_nothing_is_not_reused_for_one_that_omits(self):
        span = _span({"hermes.tool.command": "ls", "tool.name": "terminal"})
        assert redact_span(span) is span  # cached as unchanged for an empty omit
        out = redact_span(span, self.OMIT)
        assert dict(out.attributes) == {"tool.name": "terminal"}
        assert redact_span(span, self.OMIT) is out  # and the omitting pass is shared

    def test_default_leaves_the_text_and_masks_a_secret_in_the_status_description(self):
        text = f"401 from https://api.example with Authorization: Bearer {BEARER}"
        span = _span(
            {"hermes.tool.command": "ls", "error.message": "boom"},
            status=Status(StatusCode.ERROR, text),
        )
        out = redact_span(span)
        assert out.attributes["hermes.tool.command"] == "ls"
        assert out.attributes["error.message"] == "boom"
        assert out.status.status_code is StatusCode.ERROR
        assert BEARER not in out.status.description and "401 from" in out.status.description

    def test_a_span_that_cannot_be_rebuilt_is_dropped_not_exported_with_the_text(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("cannot rebuild")

        monkeypatch.setattr(pipeline, "_redact_span_uncached", boom)
        span = _span({"hermes.tool.command": "ls"})
        assert redact_span(span, self.OMIT) is None
        assert (
            redact_span(_span({"hermes.tool.command": "ls"})) is not None
        )  # masking alone fails open

        exported = []

        class Inner:
            def export(self, spans):
                exported.extend(spans)

        pipeline._ContentRedactingExporter(Inner(), self.OMIT).export([span])
        assert exported == []

        class Store:
            rows = []

            def add_span(self, row):
                self.rows.append(row)

            def flush(self):
                pass

        pipeline._LiveSpanProcessor(Store(), self.OMIT).on_end(span)
        assert Store.rows == []


def _text_left(cfg):
    """Every sensitive attribute, status description and event body the hook
    scenarios leave behind under *cfg*, after the pipeline pass."""
    omit = cfg.omitted_attributes()
    left, bodies = set(), set()
    for scenario in SCENARIOS.values():
        with three_signal_plugin(cfg) as p:
            scenario(p)
            p.plugin.spans.end_all()
            for raw in p.spans.get_finished_spans():
                # The pass itself, without its per-span cache (nothing to share here).
                span = pipeline._redact_span_uncached(raw, omit)
                left.update(
                    ("span", k) for k in span.attributes if k in semconv.SENSITIVE_ATTRIBUTES
                )
                if span.status.description:
                    left.add(("span", "status.description"))
                for ev in span.events:
                    left.update(
                        ("span event", k)
                        for k in ev.attributes
                        if k in semconv.SENSITIVE_ATTRIBUTES
                    )
            for rec in p.logs.get_finished_logs():
                record = getattr(rec, "log_record", rec)
                left.update(
                    ("event", k)
                    for k in (record.attributes or {})
                    if k in semconv.SENSITIVE_ATTRIBUTES
                )
                bodies.add(str(record.body))
    return left, bodies


def test_off_plus_the_three_switches_leaves_no_free_text_in_any_hook_scenario():
    """The issue's use case: content-free spans and events across every hook family."""
    cfg = dataclasses.replace(config_for("off", log_events=True), **ALL_OFF)
    left, bodies = _text_left(cfg)
    assert left == set()
    # The request error messages the scenarios use never reach an event body.
    assert not [b for b in bodies if "Too Many Requests" in b or "invalid api key" in b]
    assert any(b.startswith("api error RateLimitError") for b in bodies)


def test_off_alone_still_records_them_so_the_default_did_not_change():
    left, bodies = _text_left(config_for("off", log_events=True))
    assert {
        ("span", "hermes.tool.command"),
        ("span", "hermes.tool.target"),
        ("span", "hermes.turn.tool_commands"),
        ("span", "hermes.turn.tool_targets"),
        ("span", "status.description"),
        ("span event", "exception.message"),
        ("event", "exception.message"),
    } <= left
    assert any("429 Too Many Requests" in b for b in bodies)
