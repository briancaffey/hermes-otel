"""The live-store routes of the dashboard tab (``/live/*``, #184).

The gateway writes turns into a SQLite file the dashboard opens in its own
process; these routes read it. ``plugin_api`` includes this router, so the
URLs are unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from hermes_otel.dashboard.backends.paging import LOG_PAGE_SLACK, log_page
from hermes_otel.dashboard.route_helpers import check_bucket_count, plugin_module, window_ns

router = APIRouter()


def _get_live_store():
    """Return the LiveStore for the request's Hermes home, or None.

    The dashboard runs in a SEPARATE process from the gateway: it opens the
    shared SQLite file itself (``create=True``) and reads what the gateway
    writes. The store follows the profile of the request (#70): a dashboard
    serving several profiles reads each one's own file. ``None`` when the
    plugin's live store (``hermes_otel.live_store_registry``) cannot be
    imported or the store cannot be created (:func:`_no_store_reason` says which).
    """
    mod = plugin_module("live_store_registry")
    if mod is None:
        return None
    return mod.get_live_store_for_home(create=True)


def _no_store_reason() -> Dict[str, Any]:
    """Why :func:`_get_live_store` returned ``None``: the module did not
    import, or it did and creating the store raised (#616)."""
    mod = plugin_module("live_store_registry")
    if mod is None:
        return {
            "reason": "the hermes_otel.live_store_registry module could not be imported by the dashboard"
        }
    failure = mod.create_error_for_home()
    if failure is None:
        return {"reason": "the live store could not be created"}
    path, error = failure
    return {"reason": f"the live store {path} could not be created: {error}", "path": path}


@router.get("/live/status")
def live_status() -> Dict[str, Any]:
    """Whether the live store can be read, and its current fill.

    ``live: false`` names the real cause: the ``hermes_otel.live_store_registry``
    module could not be imported, creating the store raised, or the file could
    not be opened or initialised (``open_error``). An empty but healthy store is ``live: true`` with zero
    rows; ``dashboard_live: false`` on the gateway shows up as a store that
    never fills, which the Settings tab reports.
    """
    store = _get_live_store()
    if store is None:
        return {"live": False, **_no_store_reason()}
    stats = store.stats()
    if stats.get("open_error"):
        return {
            "live": False,
            "reason": f"live store {store.db_path} could not be opened: {stats['open_error']}",
            "path": store.db_path,
            **stats,
        }
    return {"live": True, "path": store.db_path, **stats}


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
    min_duration_ms: Optional[int] = Query(None, ge=0),
    model: str = Query("", description="substring of the model name"),
    tool: str = Query("", description="tool name (tool.<name> spans)"),
    before_ns: int = Query(
        0, ge=0, description="Keyset cursor: only traces that started strictly before this"
    ),
) -> Dict[str, Any]:
    """One row per trace, newest start first. Page with ``before_ns`` =
    the previous page's ``next_before_ns`` (``offset`` still works)."""
    store = _get_live_store()
    if store is None:
        return {"live": False, "traces": [], "total": 0, "has_more": False, "next_before_ns": None}
    start_ns, end_ns = window_ns(lookback_hours, start_s, end_s)
    out = store.query_traces(
        min_duration_ms=min_duration_ms or None,
        model=model.strip() or None,
        tool=tool.strip() or None,
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
        before_ns=before_ns or None,
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
    start_ns, end_ns = window_ns(lookback_hours, None, None)
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
    start_ns, end_ns = window_ns(lookback_hours, start_s, end_s)
    check_bucket_count(start_ns, end_ns, bucket_s)
    store = _get_live_store()
    if store is None:
        return {"live": False, "buckets": [], "series": {}}
    return {
        "live": True,
        **store.metric_buckets(
            name, start_ns, end_ns, bucket_s, group_by=group_by.strip() or None, agg=agg
        ),
    }


@router.get("/live/logs/search")
def live_logs_search(
    trace_id: str = Query(""),
    span_id: str = Query("", description="Only lines logged while this span was current"),
    session: str = Query(""),
    min_level: int = Query(0, ge=0, le=50),
    logger: str = Query(""),
    text: str = Query(""),
    lookback_hours: float = Query(1.0, gt=0, le=8760),
    limit: int = Query(300, ge=1, le=2000),
    before_ns: int = Query(0, ge=0),
    events_only: bool = Query(False, description="Only rows with an event_name (logs.events)"),
    event_name: str = Query("", description="Exactly this event (implies events_only)"),
    start_s: Optional[int] = Query(
        None, ge=0, description="Window start (unix s); overrides lookback"
    ),
    end_s: Optional[int] = Query(None, ge=0, description="Window end (unix s); default now"),
) -> Dict[str, Any]:
    store = _get_live_store()
    if store is None:
        return {"live": False, "logs": [], "next_before_ns": None, "has_more": False}
    start_ns, end_ns = window_ns(lookback_hours, start_s, end_s)
    rows = store.query_logs(
        trace_id=trace_id.strip() or None,
        span_id=span_id.strip() or None,
        session=session.strip() or None,
        level_min=min_level or None,
        logger=logger.strip() or None,
        text=text.strip() or None,
        start_ns=start_ns,
        end_ns=end_ns,
        limit=limit + LOG_PAGE_SLACK,
        before_ns=before_ns or None,
        events_only=events_only,
        event_name=event_name.strip() or None,
    )
    return {"live": True, **log_page(rows, limit)}


@router.get("/live/loggers")
def live_loggers() -> Dict[str, Any]:
    store = _get_live_store()
    if store is None:
        return {"live": False, "loggers": []}
    return {"live": True, "loggers": store.loggers()}
