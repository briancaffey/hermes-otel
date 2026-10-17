"""``HermesOTelPlugin.init()`` re-entered (#484), through the real pipeline
builder with in-memory span exporters standing in for the OTLP ones: an
unchanged config keeps the live pipeline (same object, nothing flushed or
rebuilt), a changed config flushes and shuts the old pipeline down and builds
a new one, and no span is lost on either side."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from hermes_otel.backend_config import BackendConfig
from hermes_otel.plugin_config import SHORT_FINGERPRINT_CHARS, HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin


class _Exporter(InMemorySpanExporter):
    """The in-memory exporter, remembering the endpoint it stands in for and
    whether the pipeline shut it down."""

    def __init__(self, endpoint=None, headers=None, **_):
        super().__init__()
        self.endpoint = endpoint
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1
        super().shutdown()


def _config(project: str) -> HermesOtelConfig:
    return HermesOtelConfig(
        project_name=project,
        dashboard_live=False,
        probe_backends=False,
        # Spans only: the metric reader and the log exporter are not stubbed.
        backends=(
            BackendConfig(
                type="otlp",
                endpoint="http://collector:4318/v1/traces",
                metrics=False,
                logs=False,
            ),
        ),
    )


@pytest.fixture()
def exporters():
    """Every span exporter the pipeline built, in order; the OTel global
    tracer provider is left alone."""
    built = []

    def factory(**kwargs):
        exp = _Exporter(**kwargs)
        built.append(exp)
        return exp

    with (
        patch("hermes_otel.pipeline.OTLPSpanExporter", side_effect=factory),
        patch("hermes_otel.pipeline.trace.set_tracer_provider"),
    ):
        yield built


def _names(exporter):
    return sorted(s.name for s in exporter.get_finished_spans())


class TestReinitByFingerprint:
    def test_same_config_keeps_the_live_pipeline(self, exporters):
        plugin = HermesOTelPlugin(config=_config("fp-a"))
        assert plugin.init() is True
        first = plugin.pipeline
        assert plugin.config_fingerprint == _config("fp-a").fingerprint()
        plugin.start_span("agent", "session:s1", kind="agent", session_id="s1")
        plugin.start_span("tool.bash", "tool:t1", kind="tool", session_id="s1")
        plugin.end_span("tool:t1")  # queued in the batch processor, not yet exported

        assert plugin.init() is True  # a reload with nothing changed

        assert plugin.pipeline is first
        assert len(exporters) == 1 and exporters[0].shutdown_calls == 0
        # The open span is still open, the queued one was neither flushed nor dropped.
        assert plugin.spans.has_span("session:s1")
        assert plugin.spans.active_count() == 1
        plugin.end_span("session:s1")
        plugin.pipeline.force_flush()
        assert _names(exporters[0]) == ["agent", "tool.bash"]

    def test_changed_config_flushes_and_rebuilds_without_losing_a_span(self, exporters, caplog):
        plugin = HermesOTelPlugin(config=_config("fp-a"))
        assert plugin.init() is True
        old = plugin.pipeline
        old_fp = plugin.config_fingerprint
        plugin.start_span("agent", "session:s1", kind="agent", session_id="s1")
        plugin.start_span("tool.bash", "tool:t1", kind="tool", session_id="s1")
        plugin.end_span("tool:t1")  # buffered in the old pipeline

        plugin.config = replace(plugin.config, project_name="fp-b")
        with caplog.at_level("INFO", logger="hermes_otel"):
            assert plugin.init() is True

        new = plugin.pipeline
        assert new is not old and plugin.is_enabled
        assert plugin.config_fingerprint == _config("fp-b").fingerprint() != old_fp
        assert new.resource.attributes["openinference.project.name"] == "fp-b"
        assert old.resource.attributes["openinference.project.name"] == "fp-a"
        # The old side: its buffered span and its still-open root were both
        # exported before the exporter was shut down; the root says why it ended.
        old_exp, new_exp = exporters
        assert old_exp.shutdown_calls == 1
        assert _names(old_exp) == ["agent", "tool.bash"]
        root = next(s for s in old_exp.get_finished_spans() if s.name == "agent")
        assert root.attributes["hermes.span.ended_by"] == "sweep"
        assert root.attributes["hermes.span.sweep_reason"] == "shutdown"
        # The new side takes every span from now on, and only those.
        plugin.start_span("agent", "session:s2", kind="agent", session_id="s2")
        plugin.end_span("session:s2")
        new.force_flush()
        assert _names(new_exp) == ["agent"]
        assert _names(old_exp) == ["agent", "tool.bash"]
        assert new_exp.shutdown_calls == 0
        # The transition names both short fingerprints.
        line = next(r.getMessage() for r in caplog.records if "config changed" in r.getMessage())
        assert old_fp[:SHORT_FINGERPRINT_CHARS] in line
        assert plugin.config_fingerprint[:SHORT_FINGERPRINT_CHARS] in line

    def test_explicit_endpoint_counts_as_config(self, exporters):
        plugin = HermesOTelPlugin(
            config=HermesOtelConfig(dashboard_live=False, probe_backends=False)
        )
        assert plugin.init(endpoint="http://phoenix:6006/v1/traces") is True
        first = plugin.pipeline
        assert plugin.init(endpoint="http://phoenix:6006/v1/traces") is True
        assert plugin.pipeline is first
        assert plugin.init(endpoint="http://other:6006/v1/traces") is True
        assert plugin.pipeline is not first
        assert [e.endpoint for e in exporters] == [
            "http://phoenix:6006/v1/traces",
            "http://other:6006/v1/traces",
        ]
        assert exporters[0].shutdown_calls == 1

    def test_shutdown_forgets_the_fingerprint(self, exporters):
        plugin = HermesOTelPlugin(config=_config("fp-a"))
        assert plugin.init() is True
        plugin.shutdown()
        assert plugin.config_fingerprint is None and plugin.pipeline is None
        assert plugin.init() is True  # rebuilt, not mistaken for a no-op
        assert len(exporters) == 2
