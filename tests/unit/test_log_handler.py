"""Tests for hermes_otel.log_handler — the OTel logs pipeline.

Covers the three parts the tracer depends on:

* :func:`build_log_processors` — filters by ``supports_logs``, builds one
  :class:`BatchLogRecordProcessor` per log-capable backend.
* :func:`install_handler` — attaches an OTel ``LoggingHandler`` to the
  right Python logger, sets level appropriately, is idempotent across
  repeated installs, and filters out OTel-internal logger records.
* :func:`resolve_level` — string-to-int level parsing.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from hermes_otel import log_handler
from hermes_otel.backends import _ResolvedBackend

# ── resolve_level ──────────────────────────────────────────────────────────


class TestResolveLevel:
    def test_named_levels(self):
        assert log_handler.resolve_level("DEBUG") == logging.DEBUG
        assert log_handler.resolve_level("INFO") == logging.INFO
        assert log_handler.resolve_level("warning") == logging.WARNING
        assert log_handler.resolve_level("Error") == logging.ERROR

    def test_numeric_level(self):
        assert log_handler.resolve_level("20") == 20
        assert log_handler.resolve_level("40") == 40

    def test_unknown_falls_back_to_default(self):
        assert log_handler.resolve_level("bogus") == logging.INFO
        assert log_handler.resolve_level("") == logging.INFO
        assert log_handler.resolve_level(None) == logging.INFO

    def test_custom_default(self):
        assert log_handler.resolve_level(None, default=logging.WARNING) == logging.WARNING


# ── Endpoint derivation ─────────────────────────────────────────────────────


class TestDeriveLogsEndpoint:
    def test_swaps_traces_suffix(self):
        assert (
            log_handler._derive_logs_endpoint("http://localhost:4318/v1/traces")
            == "http://localhost:4318/v1/logs"
        )

    def test_passthrough_when_no_traces_suffix(self):
        # Some backends expose non-suffixed OTLP endpoints; leave them alone
        # so the caller can hand the exporter what they explicitly configured.
        assert (
            log_handler._derive_logs_endpoint("http://collector.example/otlp")
            == "http://collector.example/otlp"
        )


# ── build_log_processors ────────────────────────────────────────────────────


@pytest.fixture
def fake_otlp_exporter():
    """Stub OTLPLogExporter so tests don't open real network sockets."""
    with patch.object(log_handler, "OTLPLogExporter", new=MagicMock()) as m:
        yield m


class TestBuildLogProcessors:
    def test_skips_backends_that_dont_support_logs(self, fake_otlp_exporter):
        backends = [
            _ResolvedBackend(
                type="tempo",
                endpoint="http://tempo:4318/v1/traces",
                supports_logs=False,
            ),
            _ResolvedBackend(
                type="otlp",
                endpoint="http://otlp:4318/v1/traces",
                supports_logs=True,
            ),
        ]
        procs = log_handler.build_log_processors(backends)
        assert len(procs) == 1
        _proc, backend = procs[0]
        assert backend.type == "otlp"

    def test_empty_list_when_nothing_supports_logs(self, fake_otlp_exporter):
        backends = [
            _ResolvedBackend(type="phoenix", endpoint="x", supports_logs=False),
            _ResolvedBackend(type="jaeger", endpoint="x", supports_logs=False),
        ]
        assert log_handler.build_log_processors(backends) == []

    def test_uses_derived_logs_endpoint(self, fake_otlp_exporter):
        backends = [
            _ResolvedBackend(
                type="otlp",
                endpoint="http://collector:4318/v1/traces",
                supports_logs=True,
            )
        ]
        log_handler.build_log_processors(backends)
        fake_otlp_exporter.assert_called_once()
        call_kwargs = fake_otlp_exporter.call_args.kwargs
        assert call_kwargs["endpoint"] == "http://collector:4318/v1/logs"

    def test_merges_extra_headers_over_backend_headers(self, fake_otlp_exporter):
        backends = [
            _ResolvedBackend(
                type="otlp",
                endpoint="http://c:4318/v1/traces",
                headers={"X-From-Backend": "a", "X-Both": "backend"},
                supports_logs=True,
            )
        ]
        log_handler.build_log_processors(
            backends,
            extra_headers={"X-Global": "b", "X-Both": "global"},
        )
        headers = fake_otlp_exporter.call_args.kwargs["headers"]
        # Per-backend headers win on key collision (they carry resolver-built
        # auth); matches tracer._merge_headers and the documented precedence.
        assert headers == {
            "X-From-Backend": "a",
            "X-Global": "b",
            "X-Both": "backend",
        }

    def test_exporter_failure_is_isolated(self):
        backends = [
            _ResolvedBackend(type="otlp", endpoint="x", supports_logs=True),
            _ResolvedBackend(type="otlp", endpoint="y", supports_logs=True),
        ]
        # First backend explodes, second must still succeed.
        with patch.object(
            log_handler,
            "OTLPLogExporter",
            side_effect=[RuntimeError("boom"), MagicMock()],
        ):
            procs = log_handler.build_log_processors(backends)
        assert len(procs) == 1


