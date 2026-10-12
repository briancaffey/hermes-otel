"""Log severity spellings shared by the adapters (#403): OTel severity
numbers for a level name, and the severity floor of a Python logging level."""

from __future__ import annotations

from typing import Any, Optional

_SEVERITY_NUMBERS = {
    "TRACE": 1,
    "DEBUG": 5,
    "INFO": 9,
    "WARN": 13,
    "WARNING": 13,
    "ERROR": 17,
    "CRITICAL": 21,
    "FATAL": 21,
}


def severity_number_for(level: Any) -> Optional[int]:
    """OTel severity number for a level spelling (Python or OTel); None when unknown."""
    if level is None:
        return None
    text = str(level).strip().upper()
    if text.isdigit():
        n = int(text)
        return n if n <= 24 else {10: 5, 20: 9, 30: 13, 40: 17, 50: 21}.get(n)
    return _SEVERITY_NUMBERS.get(text)


# Python logging levels → the smallest OTel severity number of that band
# (the exporter maps DEBUG→5, INFO→9, WARNING→13, ERROR→17, CRITICAL→21).
_OTEL_SEVERITY_FLOOR = {10: 5, 20: 9, 30: 13, 40: 17, 50: 21}


def otel_severity_for(min_level: int) -> int:
    band = max((lvl for lvl in _OTEL_SEVERITY_FLOOR if lvl <= int(min_level)), default=0)
    return _OTEL_SEVERITY_FLOOR.get(band, 1)
