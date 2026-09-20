"""FastAPI routes for the hermes-otel dashboard tab.

Mounted at ``/api/plugins/hermes_otel/*`` by the Hermes dashboard.
Thin router — all backend-specific logic lives in the sibling
``backends`` package.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

# The Hermes plugin loader imports this file via
# ``importlib.util.spec_from_file_location``, which does NOT put the
# file's directory on sys.path. To let this module import from a
# sibling ``backends`` package we add our directory explicitly. One-
# time, idempotent.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from backends import (  # noqa: E402  (after the path shim above)
    adapters,
    candidate_config_paths,
    find_adapter_class,
    resolve_adapter,
)
from backends.base import StructuredFilter  # noqa: E402

router = APIRouter()


def _get_live_store():
    """Return the in-process LiveStore the tracer feeds, or None.

    Must import the SAME ``hermes_otel.live_store`` module the tracer uses so
    the singleton is shared (the dashboard runs in the same process). The
    plugin package's parent is added to sys.path defensively in case this API
    module was loaded before ``hermes_otel`` was importable.
    """
    try:
        from hermes_otel.live_store import get_live_store
    except Exception:
        pkg_parent = _HERE.parent.parent  # …/plugins  (so `hermes_otel` resolves)
        if str(pkg_parent) not in sys.path:
            sys.path.insert(0, str(pkg_parent))
        try:
            from hermes_otel.live_store import get_live_store
        except Exception:
            return None
    # create=True: the dashboard runs in a SEPARATE process from the gateway, so
    # it opens the shared SQLite store itself (reading what the gateway writes).
    return get_live_store(create=True)


@router.get("/live/status")
def live_status() -> Dict[str, Any]:
    """Whether the in-process live store is active, and its current fill."""
    store = _get_live_store()
    if store is None:
        return {
            "live": False,
            "reason": "live store unavailable (dashboard_live off / no telemetry yet)",
        }
    return {"live": True, **store.stats()}


@router.get("/live/spans")
def live_spans(
    since: int = Query(0, ge=0, description="Return items with seq > since"),
    limit: int = Query(500, ge=1, le=5000),
) -> Dict[str, Any]:
    store = _get_live_store()
    if store is None:
        return {"live": False, "spans": [], "cursor": 0}
    return {"live": True, "spans": store.spans(since=since, limit=limit), "cursor": store.cursor()}


@router.get("/live/metrics")
def live_metrics(
    since: int = Query(0, ge=0),
    limit: int = Query(2000, ge=1, le=10000),
) -> Dict[str, Any]:
    store = _get_live_store()
    if store is None:
        return {"live": False, "metrics": [], "cursor": 0}
    return {
        "live": True,
        "metrics": store.metrics(since=since, limit=limit),
        "cursor": store.cursor(),
    }


@router.get("/live/logs")
def live_logs(
    since: int = Query(0, ge=0),
    limit: int = Query(2000, ge=1, le=10000),
) -> Dict[str, Any]:
    store = _get_live_store()
    if store is None:
        return {"live": False, "logs": [], "cursor": 0}
    return {"live": True, "logs": store.logs(since=since, limit=limit), "cursor": store.cursor()}


# ── live store: server-side queries (#184) ───────────────────────────────
#
# The cursor endpoints above ship raw rows for the streaming views. These
# filter, group and bucket in SQLite so the browser gets one page of results
# (traces, sessions, buckets, log lines) instead of the whole buffer.


def _window(
    lookback_hours: float, start_s: Optional[int], end_s: Optional[int]
) -> "tuple[int, int]":
    end = int(end_s) if end_s else int(time.time())
    start = int(start_s) if start_s else end - int(lookback_hours * 3600)
    return start * 1_000_000_000, end * 1_000_000_000


@router.get("/live/traces")
def live_traces(
    lookback_hours: float = Query(1.0, gt=0, le=8760),
    start_s: Optional[int] = Query(None, ge=0),
    end_s: Optional[int] = Query(None, ge=0),
    session: str = Query(""),
    status: str = Query("", description="'ok' or 'error'"),
    name: str = Query("", description="substring of a span name"),
    kind: str = Query("", description="agent, cron, tool, llm, api, subagent, approval"),
    text: str = Query("", description="substring anywhere in a span's attributes"),
    trace_id: str = Query(""),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    store = _get_live_store()
    if store is None:
        return {"live": False, "traces": [], "total": 0}
    start_ns, end_ns = _window(lookback_hours, start_s, end_s)
    out = store.query_traces(
        start_ns=start_ns,
        end_ns=end_ns,
        session=session.strip() or None,
        status=status.strip().lower() or None,
        name=name.strip() or None,
        kind=kind.strip().lower() or None,
        text=text.strip() or None,
        trace_id=trace_id.strip() or None,
        limit=limit,
        offset=offset,
    )
    return {"live": True, **out}


@router.get("/live/traces/{trace_id}")
def live_trace(trace_id: str) -> Dict[str, Any]:
    if not trace_id or not trace_id.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid trace id")
    store = _get_live_store()
    if store is None:
        return {"live": False, "trace": None, "spans": []}
    out = store.trace(trace_id)
    if out["trace"] is None:
        raise HTTPException(status_code=404, detail="Trace not in the live store")
    return {"live": True, **out}


@router.get("/live/sessions")
def live_sessions(
    lookback_hours: float = Query(24.0, gt=0, le=8760),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    store = _get_live_store()
    if store is None:
        return {"live": False, "sessions": []}
    start_ns, end_ns = _window(lookback_hours, None, None)
    return {"live": True, **store.sessions(start_ns=start_ns, end_ns=end_ns, limit=limit)}


@router.get("/live/metrics/names")
def live_metric_names() -> Dict[str, Any]:
    store = _get_live_store()
    if store is None:
        return {"live": False, "names": []}
    return {"live": True, "names": store.metric_names()}


@router.get("/live/metrics/query")
def live_metrics_query(
    name: str = Query(..., min_length=1),
    group_by: str = Query("", description="attribute to split series by"),
    agg: str = Query("sum", pattern="^(sum|count|avg|max|last)$"),
    lookback_hours: float = Query(1.0, gt=0, le=8760),
    start_s: Optional[int] = Query(None, ge=0),
    end_s: Optional[int] = Query(None, ge=0),
    bucket_s: int = Query(15, ge=1, le=86400),
) -> Dict[str, Any]:
    store = _get_live_store()
    if store is None:
        return {"live": False, "buckets": [], "series": {}}
    start_ns, end_ns = _window(lookback_hours, start_s, end_s)
    return {
        "live": True,
        **store.metric_buckets(
            name, start_ns, end_ns, bucket_s, group_by=group_by.strip() or None, agg=agg
        ),
    }


@router.get("/live/logs/search")
def live_logs_search(
    trace_id: str = Query(""),
    session: str = Query(""),
    min_level: int = Query(0, ge=0, le=50),
    logger: str = Query(""),
    text: str = Query(""),
    lookback_hours: float = Query(1.0, gt=0, le=8760),
    limit: int = Query(300, ge=1, le=2000),
) -> Dict[str, Any]:
    store = _get_live_store()
    if store is None:
        return {"live": False, "logs": []}
    start_ns, end_ns = _window(lookback_hours, None, None)
    return {
        "live": True,
        "logs": store.query_logs(
            trace_id=trace_id.strip() or None,
            session=session.strip() or None,
            level_min=min_level or None,
            logger=logger.strip() or None,
            text=text.strip() or None,
            start_ns=start_ns,
            end_ns=end_ns,
            limit=limit,
        ),
    }


@router.get("/live/loggers")
def live_loggers() -> Dict[str, Any]:
    store = _get_live_store()
    if store is None:
        return {"live": False, "loggers": []}
    return {"live": True, "loggers": store.loggers()}


@router.get("/status")
def status() -> Dict[str, Any]:
    """Report the active query backend + every configured backend."""
    adapter, backends, cfg_path, pin = resolve_adapter()
    queryable_types = sorted({t for cls in adapters() for t in cls.handles})

    backend_list = [
        {
            "type": b.get("type"),
            "name": b.get("name") or b.get("type"),
            "endpoint": b.get("endpoint"),
            "supported": find_adapter_class(b.get("type", "")) is not None,
        }
        for b in backends
    ]

    if adapter is None:
        if cfg_path is None:
            reason = "No config.yaml found. Looked at: " + ", ".join(
                str(p) for p in candidate_config_paths()
            )
        elif not backends:
            reason = (
                f"No backends: configured in {cfg_path}. Add at least one "
                "backend entry under ``backends:``."
            )
        else:
            reason = (
                f"No queryable backend configured in {cfg_path}. Supported "
                f"types: {', '.join(queryable_types)}."
            )
        return {
            "configured": False,
            "reason": reason,
            "backends": backend_list,
            "queryable_types": queryable_types,
            "config_path": str(cfg_path) if cfg_path else None,
            "query_backend_pin": pin,
        }

    st = adapter.status()
    return {
        "configured": True,
        "backends": backend_list,
        "queryable_types": queryable_types,
        "config_path": str(cfg_path) if cfg_path else None,
        "query_backend_pin": pin,
        **st,
    }


def _parse_filter(
    q: str,
    service: str,
    name_regex: str,
    min_duration_ms: Optional[int],
    status_in: str,
    free_text: str,
    roots_only: bool,
) -> StructuredFilter:
    return StructuredFilter(
        service=service.strip() or None,
        name_regex=name_regex.strip() or None,
        min_duration_ms=min_duration_ms if (min_duration_ms and min_duration_ms > 0) else None,
        status=status_in.strip().lower() or None,
        free_text=free_text.strip() or None,
        raw=q.strip() or None,
        roots_only=roots_only,
    )


@router.get("/traces/search")
def search_traces(
    limit: int = Query(50, ge=1, le=200),
    lookback_hours: float = Query(1.0, gt=0, le=8760),
    q: str = Query("", description="Backend-native raw query"),
    service: str = Query(""),
    name_regex: str = Query(""),
    min_duration_ms: Optional[int] = Query(None, ge=0),
    status: str = Query("", description="'ok' or 'error'"),
    free_text: str = Query(""),
    roots_only: bool = Query(True, description="Restrict matches to root spans"),
) -> Dict[str, Any]:
    adapter, _, _, _ = resolve_adapter()
    if adapter is None:
        raise HTTPException(status_code=503, detail="No trace backend configured")

    end_s = int(time.time())
    start_s = end_s - int(lookback_hours * 3600)
    f = _parse_filter(q, service, name_regex, min_duration_ms, status, free_text, roots_only)
    return adapter.search(f, start_s, end_s, limit)


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str) -> Dict[str, Any]:
    if not trace_id or not trace_id.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid trace id")
    adapter, _, _, _ = resolve_adapter()
    if adapter is None:
        raise HTTPException(status_code=503, detail="No trace backend configured")
    return adapter.get_trace(trace_id)