# ── install_handler ─────────────────────────────────────────────────────────


@pytest.fixture
def clean_root_logger():
    """Strip any handlers we install and restore the root logger on teardown."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield root
    # Remove anything this test installed and restore prior state.
    for h in list(root.handlers):
        if getattr(h, log_handler._HANDLER_MARKER, False):
            root.removeHandler(h)
    root.setLevel(saved_level)
    # Preserve handlers the test framework may have added.
    for h in list(root.handlers):
        if h not in saved_handlers:
            root.removeHandler(h)
    for h in saved_handlers:
        if h not in root.handlers:
            root.addHandler(h)


class TestInstallHandler:
    def _fake_processors(self, n=1):
        return [
            (MagicMock(), _ResolvedBackend(type="otlp", endpoint="x", supports_logs=True))
            for _ in range(n)
        ]

    def _resource(self):
        from opentelemetry.sdk.resources import Resource

        return Resource.create({"service.name": "hermes-otel-test"})

    def test_returns_none_when_no_processors(self):
        provider = log_handler.install_handler(
            resource=self._resource(),
            processors=[],
            level=logging.INFO,
        )
        assert provider is None

    def test_attaches_to_root_by_default(self, clean_root_logger):
        log_handler.install_handler(
            resource=self._resource(),
            processors=self._fake_processors(),
            level=logging.INFO,
        )
        markers = [
            h for h in clean_root_logger.handlers if getattr(h, log_handler._HANDLER_MARKER, False)
        ]
        assert len(markers) == 1

    def test_attaches_to_named_logger_when_requested(self, clean_root_logger):
        log_handler.install_handler(
            resource=self._resource(),
            processors=self._fake_processors(),
            level=logging.INFO,
            attach_logger="hermes_otel_test_scoped",
        )
        target = logging.getLogger("hermes_otel_test_scoped")
        try:
            markers = [h for h in target.handlers if getattr(h, log_handler._HANDLER_MARKER, False)]
            assert len(markers) == 1
            # Root gets nothing — scoped install must not leak.
            root_markers = [
                h
                for h in clean_root_logger.handlers
                if getattr(h, log_handler._HANDLER_MARKER, False)
            ]
            assert root_markers == []
        finally:
            for h in list(target.handlers):
                if getattr(h, log_handler._HANDLER_MARKER, False):
                    target.removeHandler(h)

    def test_is_idempotent_across_reinstalls(self, clean_root_logger):
        log_handler.install_handler(
            resource=self._resource(),
            processors=self._fake_processors(),
            level=logging.INFO,
        )
        log_handler.install_handler(
            resource=self._resource(),
            processors=self._fake_processors(),
            level=logging.INFO,
        )
        log_handler.install_handler(
            resource=self._resource(),
            processors=self._fake_processors(),
            level=logging.INFO,
        )
        markers = [
            h for h in clean_root_logger.handlers if getattr(h, log_handler._HANDLER_MARKER, False)
        ]
        assert len(markers) == 1, "should replace, not stack, prior installs"

    def test_raises_target_level_to_match_handler(self, clean_root_logger):
        clean_root_logger.setLevel(logging.ERROR)
        log_handler.install_handler(
            resource=self._resource(),
            processors=self._fake_processors(),
            level=logging.DEBUG,
        )
        assert clean_root_logger.level <= logging.DEBUG

    def test_leaves_target_level_alone_when_already_low_enough(self, clean_root_logger):
        clean_root_logger.setLevel(logging.DEBUG)
        log_handler.install_handler(
            resource=self._resource(),
            processors=self._fake_processors(),
            level=logging.WARNING,
        )
        # We only LOWER the effective level — never raise it.
        assert clean_root_logger.level == logging.DEBUG


# ── OTel-internal filter ────────────────────────────────────────────────────


class TestLgtmBackendType:
    """The `lgtm` backend type: alias over `otlp` with nicer display name."""

    def test_lgtm_resolves_as_logs_capable(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        rb = backends.resolve(
            BackendConfig(type="lgtm", endpoint="http://localhost:4318/v1/traces")
        )
        assert rb.type == "lgtm"
        assert rb.display_name == "LGTM"
        assert rb.supports_metrics is True
        assert rb.supports_logs is True

    def test_lgtm_requires_endpoint(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="lgtm"))

    def test_lgtm_allows_name_override(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        rb = backends.resolve(
            BackendConfig(
                type="lgtm",
                name="prod-lgtm",
                endpoint="http://collector:4318/v1/traces",
            )
        )
        assert rb.display_name == "prod-lgtm"

    def test_tempo_remains_traces_only(self):
        """Sanity: don't let LGTM's addition change tempo's behavior."""
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        rb = backends.resolve(
            BackendConfig(type="tempo", endpoint="http://localhost:4318/v1/traces")
        )
        assert rb.supports_metrics is False
        assert rb.supports_logs is False


