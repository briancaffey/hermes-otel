"""Backend reachability probe (#481): one TCP connect per endpoint at init.

A backend that is down used to show up 30 s later as a failed export inside
the batch processor's worker thread, in a log line most users never see.
Now ``init()`` opens one TCP connection to each distinct host and port the
configured backends export to (traces, metrics and logs endpoints) and logs
one line per backend: reachable with the connect time, or unreachable with
the error. Names are resolved on the init thread (milliseconds); the
connects, which can wait out a timeout, run on a background thread so
startup latency is unchanged.

It runs once; nothing polls afterwards. It sends no bytes, so it needs no
credentials and says nothing about paths, TLS or auth: a reverse proxy that
accepts the connection passes even when the service behind it is down.
Exports are never gated on the result. ``probe_backends: false`` skips it.

Only the standard library is imported here.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

DEFAULT_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class ProbeResult:
    backend: str  # display name
    host: str
    port: int
    ok: bool
    ms: float  # connect time, or time until the error
    error: str = ""  # empty when ok

    @property
    def target(self) -> str:
        return f"{self.host}:{self.port}"

    def line(self) -> str:
        """The one startup-log line for this backend."""
        if self.ok:
            return f"[hermes-otel] ✓ {self.backend} reachable at {self.target} ({self.ms:.0f} ms)"
        return (
            f"[hermes-otel] ⚠ {self.backend} unreachable at {self.target} ({self.error}); "
            "exports will be retried and dropped when the queue fills"
        )


def parse_host_port(endpoint: Any) -> Optional[Tuple[str, int]]:
    """``(host, port)`` from an OTLP endpoint URL, or None when it has no host.

    The default port is 80 for ``http``, 443 for ``https``, else 4318.
    """
    try:
        parts = urlsplit(str(endpoint or "").strip())
    except ValueError:
        return None
    if not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        port = {"http": 80, "https": 443}.get(parts.scheme, 4318)
    return parts.hostname, int(port)


def resolve(host: str, port: int) -> Tuple[Optional[Tuple[int, Any]], str]:
    """``((family, sockaddr), "")`` for the first stream address of ``host``,
    or ``(None, error)``. Runs on the caller's thread: name resolution is
    kept off background threads on purpose, since a ``fork()`` while another
    thread is inside the resolver can crash the child on macOS, and Hermes
    spawns tool subprocesses right after init."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None, "name does not resolve"
    except OSError as e:
        return None, e.strerror or e.__class__.__name__
    if not infos:
        return None, "name does not resolve"
    family, _type, _proto, _canon, sockaddr = infos[0]
    return (family, sockaddr), ""


def connect(
    address: Tuple[int, Any], timeout_s: float = DEFAULT_TIMEOUT_S
) -> Tuple[bool, float, str]:
    """``(ok, ms, error)`` for one TCP connect to a resolved address; never raises."""
    family, sockaddr = address
    start = time.perf_counter()
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_s)
            sock.connect(sockaddr)
        return True, (time.perf_counter() - start) * 1000.0, ""
    except socket.timeout:
        return False, (time.perf_counter() - start) * 1000.0, f"timed out after {timeout_s:g} s"
    except OSError as e:
        return False, (time.perf_counter() - start) * 1000.0, e.strerror or e.__class__.__name__
    except Exception as e:  # pragma: no cover - defensive
        return False, (time.perf_counter() - start) * 1000.0, e.__class__.__name__


def probe_tcp(
    host: str, port: int, timeout_s: float = DEFAULT_TIMEOUT_S
) -> Tuple[bool, float, str]:
    """``(ok, ms, error)`` for one resolve + connect on the calling thread; never raises."""
    address, error = resolve(host, port)
    if address is None:
        return False, 0.0, error
    return connect(address, timeout_s)


def backend_targets(backends: Iterable[Any]) -> List[Tuple[str, str, int]]:
    """``(display name, host, port)`` for every distinct host:port a backend
    exports to, in config order; a backend whose signals share one host:port
    is probed once, and two backends on the same host:port each get a line
    from the same connect."""
    out: List[Tuple[str, str, int]] = []
    for b in backends:
        name = str(getattr(b, "display_name", None) or getattr(b, "type", None) or "backend")
        endpoints = [getattr(b, "endpoint", None)]
        for attr, flag in (
            ("metrics_endpoint", "supports_metrics"),
            ("logs_endpoint", "supports_logs"),
        ):
            if getattr(b, flag, False) and getattr(b, attr, None):
                endpoints.append(getattr(b, attr))
        for ep in endpoints:
            parsed = parse_host_port(ep)
            if parsed is None:
                continue
            entry = (name, parsed[0], parsed[1])
            if entry not in out:
                out.append(entry)
    return out


def probe_backends(
    backends: Iterable[Any], timeout_s: float = DEFAULT_TIMEOUT_S
) -> List[ProbeResult]:
    """Resolve and connect to every distinct host:port once, on the calling
    thread; results in config order."""
    targets = backend_targets(backends)
    return _connect_all(targets, _resolve_all(targets), timeout_s)


def _resolve_all(
    targets: List[Tuple[str, str, int]],
) -> Dict[Tuple[str, int], Tuple[Optional[Tuple[int, Any]], str]]:
    return {(h, p): resolve(h, p) for _n, h, p in dict.fromkeys((n, h, p) for n, h, p in targets)}


def _connect_all(
    targets: List[Tuple[str, str, int]],
    resolved: Dict[Tuple[str, int], Tuple[Optional[Tuple[int, Any]], str]],
    timeout_s: float,
) -> List[ProbeResult]:
    cache: Dict[Tuple[str, int], Tuple[bool, float, str]] = {}
    results: List[ProbeResult] = []
    for name, host, port in targets:
        if (host, port) not in cache:
            address, error = resolved[(host, port)]
            cache[(host, port)] = (
                (False, 0.0, error) if address is None else connect(address, timeout_s)
            )
        ok, ms, error = cache[(host, port)]
        results.append(ProbeResult(name, host, port, ok, ms, error))
    return results


def start_probe(
    backends: Iterable[Any],
    on_done: Callable[[List[ProbeResult]], None],
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Optional[threading.Thread]:
    """Resolve every target now (milliseconds, on this thread), then connect
    on a daemon thread and hand the results to ``on_done``; returns the
    thread (None when there is nothing to probe)."""
    targets = backend_targets(list(backends))
    if not targets:
        return None
    resolved = _resolve_all(targets)

    def run() -> None:
        try:
            on_done(_connect_all(targets, resolved, timeout_s))
        except Exception:  # pragma: no cover - the probe may never break init
            pass

    thread = threading.Thread(target=run, name="hermes-otel-probe", daemon=True)
    thread.start()
    return thread


# The unpatched function, for tests that restore it after the suite-wide no-op.
_real_start_probe = start_probe
