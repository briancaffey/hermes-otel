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
    lookback_hours: float = Query(1.0, gt=0, le=168),
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
