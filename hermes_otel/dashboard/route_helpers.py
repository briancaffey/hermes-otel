"""Helpers shared by the dashboard's route modules (``plugin_api`` for the
backend routes, ``live_api`` for the live store): plugin-module import,
query windows and the bucket-count guard (#403)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from fastapi import HTTPException

_HERE = Path(__file__).resolve().parent


def plugin_module(name: str):
    """Import ``hermes_otel.<name>``, adding the plugins dir to sys.path if needed.

    The Hermes plugin loader imports this API file by path, so ``hermes_otel``
    may not be importable yet; the package's parent (…/plugins) is added
    defensively. Returns None when the import fails either way.
    """
    import importlib

    try:
        return importlib.import_module(f"hermes_otel.{name}")
    except Exception:
        pkg_parent = _HERE.parent.parent
        if str(pkg_parent) not in sys.path:
            sys.path.insert(0, str(pkg_parent))
        try:
            return importlib.import_module(f"hermes_otel.{name}")
        except Exception:
            return None


# At most this many buckets per metrics query: ``bucket_s=1`` over a year
# would otherwise allocate 31.5 M buckets per series (#290).
MAX_BUCKETS = 5000


def window_ns(
    lookback_hours: float, start_s: Optional[int], end_s: Optional[int]
) -> Tuple[int, int]:
    """``(start_ns, end_ns)``: an absolute window when given, else the
    lookback ending now; 422 when the start is after the end."""
    # The default window ends now, to the nanosecond: a row written a fraction
    # of a second ago must not fall outside a window truncated to the second.
    # ``0`` is the epoch, not "unset" (#290): only ``None`` falls back.
    end_ns = int(end_s) * 1_000_000_000 if end_s is not None else time.time_ns()
    start_ns = (
        int(start_s) * 1_000_000_000
        if start_s is not None
        else end_ns - int(lookback_hours * 3600) * 1_000_000_000
    )
    if start_ns > end_ns:
        raise HTTPException(status_code=422, detail="start_s must not be after end_s")
    return start_ns, end_ns


def check_bucket_count(start_ns: int, end_ns: int, bucket_s: int) -> None:
    """422 when a window / bucket pair would produce more than ``MAX_BUCKETS``
    buckets (``bucket_s=1`` over a year is 31.5 M per series, #290). Shared by
    the live and the backend metric routes."""
    n = (int(end_ns) - int(start_ns)) // (max(1, int(bucket_s)) * 1_000_000_000) + 1
    if n > MAX_BUCKETS:
        raise HTTPException(
            status_code=422,
            detail=f"{n} buckets requested; at most {MAX_BUCKETS} (widen bucket_s or narrow the window)",
        )


def window_s(
    lookback_hours: float, start_s: Optional[int], end_s: Optional[int]
) -> Tuple[int, int]:
    """:func:`window_ns` in whole seconds, for the backend routes."""
    start_ns, end_ns = window_ns(lookback_hours, start_s, end_s)
    return start_ns // 1_000_000_000, end_ns // 1_000_000_000
