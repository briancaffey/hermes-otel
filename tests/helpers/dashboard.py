"""Scaffolding for the dashboard adapter tests: isolation from the developer's
config and the probe caches, a scripted HTTP layer, adapter construction, and
the FastAPI app the contract tests drive."""

from __future__ import annotations

import copy
import importlib
import io
import pkgutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib import error as _urlerror
from urllib import parse as _urlparse

import pytest

from hermes_otel.dashboard import backends
from hermes_otel.dashboard.backends.base import BackendError

NS = 1_000_000_000

# Module-level probe caches the adapters keep (what a server answered once).
# ``backends.clear_caches()`` clears the config and instance caches only.
PROBE_CACHES: Tuple[Tuple[str, str], ...] = (
    ("jaeger", "_API_PROBE_CACHE"),
    ("phoenix", "_ROOT_ARGS_CACHE"),
    ("uptrace", "_DIALECT_CACHE"),
    ("langfuse", "_API_CACHE"),
    ("greptime", "_COLUMNS_CACHE"),
)


def backend_modules() -> List[Any]:
    """Every module of the ``dashboard.backends`` package (adapters and mixins)."""
    pkg = importlib.import_module("hermes_otel.dashboard.backends")
    out = []
    for info in pkgutil.iter_modules(pkg.__path__):
        out.append(importlib.import_module(f"hermes_otel.dashboard.backends.{info.name}"))
    return out


def clear_probe_caches() -> None:
    for mod, name in PROBE_CACHES:
        cache = getattr(
            importlib.import_module(f"hermes_otel.dashboard.backends.{mod}"), name, None
        )
        if isinstance(cache, dict):
            cache.clear()


def isolate_backends(monkeypatch) -> None:
    """No developer config, no cached adapters, no probed server dialects."""
    monkeypatch.setattr(backends, "top_level_config", lambda: {})
    backends.clear_caches()
    clear_probe_caches()


@pytest.fixture(autouse=True)
def isolated_backends(request, monkeypatch):
    """Autouse for every dashboard test module (``test_dashboard_*``,
    ``test_openobserve_*``): the developer's real ``hermes_otel.yaml`` and any
    cache a previous test filled must never leak into an adapter test."""
    path = getattr(request.node, "path", None) or Path(str(request.node.fspath))
    name = Path(str(path)).name
    if not name.startswith(("test_dashboard", "test_openobserve")):
        yield
        return
    isolate_backends(monkeypatch)
    yield
    backends.clear_caches()
    clear_probe_caches()


def adapter(backend_type: str, **cfg: Any):
    """An adapter instance for ``backend_type`` from a minimal config entry."""
    cls = backends.find_adapter_class(backend_type)
    assert cls is not None, f"no adapter handles {backend_type!r}"
    return cls({"type": backend_type, **cfg})


# ── scripted HTTP ──────────────────────────────────────────────────────

_MISSING = object()