class TestUptraceBackendType:
    """The `uptrace` backend type: DSN-based auth, all three signals."""

    def test_uptrace_resolves_with_dsn(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        rb = backends.resolve(
            BackendConfig(
                type="uptrace",
                endpoint="http://localhost:14318/v1/traces",
                dsn="http://project1_secret@localhost:14318?grpc=14317",
            )
        )
        assert rb.type == "uptrace"
        assert rb.display_name == "Uptrace"
        assert rb.supports_metrics is True
        assert rb.supports_logs is True
        assert rb.headers is not None
        assert rb.headers.get("uptrace-dsn") == (
            "http://project1_secret@localhost:14318?grpc=14317"
        )

    def test_uptrace_requires_endpoint(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="uptrace", dsn="http://t@h:14318"))

    def test_uptrace_requires_dsn(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        with pytest.raises(ValueError, match="dsn"):
            backends.resolve(
                BackendConfig(
                    type="uptrace",
                    endpoint="http://localhost:14318/v1/traces",
                )
            )

    def test_uptrace_dsn_from_env(self, monkeypatch):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        monkeypatch.setenv("UPTRACE_DSN", "http://token@host:14318?grpc=14317")
        rb = backends.resolve(
            BackendConfig(
                type="uptrace",
                endpoint="http://localhost:14318/v1/traces",
            )
        )
        assert rb.headers["uptrace-dsn"] == "http://token@host:14318?grpc=14317"

    def test_uptrace_user_headers_merge_on_top(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        rb = backends.resolve(
            BackendConfig(
                type="uptrace",
                endpoint="http://localhost:14318/v1/traces",
                dsn="http://t@h:14318",
                headers={"X-Extra": "1"},
            )
        )
        assert rb.headers["X-Extra"] == "1"
        assert rb.headers["uptrace-dsn"] == "http://t@h:14318"


class TestOpenObserveBackendType:
    """The `openobserve` backend: Basic auth + stream-name, all three signals."""

    def test_openobserve_resolves_with_basic_auth(self):
        import base64

        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        rb = backends.resolve(
            BackendConfig(
                type="openobserve",
                endpoint="http://localhost:5080/api/default/v1/traces",
                user="root@example.com",
                password="Complexpass#123",
            )
        )
        assert rb.type == "openobserve"
        assert rb.display_name == "OpenObserve"
        assert rb.supports_metrics is True
        assert rb.supports_logs is True
        expected_auth = base64.b64encode(b"root@example.com:Complexpass#123").decode()
        assert rb.headers["Authorization"] == f"Basic {expected_auth}"
        assert rb.headers["stream-name"] == "default"

    def test_openobserve_requires_endpoint(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="openobserve", user="u", password="p"))

    def test_openobserve_requires_credentials(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        with pytest.raises(ValueError, match="user and password"):
            backends.resolve(
                BackendConfig(
                    type="openobserve",
                    endpoint="http://localhost:5080/api/default/v1/traces",
                )
            )

    def test_openobserve_credentials_from_env(self, monkeypatch):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        monkeypatch.setenv("OPENOBSERVE_USER", "root@example.com")
        monkeypatch.setenv("OPENOBSERVE_PASSWORD", "Complexpass#123")
        rb = backends.resolve(
            BackendConfig(
                type="openobserve",
                endpoint="http://localhost:5080/api/default/v1/traces",
            )
        )
        assert "Authorization" in rb.headers

    def test_openobserve_custom_stream_name(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        rb = backends.resolve(
            BackendConfig(
                type="openobserve",
                endpoint="http://localhost:5080/api/myorg/v1/traces",
                user="u",
                password="p",
                stream_name="my-stream",
            )
        )
        assert rb.headers["stream-name"] == "my-stream"


class TestParseableBackendType:
    """Parseable backend uses signal-specific dataset and source headers."""

    def test_parseable_resolves_all_signal_headers(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        rb = backends.resolve(
            BackendConfig(
                type="parseable",
                endpoint="https://parseable.example.com/v1/traces",
                api_key="p-key",
            )
        )

        assert rb.type == "parseable"
        assert rb.display_name == "Parseable"
        assert rb.supports_metrics is True
        assert rb.supports_logs is True
        assert rb.headers == {
            "X-API-Key": "p-key",
            "X-P-Stream": "hermes-traces",
            "X-P-Log-Source": "otel-traces",
        }
        assert rb.metrics_headers["X-P-Stream"] == "hermes-metrics"
        assert rb.metrics_headers["X-P-Log-Source"] == "otel-metrics"
        assert rb.logs_headers["X-P-Stream"] == "hermes-logs"
        assert rb.logs_headers["X-P-Log-Source"] == "otel-logs"

    def test_parseable_custom_datasets_and_headers(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        rb = backends.resolve(
            BackendConfig(
                type="parseable",
                endpoint="https://parseable.example.com/v1/traces",
                api_key="p-key",
                traces_dataset="prod-traces",
                metrics_dataset="prod-metrics",
                logs_dataset="prod-logs",
                headers={"X-Tenant": "acme"},
            )
        )

        assert rb.headers["X-P-Stream"] == "prod-traces"
        assert rb.metrics_headers["X-P-Stream"] == "prod-metrics"
        assert rb.logs_headers["X-P-Stream"] == "prod-logs"
        assert rb.logs_headers["X-Tenant"] == "acme"

    def test_parseable_requires_endpoint_and_api_key(self):
        from hermes_otel import backends
        from hermes_otel.plugin_config import BackendConfig

        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="parseable", api_key="p-key"))
        with pytest.raises(ValueError, match="api_key"):
            backends.resolve(
                BackendConfig(
                    type="parseable",
                    endpoint="https://parseable.example.com/v1/traces",
                )
            )

    def test_parseable_resolves_from_env(self, monkeypatch):
        from hermes_otel import backends

        monkeypatch.setenv("OTEL_PARSEABLE_ENDPOINT", "https://parseable.example.com/v1/traces")
        monkeypatch.setenv("PARSEABLE_API_KEY", "env-key")
        monkeypatch.setenv("PARSEABLE_TRACES_DATASET", "env-traces")

        rb = backends.resolve_from_env()

        assert rb.type == "parseable"
        assert rb.headers["X-API-Key"] == "env-key"
        assert rb.headers["X-P-Stream"] == "env-traces"


class TestExcludeOTelInternalFilter:
    def _record(self, name: str, level: int = logging.DEBUG) -> logging.LogRecord:
        return logging.LogRecord(
            name=name,
            level=level,
            pathname=__file__,
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )

    def test_drops_opentelemetry_records(self):
        f = log_handler._ExcludeOTelInternal()
        assert f.filter(self._record("opentelemetry.sdk._logs.export")) is False

    @pytest.mark.parametrize(
        "logger_name",
        [
            "urllib3.connectionpool",
            "urllib3.util.retry",
            "httpx",
            "httpcore.http11",
            "requests.adapters",
        ],
    )
    def test_drops_http_client_records(self, logger_name):
        """HTTP-client DEBUG logs would loop if we forwarded them."""
        f = log_handler._ExcludeOTelInternal()
        assert f.filter(self._record(logger_name)) is False

    def test_keeps_application_records(self):
        f = log_handler._ExcludeOTelInternal()
        assert f.filter(self._record("hermes_otel.hooks", level=logging.INFO)) is True
        assert f.filter(self._record("hermes.gateway", level=logging.INFO)) is True
        assert f.filter(self._record("myapp.module", level=logging.INFO)) is True


# ── tracer wiring ──────────────────────────────────────────────────────────


class TestTracerLogsPipelineWiring:
    """Integration between plugin_config, backends, and tracer for the logs path."""

    def _capable_backend(self):
        return _ResolvedBackend(
            type="otlp",
            endpoint="http://localhost:4318/v1/traces",
            display_name="OTLP",
            supports_logs=True,
        )

    def test_skipped_when_capture_logs_false(self):
        from opentelemetry.sdk.resources import Resource

        from hermes_otel.plugin_config import HermesOtelConfig
        from hermes_otel.tracer import HermesOTelPlugin

        plugin = HermesOTelPlugin(config=HermesOtelConfig(capture_logs=False))
        with patch.object(log_handler, "build_log_processors") as mock_build:
            plugin._init_logs_pipeline(Resource.create({}), [self._capable_backend()])
        mock_build.assert_not_called()
        assert plugin._logger_provider is None
        assert plugin._log_processors == []

    def test_installs_handler_when_capture_logs_true(self, clean_root_logger):
        from opentelemetry.sdk.resources import Resource

        from hermes_otel.plugin_config import HermesOtelConfig
        from hermes_otel.tracer import HermesOTelPlugin

        cfg = HermesOtelConfig(capture_logs=True, log_level="DEBUG")
        plugin = HermesOTelPlugin(config=cfg)

        with patch.object(log_handler, "OTLPLogExporter", new=MagicMock()):
            plugin._init_logs_pipeline(
                Resource.create({"service.name": "t"}), [self._capable_backend()]
            )

        assert plugin._logger_provider is not None
        assert len(plugin._log_processors) == 1
        # Handler reached the root logger with our marker.
        markers = [
            h for h in clean_root_logger.handlers if getattr(h, log_handler._HANDLER_MARKER, False)
        ]
        assert len(markers) == 1

    def test_warns_when_no_backend_supports_logs(self, caplog):
        from opentelemetry.sdk.resources import Resource

        from hermes_otel.plugin_config import HermesOtelConfig
        from hermes_otel.tracer import HermesOTelPlugin

        cfg = HermesOtelConfig(capture_logs=True)
        plugin = HermesOTelPlugin(config=cfg)
        traces_only = _ResolvedBackend(type="tempo", endpoint="x", supports_logs=False)

        with caplog.at_level("WARNING", logger="hermes_otel"):
            plugin._init_logs_pipeline(Resource.create({}), [traces_only])

        assert plugin._logger_provider is None
        assert any("no configured backend accepts OTLP logs" in r.message for r in caplog.records)

    def test_live_store_alone_gets_a_provider(self, clean_root_logger, caplog, tmp_path):
        from opentelemetry.sdk.resources import Resource

        from hermes_otel import live_store as ls
        from hermes_otel.plugin_config import HermesOtelConfig
        from hermes_otel.tracer import HermesOTelPlugin

        plugin = HermesOTelPlugin(config=HermesOtelConfig(capture_logs=True, dashboard_live=True))
        store = ls.LiveStore(db_path=str(tmp_path / "live.db"))
        plugin._live_active = True
        traces_only = _ResolvedBackend(type="tempo", endpoint="x", supports_logs=False)
        with (
            patch.object(ls, "get_live_store", return_value=store),
            caplog.at_level("INFO", logger="hermes_otel"),
        ):
            plugin._init_logs_pipeline(Resource.create({}), [traces_only])
        try:
            assert plugin._logger_provider is not None
            assert plugin._log_processors == []
            assert any("live store only" in r.message for r in caplog.records)
            assert not any(r.levelname == "WARNING" for r in caplog.records)
        finally:
            log_handler.uninstall_handler(None)
            store.close()

    def test_force_flush_drains_logger_provider(self):
        from hermes_otel.tracer import HermesOTelPlugin

        plugin = HermesOTelPlugin()
        plugin._logger_provider = MagicMock()
        plugin._force_flush()
        plugin._logger_provider.force_flush.assert_called_once_with(timeout_millis=2000)


def _fake(prefix: str, body: str) -> str:
    """A credential-shaped string assembled at runtime (never a literal key in source)."""
    return prefix + body


def _pipeline(resolve=None, tracker=None, live_store=None, redact=True):
    """A real provider: enricher, optional live sink, in-memory exporter; one scoped logger."""
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    exporter = InMemoryLogExporter()
    provider = LoggerProvider(resource=Resource.create({}))
    provider.add_log_record_processor(
        log_handler.HermesLogProcessor(resolve=resolve, tracker=tracker, redact=redact)
    )
    if live_store is not None:
        provider.add_log_record_processor(log_handler.LiveLogProcessor(live_store))
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    lg = logging.getLogger("test.enricher")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    lg.addHandler(handler)
    return lg, exporter, handler


class TestHermesLogProcessor:
    """Attribution never guesses, host internals never leave, secrets never leave (#265)."""

    @pytest.fixture()
    def span(self):
        from opentelemetry.sdk.trace import TracerProvider

        tracer = TracerProvider().get_tracer("t")
        s = tracer.start_span("session.abc", attributes={"hermes.platform": "telegram"})
        try:
            yield s
        finally:
            s.end()

    @pytest.fixture()
    def pipe(self):
        holder: dict = {"found": None, "tracker": None}
        lg, exporter, handler = _pipeline(
            resolve=lambda: holder["found"], tracker=_FakeTracker(holder)
        )
        try:
            yield lg, exporter, holder
        finally:
            lg.removeHandler(handler)

    def test_single_session_tier(self, pipe, span):
        lg, exporter, holder = pipe
        holder["found"] = ("sess-1", span)
        lg.info("tool terminal completed")
        (rec,) = exporter.get_finished_logs()
        ctx = span.get_span_context()
        r = rec.log_record
        assert r.trace_id == ctx.trace_id and r.span_id == ctx.span_id
        assert r.trace_flags == ctx.trace_flags
        assert r.attributes["hermes.session_id"] == "sess-1"
        assert r.attributes["gen_ai.conversation.id"] == "sess-1"
        assert r.attributes["hermes.log.attribution"] == "single_session"

    def test_unattributed_without_a_session(self, pipe):
        lg, exporter, holder = pipe
        lg.info("gateway housekeeping")
        (rec,) = exporter.get_finished_logs()
        attrs = rec.log_record.attributes or {}
        assert not rec.log_record.trace_id
        assert "hermes.session_id" not in attrs and "hermes.log.attribution" not in attrs

    def test_context_tier_keeps_a_current_span(self, pipe, span):
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider

        lg, exporter, holder = pipe
        holder["found"] = ("sess-1", span)  # would be used by the single-session tier
        tracer = TracerProvider().get_tracer("t")
        with tracer.start_as_current_span("current") as current:
            lg.info("inside a current span")
        (rec,) = exporter.get_finished_logs()
        assert rec.log_record.trace_id == current.get_span_context().trace_id
        assert rec.log_record.trace_id != span.get_span_context().trace_id
        assert rec.log_record.attributes["hermes.log.attribution"] == "context"
        assert otel_trace.get_current_span() is otel_trace.INVALID_SPAN

    def test_session_tag_tier_is_exact_under_concurrent_sessions(self, pipe, span):
        lg, exporter, holder = pipe
        holder["found"] = None  # two sessions active: the single-session rule abstains
        holder["tracker"] = {"sess-2": span}
        lg.info("worker line", extra={"session_tag": " [sess-2]"})
        (rec,) = exporter.get_finished_logs()
        r = rec.log_record
        assert r.trace_id == span.get_span_context().trace_id
        assert r.attributes["hermes.session_id"] == "sess-2"
        assert r.attributes["hermes.platform"] == "telegram"  # from the session root span
        assert r.attributes["hermes.log.attribution"] == "session_tag"
        assert "session_tag" not in r.attributes

    def test_session_tag_without_an_open_span_keeps_the_session(self, pipe):
        lg, exporter, holder = pipe
        lg.info("between turns", extra={"session_tag": " [sess-9]"})
        (rec,) = exporter.get_finished_logs()
        r = rec.log_record
        assert not r.trace_id
        assert r.attributes["hermes.session_id"] == "sess-9"
        assert r.attributes["hermes.log.attribution"] == "session_tag"

    def test_single_session_rule_abstains_when_the_record_names_another_session(self, pipe, span):
        lg, exporter, holder = pipe
        holder["found"] = ("sess-1", span)
        lg.info("line from another session", extra={"session_tag": " [sess-7]"})
        (rec,) = exporter.get_finished_logs()
        r = rec.log_record
        assert not r.trace_id  # sess-7 has no open span; sess-1's ids must not be borrowed
        assert r.attributes["hermes.session_id"] == "sess-7"

    def test_host_internal_attributes_are_dropped(self, pipe):
        lg, exporter, holder = pipe
        lg.info(
            "with hermes extras",
            extra={"hermes_home": "/Users/me/.hermes", "session_tag": "", "job_home_path": "/x"},
        )
        (rec,) = exporter.get_finished_logs()
        attrs = rec.log_record.attributes
        assert "hermes_home" not in attrs and "session_tag" not in attrs
        assert "job_home_path" not in attrs
        assert attrs["code.function.name"]  # the SDK's own attributes survive

    def test_body_and_string_attributes_are_redacted(self, pipe, monkeypatch):
        from hermes_otel import redaction

        monkeypatch.setattr(redaction, "_hermes_resolved", True)
        monkeypatch.setattr(redaction, "_hermes_redactor", None)  # exercise the built-in set
        lg, exporter, holder = pipe
        lg.info(
            "calling with Authorization: Bearer "
            + _fake("sk-proj-", "abcdefghijklmnopqrstuvwxyz0123456789"),
            extra={
                "request_headers": "x-api-key: " + _fake("hcaik_", "0123456789abcdef"),
                "count": 3,
            },
        )
        (rec,) = exporter.get_finished_logs()
        r = rec.log_record
        assert _fake("sk-proj-", "abcdefghijklmnopqrstuvwxyz0123456789") not in r.body
        assert r.body.startswith("calling with Authorization: Bearer sk-pro")
        assert _fake("hcaik_", "0123456789abcdef") not in r.attributes["request_headers"]
        assert r.attributes["count"] == 3

    def test_redaction_can_be_disabled_for_the_processor(self, span):
        lg, exporter, handler = _pipeline(redact=False)
        try:
            lg.info("token=" + _fake("sk-", "abcdefghijklmnopqrstuvwxyz"))
        finally:
            lg.removeHandler(handler)
        (rec,) = exporter.get_finished_logs()
        assert rec.log_record.body == "token=" + _fake("sk-", "abcdefghijklmnopqrstuvwxyz")

    def test_processor_never_raises(self):
        proc = log_handler.HermesLogProcessor(resolve=lambda: 1 / 0, tracker=object())

        class Broken:
            def __getattr__(self, name):
                raise RuntimeError(name)

        proc.on_emit(Broken())  # swallowed


class _FakeTracker:
    """Just enough of SpanTracker for the session_tag tier."""

    def __init__(self, holder):
        self._holder = holder

    def get_current_parent(self, session_id):
        return (self._holder.get("tracker") or {}).get(session_id)

    def get_session_root(self, session_id):
        return (self._holder.get("tracker") or {}).get(session_id)


class TestLiveSinkOnTheProvider:
    def test_live_rows_match_the_exported_record(self, tmp_path, monkeypatch):
        from hermes_otel import redaction
        from hermes_otel.live_store import LiveStore

        monkeypatch.setattr(redaction, "_hermes_resolved", True)
        monkeypatch.setattr(redaction, "_hermes_redactor", None)
        store = LiveStore(db_path=str(tmp_path / "live.db"))
        lg, exporter, handler = _pipeline(live_store=store)
        try:
            lg.warning(
                "secret " + _fake("sk-", "abcdefghijklmnopqrstuvwxyz0123"),
                extra={"session_tag": " [s-1]"},
            )
        finally:
            lg.removeHandler(handler)
            store.flush()
        (rec,) = exporter.get_finished_logs()
        (row,) = store.logs()
        store.close()
        assert row["body"] == rec.log_record.body  # redacted identically
        assert _fake("sk-", "abcdefghijklmnopqrstuvwxyz0123") not in row["body"]
        assert row["session_id"] == "s-1" == rec.log_record.attributes["hermes.session_id"]
        assert row["attributes"]["hermes.log.attribution"] == "session_tag"
        assert row["level"] == "WARNING" and rec.log_record.severity_text == "WARN"

    def test_live_sink_drops_noise_and_debug(self, tmp_path):
        from hermes_otel.live_store import LiveStore

        store = LiveStore(db_path=str(tmp_path / "live.db"))
        lg, exporter, handler = _pipeline(live_store=store)
        try:
            logging.getLogger("gateway.config").propagate = False
            lg.info("Plugin platform 'raft' available but not configured")
            lg.info("kept")
        finally:
            lg.removeHandler(handler)
            store.flush()
        assert len(exporter.get_finished_logs()) == 2  # the backend still gets both
        assert [r["body"] for r in store.logs()] == ["kept"]
        store.close()

    def test_install_handler_registers_the_enricher_first_then_live_then_exporters(
        self, clean_root_logger, fake_otlp_exporter, tmp_path
    ):
        from opentelemetry.sdk.resources import Resource

        from hermes_otel.backends import _ResolvedBackend
        from hermes_otel.live_store import LiveStore

        store = LiveStore(db_path=str(tmp_path / "live.db"))
        backend = _ResolvedBackend(type="otlp", endpoint="http://x/v1/traces", supports_logs=True)
        processors = log_handler.build_log_processors([backend])
        provider = log_handler.install_handler(
            resource=Resource.create({}),
            processors=processors,
            level=logging.INFO,
            context_resolver=lambda: None,
            live_store=store,
        )
        assert provider is not None
        chain = provider._multi_log_record_processor._log_record_processors
        assert isinstance(chain[0], log_handler.HermesLogProcessor)
        assert isinstance(chain[1], log_handler.LiveLogProcessor)
        assert len(chain) == 3
        store.close()

    def test_install_handler_with_live_store_only(self, clean_root_logger, tmp_path):
        from opentelemetry.sdk.resources import Resource

        from hermes_otel.live_store import LiveStore

        store = LiveStore(db_path=str(tmp_path / "live.db"))
        provider = log_handler.install_handler(
            resource=Resource.create({}), processors=[], level=logging.INFO, live_store=store
        )
        assert provider is not None
        chain = provider._multi_log_record_processor._log_record_processors
        assert [type(c).__name__ for c in chain] == ["HermesLogProcessor", "LiveLogProcessor"]
        store.close()

    def test_python_to_severity_bands(self):
        f = log_handler._python_to_severity
        assert (f(logging.DEBUG), f(logging.INFO), f(logging.WARNING)) == (5, 9, 13)
        assert (f(logging.ERROR), f(logging.CRITICAL), f(0)) == (17, 21, 1)
