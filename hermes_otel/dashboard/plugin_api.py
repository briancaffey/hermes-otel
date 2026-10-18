"""FastAPI routes for the hermes-otel dashboard tab.

Mounted at ``/api/plugins/hermes_otel/*`` by the Hermes dashboard.
Thin router — all backend-specific logic lives in the sibling
``backends`` package, and the live-store routes in ``live_api``.
"""

from __future__ import annotations

import functools
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib import parse as _urlparse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

# The Hermes plugin loader imports this file via
# ``importlib.util.spec_from_file_location`` as a top-level module, so a
# relative import of the sibling ``backends`` package is not possible. Import
# it through the plugin package (``hermes_otel.dashboard.backends``) instead of
# putting this directory on sys.path, which would register a global top-level
# ``backends`` module in the long-lived dashboard process and collide with any
# other plugin or package of that name.
_HERE = Path(__file__).resolve().parent


def _ensure_plugin_package_importable() -> None:
    """Make ``hermes_otel`` importable, preferring the package THIS file lives in.

    The plugins directory goes to the front of ``sys.path`` before the first
    import, so a stale copy in site-packages (or a sibling checkout) cannot
    win over the package the API file ships with (#290). A package that is
    already imported is left alone: re-importing would give the router a
    second copy of the live-store singleton.
    """
    if "hermes_otel" in sys.modules:
        return
    pkg_parent = str(_HERE.parent.parent)
    if (_HERE.parent.parent / "hermes_otel" / "__init__.py").exists():
        if pkg_parent in sys.path:
            sys.path.remove(pkg_parent)
        sys.path.insert(0, pkg_parent)
    try:
        import hermes_otel  # noqa: F401
    except ImportError:
        pass


_ensure_plugin_package_importable()

from hermes_otel.dashboard.backends import (  # noqa: E402  (after the import check above)
    _instantiate,
    adapters,
    backend_label,
    candidate_config_paths,
    resolve_adapter,
)
from hermes_otel.dashboard.backends.base import (  # noqa: E402
    BackendError,
    LogFilter,
    StructuredFilter,
    error_kind,
    split_applied_filters,
    validate_label_name,
    validate_metric_name,
)
from hermes_otel.dashboard.backends.metrics_math import otlp_metric_name  # noqa: E402
from hermes_otel.dashboard.backends.paging import LOG_PAGE_SLACK, log_page  # noqa: E402
from hermes_otel.dashboard.live_api import router as _live_router  # noqa: E402
from hermes_otel.dashboard.route_helpers import (  # noqa: E402
    check_bucket_count,
    plugin_module,
    window_s,
)

router = APIRouter()
# The live-store routes (``/live/*``) live in ``live_api``; same URLs.
router.include_router(_live_router)


# ── Backend routes: one error shape (#290) ───────────────────────────────
#
# Every route that talks to a backend answers a failure with
# ``{"detail": <message>, "kind": <kind>}`` and the status the kind implies:
# ``not_found`` 404 (only where a thing can be missing: a trace), ``auth``
# and ``config`` 503, ``request`` 400, ``backend`` 502. A parsing error inside
# an adapter is a 502 too — never a 500 with a stack trace — and the trace
# goes to the plugin's debug log.

_KIND_STATUS = {"not_found": 404, "auth": 503, "config": 503, "request": 400, "backend": 502}


def _error_response(detail: str, kind: str, status: Optional[int] = None) -> JSONResponse:
    return JSONResponse(
        {"detail": detail, "kind": kind}, status_code=status or _KIND_STATUS.get(kind, 502)
    )


def _debug(msg: str) -> None:
    mod = plugin_module("debug_utils")
    if mod is not None:
        try:
            mod.debug_log(msg)
        except Exception:
            pass