class FakeHttp:
    """The adapters' ``http_get_json`` / ``http_post_json`` replaced by a route table.

    ``routes`` maps a path (exact) or a path prefix to an answer: a JSON-like
    value (deep-copied per call, since adapters mutate rows), an ``Exception``
    (raised), or a callable ``(url, body, headers) -> value``. An exact path
    wins over a prefix; a longer prefix over a shorter one. ``queue`` answers
    in order regardless of path. An unrouted call raises unless ``default`` is
    given. Every call is recorded in ``calls`` as ``(method, url, body, headers)``.
    """

    def __init__(
        self,
        routes: Optional[Dict[str, Any]] = None,
        *,
        queue: Optional[List[Any]] = None,
        default: Any = _MISSING,
    ) -> None:
        self.routes: Dict[str, Any] = dict(routes or {})
        self.queue: List[Any] = list(queue or [])
        self.default = default
        self.calls: List[Tuple[str, str, Any, Optional[Dict[str, str]]]] = []

    @property
    def urls(self) -> List[str]:
        return [c[1] for c in self.calls]

    def _answer(self, method: str, url: str, body: Any, headers: Any) -> Any:
        self.calls.append((method, url, body, headers))
        if self.queue:
            ans = self.queue.pop(0)
        else:
            path = _urlparse.urlsplit(url).path
            ans = self.routes.get(path, _MISSING)
            if ans is _MISSING:
                for prefix in sorted(self.routes, key=len, reverse=True):
                    if path.startswith(prefix):
                        ans = self.routes[prefix]
                        break
            if ans is _MISSING:
                if self.default is _MISSING:
                    raise AssertionError(f"unexpected {method} {url}")
                ans = self.default
        if isinstance(ans, Exception):
            raise ans
        if callable(ans):
            return ans(url, body, headers)
        return copy.deepcopy(ans)

    def get(self, url: str, headers=None, timeout: float = 10.0) -> Any:
        return self._answer("GET", url, None, headers)

    def post(self, url: str, body: Any = None, headers=None, timeout: float = 10.0) -> Any:
        return self._answer("POST", url, body, headers)

    def install(self, monkeypatch, *modules: Any) -> "FakeHttp":
        """Patch the HTTP functions on ``modules`` (default: every backend module)."""
        for m in modules or backend_modules():
            if hasattr(m, "http_get_json"):
                monkeypatch.setattr(m, "http_get_json", self.get)
            if hasattr(m, "http_post_json"):
                monkeypatch.setattr(m, "http_post_json", self.post)
        return self


def fake_http(monkeypatch, routes: Optional[Dict[str, Any]] = None, **kw) -> FakeHttp:
    """``FakeHttp(routes, **kw)`` installed on every backend module."""
    return FakeHttp(routes, **kw).install(monkeypatch)


# ── the contract app ───────────────────────────────────────────────────

CONTRACT_CFG: List[Dict[str, Any]] = [
    {"type": "phoenix", "name": "phx", "endpoint": "http://localhost:6006"},
    {
        "type": "langfuse",
        "name": "lf",
        "endpoint": "http://localhost:3000",
        "public_key": "pk",
        "secret_key": "sk",
    },
    {"type": "jaeger", "name": "jg", "endpoint": "http://localhost:16686"},
    {"type": "lgtm", "name": "lgtm", "endpoint": "http://localhost:4318/v1/traces"},
    {"type": "signoz", "name": "sz", "endpoint": "http://localhost:3301", "api_key": "k"},
    {"type": "uptrace", "name": "up", "endpoint": "http://localhost:14318", "user_token": "t"},
    {
        "type": "openobserve",
        "name": "oo",
        "endpoint": "http://user:pw@localhost:5080/api/default/v1/traces",
        "user": "u",
        "password": "p",
    },
]


@pytest.fixture()
def client(monkeypatch):
    """A FastAPI test client over ``plugin_api.router`` with ``CONTRACT_CFG`` loaded."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from hermes_otel.dashboard import plugin_api

    monkeypatch.setattr(
        backends, "load_config", lambda: (Path("/x/hermes_otel.yaml"), CONTRACT_CFG, None)
    )
    app = FastAPI()
    app.include_router(plugin_api.router)
    with TestClient(app) as c:
        yield c


def down(*_a, **_k):
    """An HTTP function whose backend is unreachable."""
    raise BackendError(502, "Backend unreachable: [Errno 61] Connection refused")


def row(trace_id: str, start_ns: int) -> Dict[str, Any]:
    """A Tempo-shaped search row with only the fields paging looks at."""
    return {"traceID": trace_id, "startTimeUnixNano": str(start_ns), "spanSets": []}


def http_error(code: int, body: str = "") -> _urlerror.HTTPError:
    return _urlerror.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


# ── a config every adapter accepts ─────────────────────────────────────

GENERIC_CFG: Dict[str, Any] = {
    "endpoint": "http://localhost:4318/v1/traces",
    "api_key": "k",
    "public_key": "pk",
    "secret_key": "sk",
    "user": "u",
    "password": "p",
    "user_token": "t",
    "dsn": "http://t@localhost:14318/1",
}


def generic_adapter(cls: Any):
    """``cls`` built from ``GENERIC_CFG`` under its first handled type."""
    backend_type = sorted(cls.handles)[0]
    return cls({"type": backend_type, **GENERIC_CFG})


Fake = Callable[..., Any]
