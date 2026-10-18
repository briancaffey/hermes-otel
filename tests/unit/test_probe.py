"""#481: the reachability probe: URL parsing, one connect per host:port, a line per backend."""

from __future__ import annotations

import socket
import threading

import pytest

from hermes_otel import probe
from hermes_otel.backends import _ResolvedBackend
from hermes_otel.probe import (
    ProbeResult,
    backend_targets,
    parse_host_port,
    probe_backends,
    probe_tcp,
)


@pytest.fixture
def listener():
    """A listening socket on an ephemeral port that accepts and closes."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    stop = threading.Event()

    def accept():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except socket.timeout:
                continue

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    yield srv.getsockname()[1]
    stop.set()
    srv.close()


@pytest.fixture
def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestParse:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("http://localhost:4318/v1/traces", ("localhost", 4318)),
            ("https://api.honeycomb.io/v1/traces", ("api.honeycomb.io", 443)),
            ("http://collector/v1/traces", ("collector", 80)),
            ("grpc://otel:4317", ("otel", 4317)),
            ("http://[::1]:4318/v1/traces", ("::1", 4318)),
            ("  http://x:1/  ", ("x", 1)),
            # A gRPC target is a bare host:port (#469).
            ("localhost:4317", ("localhost", 4317)),
            ("telemetry.example:443", ("telemetry.example", 443)),
            ("[::1]:4317", ("::1", 4317)),
        ],
    )
    def test_host_and_port(self, url, expected):
        assert parse_host_port(url) == expected

    @pytest.mark.parametrize(
        "url", ["", None, "nonsense", "http://", "http://host:notaport/", "host:notaport"]
    )
    def test_no_host_is_none(self, url):
        assert parse_host_port(url) is None


class TestProbeTcp:
    def test_listening_port_is_reachable(self, listener):
        ok, ms, error = probe_tcp("127.0.0.1", listener, 1.0)
        assert ok and error == "" and ms >= 0

    def test_closed_port_is_refused(self, closed_port):
        ok, _ms, error = probe_tcp("127.0.0.1", closed_port, 1.0)
        assert not ok and "refused" in error.lower()

    def test_unknown_host_does_not_resolve(self, monkeypatch):
        # Patched, not a real lookup: on macOS a resolver call in the pytest
        # process makes a later subprocess fork crash (the refactor-check
        # tests run `git archive` after this file).
        def no_such(*_a, **_k):
            raise socket.gaierror(8, "nodename nor servname provided")

        monkeypatch.setattr(probe.socket, "getaddrinfo", no_such)
        ok, _ms, error = probe_tcp("no-such-host.invalid", 80, 1.0)
        assert not ok and error == "name does not resolve"

    def test_timeout_is_reported(self, monkeypatch):
        class Slow:
            def __init__(self, *_a, **_k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def settimeout(self, _t):
                pass

            def connect(self, _addr):
                raise socket.timeout()

        monkeypatch.setattr(probe.socket, "socket", Slow)
        ok, _ms, error = probe_tcp("127.0.0.1", 4318, 0.25)
        assert not ok and error == "timed out after 0.25 s"


def _b(name, endpoint, **kw):
    return _ResolvedBackend(type="otlp", endpoint=endpoint, display_name=name, **kw)


class TestTargets:
    def test_signals_on_one_host_port_are_one_target(self):
        b = _b(
            "oo",
            "http://oo:5080/api/default/v1/traces",
            metrics_endpoint="http://oo:5080/api/default/v1/metrics",
            logs_endpoint="http://oo:5080/api/default/v1/logs",
            supports_logs=True,
        )
        assert backend_targets([b]) == [("oo", "oo", 5080)]

    def test_a_metrics_store_on_another_port_is_its_own_target(self):
        b = _b("g", "http://g:4000/v1/traces", metrics_endpoint="http://g:4001/v1/metrics")
        assert backend_targets([b]) == [("g", "g", 4000), ("g", "g", 4001)]

    def test_unsupported_signal_endpoints_are_skipped(self):
        b = _b(
            "t",
            "http://t:1/v1/traces",
            metrics_endpoint="http://t:2/v1/metrics",
            supports_metrics=False,
            logs_endpoint="http://t:3/v1/logs",
            supports_logs=False,
        )
        assert backend_targets([b]) == [("t", "t", 1)]

    def test_unparseable_endpoint_is_skipped(self):
        assert backend_targets([_b("x", "nonsense")]) == []

    def test_two_backends_on_one_host_port_connect_once_and_each_get_a_line(self, monkeypatch):
        calls = []

        def fake(address, timeout_s):
            calls.append(address[1])
            return True, 1.0, ""

        monkeypatch.setattr(probe, "resolve", lambda h, p: ((2, (h, p)), ""))
        monkeypatch.setattr(probe, "connect", fake)
        results = probe_backends([_b("a", "http://h:1/v1/traces"), _b("b", "http://h:1/v1/traces")])
        assert calls == [("h", 1)]
        assert [(r.backend, r.ok) for r in results] == [("a", True), ("b", True)]


class TestResultLine:
    def test_reachable_and_unreachable_lines(self):
        ok = ProbeResult("jaeger", "localhost", 4318, True, 2.6)
        assert ok.line() == "[hermes-otel] ✓ jaeger reachable at localhost:4318 (3 ms)"
        bad = ProbeResult("lgtm", "localhost", 4318, False, 1.0, "Connection refused")
        assert bad.line().startswith(
            "[hermes-otel] ⚠ lgtm unreachable at localhost:4318 (Connection refused)"
        )


class TestStartProbe:
    def test_runs_off_thread_and_reports(self, listener):
        got = []
        done = threading.Event()

        def on_done(results):
            got.extend(results)
            done.set()

        t = probe._real_start_probe(
            [_b("l", f"http://127.0.0.1:{listener}/v1/traces")], on_done, 1.0
        )
        assert t is not None and t.daemon
        assert done.wait(5.0)
        assert [(r.backend, r.ok) for r in got] == [("l", True)]

    def test_nothing_to_probe(self):
        assert probe._real_start_probe([], lambda r: None) is None
        assert probe._real_start_probe([_b("x", "nonsense")], lambda r: None) is None

    def test_unresolvable_host_is_reported_without_a_connect(self, monkeypatch):
        monkeypatch.setattr(probe, "resolve", lambda h, p: (None, "name does not resolve"))
        monkeypatch.setattr(probe, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
        got = []
        done = threading.Event()
        t = probe._real_start_probe(
            [_b("x", "http://no-such-host.invalid:1/v1/traces")],
            lambda r: (got.extend(r), done.set()),
            1.0,
        )
        assert t is not None and done.wait(5.0)
        assert [(r.ok, r.error) for r in got] == [(False, "name does not resolve")]
