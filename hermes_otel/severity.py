"""The package's one log-severity vocabulary (#521).

Three spellings of a level meet in this plugin: Python's :mod:`logging`
levels (10..50: the stdlib bridge, the ``logs:`` block, the live store's
``level`` column), OTel severity numbers (1..24: what every record carries
and every backend stores) and the level *names* people and backends write
(``WARN`` / ``WARNING``, ``FATAL`` / ``CRITICAL``, ``TRACE``). These three
functions are the only translation between them; no other module keeps a
level table. Pure stdlib, so the dashboard adapters import it too.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

# Level name -> Python logging level, in both the OTel and the stdlib
# spelling of each band. TRACE sits below DEBUG, where OTel puts it.
_PYTHON_LEVELS = {
    "TRACE": 5,
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
}

# Python level -> the lowest OTel severity number of that band (the SDK's
# ``std_to_otel`` maps DEBUG->5, INFO->9, WARNING->13, ERROR->17, CRITICAL->21).
_OTEL_FLOORS = (
    (logging.CRITICAL, 21),
    (logging.ERROR, 17),
    (logging.WARNING, 13),
    (logging.INFO, 9),
    (logging.DEBUG, 5),
)


def python_level(name: Any, default: int = logging.INFO) -> int:
    """``"WARN"`` / ``"warning"`` / ``"30"`` -> the Python level; *default* when empty or unknown."""
    text = str(name).strip().upper() if name is not None else ""
    if text.isdigit():
        return int(text)
    return _PYTHON_LEVELS.get(text, default)


def otel_severity(level: int) -> int:
    """A Python level -> the lowest OTel severity number of its band (1 below DEBUG)."""
    return next((sev for floor, sev in _OTEL_FLOORS if int(level) >= floor), 1)


def severity_number(text: Any, default: Optional[int] = None) -> Optional[int]:
    """A level spelling -> OTel severity number; *default* when unknown.

    Accepts a level name in either spelling and digits on either scale: a
    number up to 24 is already an OTel severity, a larger one a Python level.
    """
    if text is None:
        return default
    spelled = str(text).strip().upper()
    if spelled.isdigit():
        number = int(spelled)
        return number if number <= 24 else otel_severity(number)
    level = _PYTHON_LEVELS.get(spelled)
    return otel_severity(level) if level is not None else default


def level_names_at_least(min_level: int) -> Tuple[str, ...]:
    """Every level spelling at or above a Python logging level (both of each
    pair, ``WARN`` and ``WARNING``), for a backend that filters on the level's
    text rather than its number (#514)."""
    floor = otel_severity(min_level)
    return tuple(name for name, level in _PYTHON_LEVELS.items() if otel_severity(level) >= floor)
