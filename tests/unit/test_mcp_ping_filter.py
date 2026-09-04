"""MCP keepalive ``ping`` span suppression (issue #62).

MCP Python SDK 2.x (Hermes v0.21.0+) records a CLIENT span for every
outbound JSON-RPC request on the global TracerProvider — the one this plugin
installs — so every keepalive ``ping`` became a standalone one-span
``MCP send ping`` trace. The plugin wraps each span processor in
``_MCPPingFilterProcessor`` which drops the successful ones at ``on_end``.
"""

from unittest.mock import patch

import pytest
from hermes_otel.plugin_config import HermesOtelConfig, load_config
from hermes_otel.tracer import HermesOTelPlugin, _is_mcp_keepalive_ping, _MCPPingFilterProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode

# Exactly what mcp/shared/jsonrpc_dispatcher.py emits for a keepalive.
_PING_ATTRS = {"mcp.method.name": "ping", "jsonrpc.request.id": "42"}


def _emit_mcp_request(tracer, method: str, *, error: bool = False, name: str = None):
    with tracer.start_as_current_span(
        name or f"MCP send {method}",
        kind=SpanKind.CLIENT,
        attributes={"mcp.method.name": method, "jsonrpc.request.id": "42"},
    ) as span:
        if error:
            span.set_status(Status(StatusCode.ERROR, "Connection closed"))


@pytest.fixture()
def filtered_pipeline():
    """Provider whose single processor sits behind the ping filter."""
    exporter = InMemorySpanExporter()
    inner = SimpleSpanProcessor(exporter)
    provider = TracerProvider(resource=Resource.create({"service.name": "ping-filter-test"}))
    provider.add_span_processor(_MCPPingFilterProcessor(inner))
    # Same tracer name the SDK uses, resolved through this provider.
    tracer = provider.get_tracer("mcp-python-sdk")
    try:
        yield exporter, tracer
    finally:
        provider.shutdown()


class TestPredicate:
    def test_successful_ping_by_attribute(self):
        class S:
            name = "MCP send ping"
            attributes = _PING_ATTRS
            status = Status(StatusCode.UNSET)

        assert _is_mcp_keepalive_ping(S()) is True

    def test_failed_ping_is_kept(self):
        class S:
            name = "MCP send ping"
            attributes = _PING_ATTRS
            status = Status(StatusCode.ERROR, "boom")

        assert _is_mcp_keepalive_ping(S()) is False

    def test_name_fallback_without_attributes(self):
        class S:
            name = "MCP send ping"
            attributes = None
            status = None

        assert _is_mcp_keepalive_ping(S()) is True

    def test_other_mcp_requests_are_not_pings(self):
        class S:
            name = "MCP send tools/call echo"
            attributes = {"mcp.method.name": "tools/call"}
            status = Status(StatusCode.OK)

        assert _is_mcp_keepalive_ping(S()) is False


class TestFilterProcessor:
    def test_successful_ping_never_reaches_exporter(self, filtered_pipeline):
        exporter, tracer = filtered_pipeline
        _emit_mcp_request(tracer, "ping")
        assert exporter.get_finished_spans() == ()

    def test_failed_ping_is_exported(self, filtered_pipeline):
        exporter, tracer = filtered_pipeline
        _emit_mcp_request(tracer, "ping", error=True)
        spans = exporter.get_finished_spans()
        assert [s.name for s in spans] == ["MCP send ping"]
        assert spans[0].status.status_code == StatusCode.ERROR

    def test_real_mcp_traffic_is_exported(self, filtered_pipeline):
        exporter, tracer = filtered_pipeline
        _emit_mcp_request(tracer, "tools/call", name="MCP send tools/call echo")
        _emit_mcp_request(tracer, "ping")
        _emit_mcp_request(tracer, "tools/list")
        assert [s.name for s in exporter.get_finished_spans()] == [
            "MCP send tools/call echo",
            "MCP send tools/list",
        ]

    def test_lifecycle_calls_delegate(self):
        class Inner:
            calls = []

            def on_start(self, span, parent_context=None):
                self.calls.append(("on_start", span, parent_context))

            def on_end(self, span):
                self.calls.append(("on_end", span))

            def shutdown(self):
                self.calls.append(("shutdown",))

            def force_flush(self, timeout_millis=30000):
                self.calls.append(("force_flush", timeout_millis))
                return True

        inner = Inner()
        proc = _MCPPingFilterProcessor(inner)
        proc.on_start("span", parent_context="ctx")
        assert proc.force_flush(123) is True
        proc.shutdown()
        assert inner.calls == [("on_start", "span", "ctx"), ("force_flush", 123), ("shutdown",)]


class TestConfig:
    def test_default_on(self, tmp_path):
        assert HermesOtelConfig().suppress_mcp_ping_spans is True
        assert load_config(path=tmp_path / "nonexistent.yaml").suppress_mcp_ping_spans is True

    def test_yaml_opt_out(self, tmp_path):
        pytest.importorskip("yaml")
        p = tmp_path / "config.yaml"
        p.write_text("suppress_mcp_ping_spans: false\n")
        assert load_config(path=p).suppress_mcp_ping_spans is False

    def test_env_opt_out(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_SUPPRESS_MCP_PING_SPANS", "false")
        assert load_config(path=tmp_path / "nonexistent.yaml").suppress_mcp_ping_spans is False


class TestInitWiring:
    """``init()`` (live-only, zero-config) puts the live store behind the filter."""

    def _init_live_only(self, monkeypatch, suppress: bool):
        for var in (
            "OTEL_PHOENIX_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_LANGFUSE_ENDPOINT",
            "LANGSMITH_TRACING", "OTEL_SIGNOZ_ENDPOINT", "OTEL_JAEGER_ENDPOINT", "OTEL_TEMPO_ENDPOINT",
        ):
            monkeypatch.delenv(var, raising=False)
        import hermes_otel.live_store as ls

        ls._LIVE_STORE = None
        plugin = HermesOTelPlugin(
            config=HermesOtelConfig(dashboard_live=True, suppress_mcp_ping_spans=suppress)
        )
        # The global-provider install is patched out so the test never mutates
        # process-wide OTel state; grab the provider the plugin built instead.
        with patch("hermes_otel.tracer.trace.set_tracer_provider") as set_provider:
            assert plugin.init() is True
        provider = set_provider.call_args[0][0]
        return provider, ls

    @pytest.mark.parametrize("suppress", [True, False])
    def test_live_store_respects_setting(self, monkeypatch, tmp_path, suppress):
        import hermes_otel.live_store as ls_mod

        monkeypatch.setattr(ls_mod, "_default_db_path", lambda: str(tmp_path / "live.db"))
        provider, ls = self._init_live_only(monkeypatch, suppress)
        try:
            # Same tracer name the MCP SDK uses, resolved through this provider.
            tracer = provider.get_tracer("mcp-python-sdk")
            _emit_mcp_request(tracer, "ping")
            _emit_mcp_request(tracer, "ping", error=True)
            _emit_mcp_request(tracer, "tools/list")
            names = sorted(s["name"] for s in ls.get_live_store().spans())
            if suppress:
                assert names == ["MCP send ping", "MCP send tools/list"]  # only the FAILED ping
                statuses = {s["name"]: s["status"] for s in ls.get_live_store().spans()}
                assert statuses["MCP send ping"] == "ERROR"
            else:
                assert names == ["MCP send ping", "MCP send ping", "MCP send tools/list"]
        finally:
            provider.shutdown()
            ls._LIVE_STORE = None
