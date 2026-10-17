"""Timestamp conversions shared by the adapters (#403): whatever a backend
returns (ns, us, ms, s, ISO-8601) → unix nanoseconds, and unix nanoseconds →
the RFC 3339 string a backend's query API takes (#519)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

_RFC3339_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$")


def ns_from_s(seconds: int) -> int:
    """Whole unix seconds → unix nanoseconds."""
    return int(seconds) * 1_000_000_000


def ns_to_rfc3339(ns: int, digits: int = 9) -> str:
    """Unix nanoseconds → ``2026-10-11T20:27:50.202258944Z`` in UTC, the
    fraction truncated to ``digits`` places (``6`` for a µs API, ``0`` for no
    fraction at all)."""
    secs, rem = divmod(int(ns), 1_000_000_000)
    base = datetime.fromtimestamp(secs, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    frac = f".{rem // 10 ** (9 - digits):0{digits}d}" if digits else ""
    return f"{base}{frac}Z"


def rfc3339_to_ns(text: Any) -> int:
    """``2026-10-11T20:27:50.202258944Z`` → unix ns (fraction kept to 9 digits,
    which :func:`iso_to_ns` cannot: it goes through a float); ``0`` for anything
    that is not an RFC 3339 timestamp."""
    m = _RFC3339_RE.match(str(text or "").strip())
    if not m:
        return 0
    base, frac, tz = m.groups()
    dt = datetime.fromisoformat(base + ("+00:00" if tz == "Z" else tz))
    return int(dt.timestamp()) * 1_000_000_000 + int((frac or "0")[:9].ljust(9, "0"))


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
            s_iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s_iso)
            return int(dt.timestamp() * 1_000_000_000)
        except Exception:
            return None
    return None


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
