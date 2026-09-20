"""debug.log records export outcomes and the SDK's own export warnings (#167)."""

from __future__ import annotations

import logging

import pytest

import hermes_otel.debug_utils as du


@pytest.fixture
def debug_log_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(du, "_DEBUG_ENABLED", True)
    du.close_debug_log()
    yield tmp_path / "plugins" / "hermes_otel" / "debug.log"
    du.remove_sdk_log_forwarding()
    du.close_debug_log()


def _lines(path):
    return path.read_text().splitlines() if path.exists() else []


class TestSdkForwarding:
    def test_sdk_export_failure_lands_in_the_debug_log(self, debug_log_file):
        assert du.install_sdk_log_forwarding() is True
        logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").error(
            "Failed to export span batch code: 405, reason: Method Not Allowed"
        )
        du.close_debug_log()
        assert any(
            "[sdk] opentelemetry.exporter.otlp.proto.http.trace_exporter ERROR: Failed to export span batch code: 405"
            in l
            for l in _lines(debug_log_file)
        )

    def test_install_is_idempotent_and_removable(self, debug_log_file):
        du.install_sdk_log_forwarding()
        du.install_sdk_log_forwarding()
        target = logging.getLogger("opentelemetry")
        assert sum(1 for h in target.handlers if getattr(h, du._SDK_HANDLER_MARKER, False)) == 1
        du.remove_sdk_log_forwarding()
        assert not any(getattr(h, du._SDK_HANDLER_MARKER, False) for h in target.handlers)

    def test_noop_when_debug_is_off(self, monkeypatch):
        monkeypatch.setattr(du, "_DEBUG_ENABLED", False)
        assert du.install_sdk_log_forwarding() is False
        assert not any(
            getattr(h, du._SDK_HANDLER_MARKER, False)
            for h in logging.getLogger("opentelemetry").handlers
        )


class TestExportResultLines:
    def test_span_batches_log_success_and_failure(self, debug_log_file):
        from opentelemetry.sdk.trace.export import SpanExportResult

        from hermes_otel.tracer import _LoggingSpanExporter

        class Ok:
            def export(self, spans):
                return SpanExportResult.SUCCESS

            def shutdown(self):
                pass

        class Bad:
            def export(self, spans):
                return SpanExportResult.FAILURE

            def shutdown(self):
                pass

        _LoggingSpanExporter(Ok(), "Phoenix").export([object()] * 3)
        _LoggingSpanExporter(Bad(), "Langfuse").export([object()])
        du.close_debug_log()
        lines = _lines(debug_log_file)
        assert "export Phoenix: 3 span(s) -> SUCCESS" in lines
        assert "export Langfuse: 1 span(s) -> FAILURE" in lines

    def test_real_batch_processor_reports_through_the_wrapper(self, debug_log_file):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from hermes_otel.tracer import _LoggingSpanExporter

        inner = InMemorySpanExporter()
        provider = TracerProvider()
        processor = BatchSpanProcessor(_LoggingSpanExporter(inner, "InMemory"))
        provider.add_span_processor(processor)
        tracer = provider.get_tracer("t")
        for _ in range(2):
            with tracer.start_as_current_span("s"):
                pass
        assert provider.force_flush()
        provider.shutdown()
        du.close_debug_log()
        assert "export InMemory: 2 span(s) -> SUCCESS" in _lines(debug_log_file)
        assert len(inner.get_finished_spans()) == 2

    def test_log_batches_are_reported_too(self, debug_log_file):
        pytest.importorskip("opentelemetry.sdk._logs")
        from opentelemetry.sdk._logs.export import LogExportResult

        from hermes_otel.log_handler import _LoggingLogExporter

        class Ok:
            def export(self, batch):
                return LogExportResult.SUCCESS

            def shutdown(self):
                pass

        _LoggingLogExporter(Ok(), "SigNoz").export([object()] * 4)
        du.close_debug_log()
        assert "export SigNoz logs: 4 record(s) -> SUCCESS" in _lines(debug_log_file)


class TestPluginWiresItUp:
    def test_init_installs_forwarding_and_shutdown_removes_it(self, debug_log_file, monkeypatch):
        import os
        import tempfile

        monkeypatch.setenv("HERMES_OTEL_LIVE_DB", os.path.join(tempfile.mkdtemp(), "live.db"))
        for var in (
            "OTEL_PHOENIX_ENDPOINT",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_LANGFUSE_ENDPOINT",
            "LANGSMITH_TRACING",
        ):
            monkeypatch.delenv(var, raising=False)
        from hermes_otel.plugin_config import HermesOtelConfig
        from hermes_otel.tracer import HermesOTelPlugin

        plugin = HermesOTelPlugin(config=HermesOtelConfig(dashboard_live=True))
        assert plugin.init() is True
        target = logging.getLogger("opentelemetry")
        assert any(getattr(h, du._SDK_HANDLER_MARKER, False) for h in target.handlers)
        plugin.shutdown()
        assert not any(getattr(h, du._SDK_HANDLER_MARKER, False) for h in target.handlers)
