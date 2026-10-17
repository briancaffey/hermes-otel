"""Scalar coercion for loosely-typed payload values (#522).

Hook payloads, config values and stored span attributes arrive as ints, floats,
digit strings, bools or nothing at all. These four never raise and differ only
in what "nothing" becomes: :func:`to_int` zero-fills (usage counters),
:func:`to_optional_int` and :func:`optional_number` return None (status codes,
metric values), :func:`coerce_bool` tells "not reported" from "reported false".
A leaf module: it imports nothing from the package.
"""

from __future__ import annotations

from typing import Any, Optional

_TRUE_STRINGS = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "f", "no", "n", "off", ""})


def to_int(value: Any) -> int:
    """Best-effort integer conversion for usage counters (never raises)."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(float(text))
        except ValueError:
            return 0
    return 0


def to_optional_int(value: Any) -> Optional[int]:
    """Best-effort int conversion that returns None (not 0) when unparseable.

    Distinct from :func:`to_int` (which zero-fills) because for error
    telemetry we must tell "status 0" apart from "no status reported".
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def optional_number(value: Any) -> Optional[float]:
    """``float(value)`` or None for bools, None, empty and unparsable input."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coerce_bool(value: Any) -> Optional[bool]:
    """Parse a loosely-typed truthy/falsey value, or None when undecidable.

    Accepts real bools, ints, and the usual string spellings. Returns None for
    ``None`` so callers can distinguish "not reported" from "reported false".
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_STRINGS:
            return True
        if text in _FALSE_STRINGS:
            return False
        return None
    return None
