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
        # Global headers override per-backend on key collision, matches
        # tracer._merge_headers semantics.
        assert headers == {
            "X-From-Backend": "a",
            "X-Global": "b",
            "X-Both": "global",
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
            h for h in clean_root_logger.handlers
            if getattr(h, log_handler._HANDLER_MARKER, False)
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
            markers = [
                h for h in target.handlers
                if getattr(h, log_handler._HANDLER_MARKER, False)
            ]
            assert len(markers) == 1
            # Root gets nothing — scoped install must not leak.
            root_markers = [
                h for h in clean_root_logger.handlers
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
            h for h in clean_root_logger.handlers
            if getattr(h, log_handler._HANDLER_MARKER, False)
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
        from hermes_otel.plugin_config import HermesOtelConfig
        from hermes_otel.tracer import HermesOTelPlugin
        from opentelemetry.sdk.resources import Resource

        plugin = HermesOTelPlugin(config=HermesOtelConfig(capture_logs=False))
        with patch.object(log_handler, "build_log_processors") as mock_build:
            plugin._init_logs_pipeline(Resource.create({}), [self._capable_backend()])
        mock_build.assert_not_called()
        assert plugin._logger_provider is None
        assert plugin._log_processors == []

    def test_installs_handler_when_capture_logs_true(self, clean_root_logger):
        from hermes_otel.plugin_config import HermesOtelConfig
        from hermes_otel.tracer import HermesOTelPlugin
        from opentelemetry.sdk.resources import Resource

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
            h for h in clean_root_logger.handlers
            if getattr(h, log_handler._HANDLER_MARKER, False)
        ]
        assert len(markers) == 1

    def test_warns_when_no_backend_supports_logs(self, caplog):
        from hermes_otel.plugin_config import HermesOtelConfig
        from hermes_otel.tracer import HermesOTelPlugin
        from opentelemetry.sdk.resources import Resource

        cfg = HermesOtelConfig(capture_logs=True)
        plugin = HermesOTelPlugin(config=cfg)
        traces_only = _ResolvedBackend(
            type="tempo", endpoint="x", supports_logs=False
        )

        with caplog.at_level("WARNING", logger="hermes_otel"):
            plugin._init_logs_pipeline(Resource.create({}), [traces_only])

        assert plugin._logger_provider is None
        assert any("no configured backend accepts OTLP logs" in r.message for r in caplog.records)

    def test_force_flush_drains_logger_provider(self):
        from hermes_otel.tracer import HermesOTelPlugin

        plugin = HermesOTelPlugin()
        plugin._logger_provider = MagicMock()
        plugin._force_flush()
        plugin._logger_provider.force_flush.assert_called_once_with(timeout_millis=2000)
