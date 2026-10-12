"""Live-store rows in the shape ``LiveStore.add_span`` / ``add_metric`` / ``add_log``
take, and a per-test ``store``."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import pytest

from hermes_otel.live_store import LiveStore

NOW = time.time_ns()
S = 1_000_000_000


def span(
    trace: str,
    name: str,
    sid: str,
    parent: Optional[str],
    attrs: Optional[Dict[str, Any]] = None,
    start: Optional[int] = None,
    end: Optional[int] = None,
    status: str = "OK",
) -> Dict[str, Any]:
    """One finished span as the live store serialises it (2 s ago → 1 s ago by default)."""
    start = NOW - 2 * S if start is None else start
    end = NOW - 1 * S if end is None else end
    return {
        "trace_id": trace,
        "span_id": sid,
        "parent_span_id": parent,
        "name": name,
        "start_time_unix_nano": start,
        "end_time_unix_nano": end,
        "duration_ms": (end - start) / 1e6,
        "status": status,
        "attributes": attrs or {},
    }


def metric(
    name: str, value: float, attrs: Optional[Dict[str, Any]] = None, ts: Optional[int] = None
):
    return name, value, attrs or {}, NOW if ts is None else ts


def log(
    body: str, level: str = "INFO", attrs: Optional[Dict[str, Any]] = None, **extra
) -> Dict[str, Any]:
    return {"body": body, "severity_text": level, "attributes": attrs or {}, "ts": NOW, **extra}


@pytest.fixture()
def store(tmp_path):
    s = LiveStore(db_path=str(tmp_path / "live.db"))
    yield s
    s.close()
