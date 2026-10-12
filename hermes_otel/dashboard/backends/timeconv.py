"""Timestamp conversions shared by the adapters (#403): whatever a backend
returns (ns, us, ms, s, ISO-8601) → unix nanoseconds."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


def ns_from_any(value: Any) -> Optional[int]:
    """Best-effort parse of a time value into unix-nanoseconds.

    Accepts int/str nanoseconds, ISO-8601 strings, and float seconds.
    Returns None if nothing works so adapters can skip the field.
    """
    if value is None:
        return None
    # Raw int or numeric string — treat as ns (Tempo/Jaeger) or ms (some
    # backends) based on magnitude.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _scale_to_ns(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Numeric string?
        try:
            return _scale_to_ns(float(s))
        except ValueError:
            pass
        # ISO-8601.
        try:
            from datetime import datetime

            s_iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s_iso)
            return int(dt.timestamp() * 1_000_000_000)
        except Exception:
            return None
    return None


def ns_from_magnitude(n: float) -> int:
    """Public spelling of :func:`_scale_to_ns` for adapters whose timestamps
    come as ms or ns depending on the version (SigNoz)."""
    return _scale_to_ns(n)


def _scale_to_ns(n: float) -> int:
    """Infer time unit from magnitude. Handy when backends differ."""
    # Anything larger than ~1e18 is already ns.
    # 1e15–1e18 → microseconds (Jaeger)
    # 1e12–1e15 → milliseconds
    # below that → seconds
    abs_n = abs(n)
    if abs_n >= 1e18:
        return int(n)
    if abs_n >= 1e15:
        return int(n * 1_000)
    if abs_n >= 1e12:
        return int(n * 1_000_000)
    return int(n * 1_000_000_000)


def iso_to_ns(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp() * 1_000_000_000)
    except Exception:
        return None