def _backend_route(fn: Optional[Callable] = None, *, not_found_ok: bool = False) -> Callable:
    """Wrap a route so adapter failures become the shared error payload.

    A ``not_found`` from a backend means "this trace does not exist" only on
    the trace-detail route; anywhere else a 404 is a wrong URL (a Jaeger v2 UI
    port, a renamed Uptrace route) and is reported as a backend failure.
    """

    def decorate(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return f(*args, **kwargs)
            except HTTPException as exc:
                kind = error_kind(exc)
                detail = str(exc.detail)
                if kind == "not_found" and not not_found_ok:
                    return _error_response(detail, "backend", 502)
                status = exc.status_code if kind not in _KIND_STATUS else _KIND_STATUS[kind]
                if kind == "request":
                    status = exc.status_code or 400
                return _error_response(detail, kind, status)
            except Exception as exc:  # an adapter tripped over the backend's answer
                _debug(f"[dashboard] {f.__name__} failed: {traceback.format_exc()}")
                return _error_response(
                    f"The adapter could not read the backend's answer: {type(exc).__name__}: {exc}",
                    "backend",
                    502,
                )

        return wrapper

    return decorate(fn) if fn is not None else decorate


def _resolve(backend: str):
    """``resolve_adapter`` for a request's ``backend=``; a name that is not
    configured is a 400 listing the configured ones (#177)."""
    try:
        return resolve_adapter(backend.strip() or None)
    except KeyError as e:
        raise BackendError(400, f"Unknown backend {backend!r}; configured: {e.args[0]}", "request")


def _adapter_for(backend: str, need: str = "traces"):
    """The adapter a request asked for (``backend=``), or the default one.

    400 for a name that is not configured, 503 when nothing queryable is
    configured or the chosen backend lacks the capability (#177, #182), 503
    ``config`` when the entry cannot be set up (a bad ``project_id``).
    """
    adapter, backends, _, _ = _resolve(backend)
    if adapter is None:
        if backend.strip():
            raise BackendError(
                503, f"Backend {backend!r} has no dashboard adapter for its type", "config"
            )
        raise BackendError(503, "No trace backend configured", "config")
    if need == "metrics" and not adapter.supports_metrics:
        raise BackendError(
            503, f"Backend {backend_label(adapter.cfg)!r} does not serve metrics", "config"
        )
    if need == "logs" and not adapter.supports_logs:
        raise BackendError(
            503, f"Backend {backend_label(adapter.cfg)!r} does not serve logs", "config"
        )
    return adapter


def _public_endpoint(endpoint: Any) -> Any:
    """An endpoint URL without its userinfo, for ``/status`` (#290)."""
    if not isinstance(endpoint, str) or "@" not in endpoint:
        return endpoint
    try:
        parts = _urlparse.urlsplit(endpoint)
    except ValueError:
        return endpoint
    if not parts.username and not parts.password:
        return endpoint
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return _urlparse.urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


@router.get("/settings")
def settings(
    reveal: bool = Query(False, description="Show credential values instead of masking them"),
) -> Dict[str, Any]:
    """Every plugin setting with its value, default, source and description,
    the config file (raw and as an effective YAML), and the environment
    variables the plugin honours. See ``hermes_otel.settings_report``.
    """
    mod = plugin_module("settings_report")
    if mod is None:
        raise HTTPException(status_code=503, detail="hermes_otel package not importable")
    return mod.build_settings_report(reveal=reveal)


@router.get("/status")
@_backend_route
def status(
    backend: str = Query("", description="Backend name or type to report on"),
) -> Dict[str, Any]:
    """Report the active query backend + every configured backend.

    ``available`` lists every entry with its capabilities so the UI builds
    its source selector from the server's view, not from the yaml (#177),
    including which search-bar fields the adapter honours (``filters``, #292).
    Each row and the top level read the same ``BackendAdapter.status()``, so
    the active backend's row and the top-level ``filters`` agree (#519).
    """
    adapter, backends, cfg_path, pin = _resolve(backend)
    queryable_types = sorted({t for cls in adapters() for t in cls.handles})

    def _caps(b: Dict[str, Any]) -> Dict[str, Any]:
        # What THIS entry serves comes from its (cached) adapter instance: a
        # ``tempo`` entry grows metrics and logs when it names a Prometheus
        # and a Loki, which the class alone cannot know.
        inst = _instantiate(b, cfg_path)
        st = inst.status() if inst is not None else {"metrics": False, "logs": False}
        row = {
            "type": b.get("type"),
            "name": backend_label(b),
            "endpoint": _public_endpoint(b.get("endpoint")),
            "supported": inst is not None,
            "metrics": bool(st["metrics"]),
            "logs": bool(st["logs"]),
        }
        if inst is not None:
            row["filters"] = st["filters"]
        return row

    backend_list = [_caps(b) for b in backends]
    out: Dict[str, Any] = {
        "configured": adapter is not None,
        "backends": backend_list,
        "available": backend_list,
        "active": backend_label(adapter.cfg) if adapter is not None else None,
        "queryable_types": queryable_types,
        "config_path": str(cfg_path) if cfg_path else None,
        "query_backend_pin": pin,
    }
    if adapter is not None:
        return {**out, **adapter.status()}
    if cfg_path is None:
        looked = ", ".join(str(p) for p in candidate_config_paths())
        out["reason"] = f"No hermes_otel.yaml found. Looked at: {looked}"
    elif not backends:
        out["reason"] = (
            f"No backends: configured in {cfg_path}. Add at least one "
            "backend entry under ``backends:``."
        )
    else:
        out["reason"] = (
            f"No queryable backend configured in {cfg_path}. Supported "
            f"types: {', '.join(queryable_types)}."
        )
    return out


def _parse_filter(
    q: str,
    service: str,
    name_regex: str,
    min_duration_ms: Optional[int],
    status_in: str,
    free_text: str,
    roots_only: bool,
    model: str = "",
    session: str = "",
    tool: str = "",
    name_prefix: str = "",
    before_ns: int = 0,
) -> StructuredFilter:
    # Attribute equality the search bar offers (#183); adapters translate the
    # keys they know (Phoenix filterCondition, OpenObserve columns, TraceQL,
    # Langfuse's sessionId) and declare the rest in ``filter_support``.
    attr_equals: Dict[str, str] = {}
    if model.strip():
        attr_equals["llm.model_name"] = model.strip()
    if session.strip():
        attr_equals["hermes.session_id"] = session.strip()
    if tool.strip():
        attr_equals["tool.name"] = tool.strip()
    return StructuredFilter(
        service=service.strip() or None,
        name_regex=name_regex.strip() or None,
        name_prefix=name_prefix.strip() or None,
        attr_equals=attr_equals,
        min_duration_ms=min_duration_ms if (min_duration_ms and min_duration_ms > 0) else None,
        status=status_in.strip().lower() or None,
        free_text=free_text.strip() or None,
        raw=q.strip() or None,
        roots_only=roots_only,
        before_ns=int(before_ns) or None,
    )


@router.get("/traces/search")
@_backend_route
def search_traces(
    limit: int = Query(50, ge=1, le=200),
    lookback_hours: float = Query(1.0, gt=0, le=8760),
    start_s: Optional[int] = Query(None, ge=0, description="Window start (unix s)"),
    end_s: Optional[int] = Query(None, ge=0, description="Window end (unix s); default now"),
    q: str = Query("", description="Backend-native raw query"),
    service: str = Query(""),
    name_regex: str = Query(
        "", description="Native span-name match (regex where the backend has one)"
    ),
    name_prefix: str = Query(
        "", description="Span-name prefix, the kind filter (tool., llm., agent)"
    ),
    min_duration_ms: Optional[int] = Query(None, ge=0),
    status: str = Query("", description="'ok' or 'error'"),
    free_text: str = Query(""),
    roots_only: bool = Query(True, description="Restrict matches to root spans"),
    backend: str = Query(
        "", description="Configured backend name or type (default: the pinned/first one)"
    ),
    model: str = Query("", description="exact model name (llm.model_name)"),
    session: str = Query("", description="exact session id (hermes.session_id)"),
    tool: str = Query("", description="exact tool name (tool.name); implies roots_only=false"),
    before_ns: int = Query(
        0, ge=0, description="Keyset cursor: only traces that started before this instant (unix ns)"
    ),
) -> Dict[str, Any]:
    adapter = _adapter_for(backend)
    start, end = window_s(lookback_hours, start_s, end_s)
    # A tool filter matches tool spans, which are never roots.
    f = _parse_filter(
        q,
        service,
        name_regex,
        min_duration_ms,
        status,
        free_text,
        roots_only and not tool.strip(),
        model,
        session,
        tool,
        name_prefix,
        before_ns,
    )
    out = adapter.search(f, start, end, limit)
    if not isinstance(out, dict):
        out = {"traces": []}
    out.setdefault("traces", [])
    out.setdefault("has_more", False)
    out.setdefault("next_before_ns", None)
    applied, ignored = split_applied_filters(adapter, f)
    out["applied_filters"] = applied
    out["ignored_filters"] = ignored
    out["backend"] = backend_label(adapter.cfg)
    return out


@router.get("/traces/{trace_id}")
@_backend_route(not_found_ok=True)
def get_trace(trace_id: str, backend: str = Query("")) -> Dict[str, Any]:
    if not trace_id or not trace_id.replace("-", "").isalnum():
        raise BackendError(400, "Invalid trace id", "request")
    adapter = _adapter_for(backend)
    out = adapter.get_trace(trace_id)
    if not isinstance(out, dict):
        out = {"batches": []}
    out.setdefault("truncated", False)
    try:
        ui_url = adapter.trace_url(trace_id)
    except Exception:
        ui_url = None
    if ui_url:
        out["ui_url"] = ui_url
    out["backend"] = backend_label(adapter.cfg)
    return out


# ── backend metrics and logs (#182): same shapes as the live endpoints ───


@router.get("/metrics/names")
@_backend_route
def backend_metric_names(
    backend: str = Query(""),
    lookback_hours: float = Query(1.0, gt=0, le=8760),
    start_s: Optional[int] = Query(None, ge=0),
    end_s: Optional[int] = Query(None, ge=0),
) -> Dict[str, Any]:
    adapter = _adapter_for(backend, "metrics")
    start, end = window_s(lookback_hours, start_s, end_s)
    names = []
    for row in adapter.metric_names(start, end) or []:
        if isinstance(row, dict) and row.get("name"):
            # The plugin's dotted instrument name next to the native spelling,
            # so the UI can match its panels on every source (#284).
            names.append({**row, "otlp_name": otlp_metric_name(str(row["name"]))})
    return {"backend": backend_label(adapter.cfg), "names": names}


@router.get("/metrics/query")
@_backend_route
def backend_metrics_query(
    name: str = Query(..., min_length=1),
    backend: str = Query(""),
    group_by: str = Query(""),
    agg: str = Query("sum", pattern="^(sum|count|avg|max|last)$"),
    lookback_hours: float = Query(1.0, gt=0, le=8760),
    start_s: Optional[int] = Query(None, ge=0),
    end_s: Optional[int] = Query(None, ge=0),
    bucket_s: int = Query(15, ge=1, le=86400),
) -> Dict[str, Any]:
    adapter = _adapter_for(backend, "metrics")
    start, end = window_s(lookback_hours, start_s, end_s)
    check_bucket_count(start * 1_000_000_000, end * 1_000_000_000, bucket_s)
    # Both names are spliced into PromQL / MQL / SQL by the adapters.
    validate_metric_name(name)
    label = validate_label_name(group_by.strip() or None)
    out = adapter.metrics_query(name, start, end, bucket_s, group_by=label, agg=agg)
    return {"backend": backend_label(adapter.cfg), "otlp_name": otlp_metric_name(name), **out}


@router.get("/logs/search")
@_backend_route
def backend_logs_search(
    backend: str = Query(""),
    trace_id: str = Query(""),
    span_id: str = Query(""),
    session: str = Query(""),
    min_level: int = Query(0, ge=0, le=50),
    logger: str = Query(""),
    text: str = Query(""),
    lookback_hours: float = Query(1.0, gt=0, le=8760),
    limit: int = Query(300, ge=1, le=2000),
    before_ns: int = Query(0, ge=0),
    events_only: bool = Query(False),
    event_name: str = Query(""),
    start_s: Optional[int] = Query(None, ge=0),
    end_s: Optional[int] = Query(None, ge=0),
) -> Dict[str, Any]:
    adapter = _adapter_for(backend, "logs")
    start, end = window_s(lookback_hours, start_s, end_s)
    f = LogFilter(
        trace_id=trace_id.strip() or None,
        span_id=span_id.strip() or None,
        session=session.strip() or None,
        min_level=min_level,
        logger=logger.strip() or None,
        text=text.strip() or None,
        before_ns=before_ns or None,
        events_only=events_only,
        event_name=event_name.strip() or None,
    )
    rows = adapter.logs_search(f, start, end, limit + LOG_PAGE_SLACK)
    return {"backend": backend_label(adapter.cfg), **log_page(rows, limit)}


@router.get("/loggers")
@_backend_route
def backend_loggers(
    backend: str = Query(""),
    lookback_hours: float = Query(24.0, gt=0, le=8760),
    start_s: Optional[int] = Query(None, ge=0),
    end_s: Optional[int] = Query(None, ge=0),
) -> Dict[str, Any]:
    adapter = _adapter_for(backend, "logs")
    start, end = window_s(lookback_hours, start_s, end_s)
    return {"backend": backend_label(adapter.cfg), "loggers": adapter.loggers(start, end)}
