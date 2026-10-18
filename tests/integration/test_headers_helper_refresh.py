"""The 401/403 refresh path of ``headers_helper`` (#483) against an in-process
OTLP receiver on 127.0.0.1, through the real OTLP/HTTP exporters.

The receiver refuses every request whose ``Authorization`` is not the token it
currently accepts. The helper prints the token it finds in a file; the test
rotates both and shows exactly one helper re-run and one retry of the same
batch, and that a permanent 401 drops the batch after that one retry.
"""

from __future__ import annotations

import logging
import shlex
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

import pytest

from hermes_otel.backend_config import BackendConfig
from hermes_otel.backends import resolve
from hermes_otel.headers_helper import RefreshingSession
from hermes_otel.pipeline import build_pipeline, exporter_kwargs
from hermes_otel.plugin_config import HermesOtelConfig

LOOPBACK = "127.0.0.1"


class Receiver:
    """An OTLP/HTTP endpoint that 401s unless the bearer token matches."""

    def __init__(self) -> None:
        self.accepted = "first"
        self.requests: List[Dict[str, Any]] = []
        self.status_when_refused = 401
        self._lock = threading.Lock()
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                auth = self.headers.get("Authorization") or ""
                ok = auth == f"Bearer {receiver.accepted}"
                with receiver._lock:
                    receiver.requests.append(
                        {"path": self.path, "auth": auth, "ok": ok, "bytes": len(body)}
                    )
                self.send_response(200 if ok else receiver.status_when_refused)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: Any) -> None:  # quiet
                pass

        self.server = ThreadingHTTPServer((LOOPBACK, 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def traces_url(self) -> str:
        return f"http://{LOOPBACK}:{self.server.server_address[1]}/v1/traces"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def receiver():
    r = Receiver()
    try:
        yield r
    finally:
        r.close()


@pytest.fixture()
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token"
    path.write_text("first", encoding="utf-8")
    return path


@pytest.fixture()
def helper_command(tmp_path: Path, token_file: Path) -> str:
    """A helper that prints ``Authorization=Bearer <token file>``."""
    body = (
        "import pathlib\n"
        f"token = pathlib.Path({str(token_file)!r}).read_text().strip()\n"
        "print('Authorization=Bearer ' + token)\n"
    )
    path = tmp_path / "mint.py"
    path.write_text(body, encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"


def _backend(receiver: Receiver, helper_command: str, **fields: Any):
    return resolve(
        BackendConfig(
            type="otlp",
            endpoint=receiver.traces_url,
            headers={"x-static": "kept"},
            headers_helper=helper_command,
            **fields,
        )
    )


def _one_span_exporter(rb):
    """A real ``OTLPSpanExporter`` built the way the pipeline builds it, and a
    provider that exports synchronously through it."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    target = rb.target("traces")
    exporter = OTLPSpanExporter(**exporter_kwargs(target, "test"))
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider


def _rotate(receiver: Receiver, token_file: Path, new: str) -> None:
    token_file.write_text(new, encoding="utf-8")
    receiver.accepted = new


class TestRefreshOnce:
    def test_401_refreshes_once_and_retries_the_same_batch_once(
        self, receiver, token_file, helper_command
    ):
        rb = _backend(receiver, helper_command)
        exporter, provider = _one_span_exporter(rb)
        session = exporter._session
        assert isinstance(session, RefreshingSession)

        provider.get_tracer("t").start_span("before-rotation").end()
        assert [r["ok"] for r in receiver.requests] == [True]

        _rotate(receiver, token_file, "second")
        provider.get_tracer("t").start_span("after-rotation").end()

        # Exactly: the refused request, one helper re-run, one retry that lands.
        assert [(r["auth"], r["ok"]) for r in receiver.requests[1:]] == [
            ("Bearer first", False),
            ("Bearer second", True),
        ]
        assert rb.headers_helper.runs == 2
        assert session.refreshes == 1 and session.retries == 1
        # The retry carried the same batch and the static headers.
        assert receiver.requests[1]["bytes"] == receiver.requests[2]["bytes"] > 0
        assert session.headers["x-static"] == "kept"

        # Later batches go straight through with the new credential.
        provider.get_tracer("t").start_span("steady").end()
        assert receiver.requests[-1] == {
            "path": "/v1/traces",
            "auth": "Bearer second",
            "ok": True,
            "bytes": receiver.requests[-1]["bytes"],
        }
        assert rb.headers_helper.runs == 2
        provider.shutdown()

    def test_403_takes_the_same_path(self, receiver, token_file, helper_command):
        receiver.status_when_refused = 403
        rb = _backend(receiver, helper_command)
        exporter, provider = _one_span_exporter(rb)
        _rotate(receiver, token_file, "second")
        provider.get_tracer("t").start_span("s").end()
        assert [r["ok"] for r in receiver.requests] == [False, True]
        assert exporter._session.refreshes == 1
        provider.shutdown()

    def test_permanent_401_drops_the_batch_after_one_retry(
        self, receiver, token_file, helper_command, caplog
    ):
        from opentelemetry.sdk.trace.export import SpanExportResult

        rb = _backend(receiver, helper_command)
        exporter, provider = _one_span_exporter(rb)
        receiver.accepted = "never"  # the helper keeps printing "first"
        tracer = provider.get_tracer("t")
        span = tracer.start_span("s")
        span.end()
        # SimpleSpanProcessor swallows the result; export the same span again
        # directly to read it.
        with caplog.at_level(logging.ERROR, logger="opentelemetry"):
            result = exporter.export([span])
        assert result is SpanExportResult.FAILURE
        # Two attempts per batch: the refused one and the single retry.
        assert [r["ok"] for r in receiver.requests] == [False, False, False, False]
        assert rb.headers_helper.runs == 3  # the first mint + one refresh per batch
        assert exporter._session.retries == 2
        assert "401" in caplog.text
        provider.shutdown()

    def test_failed_refresh_returns_the_401_without_a_retry(
        self, receiver, token_file, helper_command, tmp_path
    ):
        from opentelemetry.sdk.trace.export import SpanExportResult

        rb = _backend(receiver, helper_command)
        exporter, provider = _one_span_exporter(rb)
        receiver.accepted = "never"
        (tmp_path / "mint.py").write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
        span = provider.get_tracer("t").start_span("s")
        span.end()
        assert exporter.export([span]) is SpanExportResult.FAILURE
        assert [r["ok"] for r in receiver.requests] == [False, False]  # no retry either time
        assert exporter._session.refreshes == 0 and exporter._session.retries == 0
        provider.shutdown()


class TestThroughThePipeline:
    """The real pipeline: spans through the BatchSpanProcessor, a metric point
    through the PeriodicExportingMetricReader and a log record through the log
    exporter, three sessions on one helper (#584)."""

    LOGGER = "hermes_otel_test_logger"
    PATHS = ("/v1/traces", "/v1/metrics", "/v1/logs")

    def _pipeline(self, receiver, helper_command):
        rb = _backend(receiver, helper_command, logs=True, metrics=True)
        cfg = HermesOtelConfig(
            dashboard_live=False,
            probe_backends=False,
            capture_logs=True,
            log_attach_logger=self.LOGGER,
            span_batch_schedule_delay_ms=50,
        )
        pipeline = build_pipeline(cfg, [rb], "default")
        assert pipeline is not None
        assert len(pipeline.span_processors) == len(pipeline.metric_readers) == 1
        return rb, cfg, pipeline

    def _emit(self, pipeline, n: int) -> None:
        pipeline.tracer.start_span(f"span-{n}").end()
        pipeline.meter.create_counter("hermes_otel_test_counter").add(1)
        logging.getLogger(self.LOGGER).warning("record %d", n)

    def _flushers(self, pipeline):
        return {
            "/v1/traces": lambda: pipeline.span_processors[0].force_flush(5000),
            "/v1/metrics": lambda: pipeline.metric_readers[0].force_flush(5000),
            "/v1/logs": lambda: pipeline.logger_provider.force_flush(5000),
        }

    def _warm(self, receiver, pipeline) -> int:
        self._emit(pipeline, 0)
        for flush in self._flushers(pipeline).values():
            flush()
        assert {r["path"] for r in receiver.requests} == set(self.PATHS)
        assert all(r["ok"] for r in receiver.requests)
        return len(receiver.requests)

    @staticmethod
    def _attempts(later, path):
        return [(r["auth"], r["ok"]) for r in later if r["path"] == path]

    def test_one_refresh_serves_the_signals_that_send_after_it(
        self, receiver, token_file, helper_command
    ):
        rb, cfg, pipeline = self._pipeline(receiver, helper_command)
        try:
            before = self._warm(receiver, pipeline)
            assert rb.headers_helper.runs == 1

            _rotate(receiver, token_file, "second")
            self._emit(pipeline, 1)
            flushers = self._flushers(pipeline)
            for path in self.PATHS:
                flushers[path]()

            later = receiver.requests[before:]
            # Traces met the rotation: refused once, re-minted, retried.
            assert self._attempts(later, "/v1/traces") == [
                ("Bearer first", False),
                ("Bearer second", True),
            ]
            # Metrics and logs picked the new set up before sending: no 401.
            for path in ("/v1/metrics", "/v1/logs"):
                assert self._attempts(later, path) == [("Bearer second", True)], path
            assert rb.headers_helper.runs == 2  # the first mint + one refresh
        finally:
            pipeline.shutdown(cfg.log_attach_logger)

    def test_concurrent_refusals_run_the_helper_once(self, receiver, token_file, helper_command):
        """All three exporters send at once after the rotation; however their
        401s interleave, every one recovers and the helper runs once."""
        rb, cfg, pipeline = self._pipeline(receiver, helper_command)
        try:
            before = self._warm(receiver, pipeline)
            _rotate(receiver, token_file, "second")
            self._emit(pipeline, 1)
            start = threading.Barrier(len(self.PATHS))
            errors: List[BaseException] = []

            def run(flush) -> None:
                try:
                    start.wait(5)
                    flush()
                except BaseException as e:  # pragma: no cover - surfaced below
                    errors.append(e)

            threads = [
                threading.Thread(target=run, args=(flush,))
                for flush in self._flushers(pipeline).values()
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
            assert not errors

            later = receiver.requests[before:]
            for path in self.PATHS:
                attempts = self._attempts(later, path)
                # At most the one refusal, and the batch lands with the new token.
                assert attempts[-1] == ("Bearer second", True), path
                assert attempts[:-1] in ([], [("Bearer first", False)]), path
            assert rb.headers_helper.runs == 2
        finally:
            pipeline.shutdown(cfg.log_attach_logger)
