"""Shared debug + logging helpers for hermes_otel.

Two flavours of output:

* :func:`debug_log` — opt-in via ``HERMES_OTEL_DEBUG=true``. Writes
  verbose per-span lines to ``~/.hermes/plugins/hermes_otel/debug.log``
  so they never pollute Hermes' stdout.

* The ``hermes_otel`` logger — stock :mod:`logging` for user-visible
  startup / warning / error messages. As a library, we add a
  :class:`~logging.NullHandler` at import time so downstream apps
  don't get "no handler" warnings. :func:`configure_default_handler`
  installs a stderr handler at INFO level when no handler has been
  configured yet — called from ``register()`` so users always see
  "✓ backend connected" without having to wire up logging themselves.
"""

from __future__ import annotations

import logging
import os
import sys

_DEBUG_LOG = os.path.expanduser("~/.hermes/plugins/hermes_otel/debug.log")
_DEBUG_ENABLED = os.getenv("HERMES_OTEL_DEBUG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


# Module-level logger shared across the plugin. Named "hermes_otel" (not
# __name__) so consumers that want to silence / reroute plugin logs only
# need to know the one name.
logger = logging.getLogger("hermes_otel")
logger.addHandler(logging.NullHandler())


def debug_log(msg: str) -> None:
    """Write a debug line if debug logging is enabled."""
    if not _DEBUG_ENABLED:
        return
    try:
        with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass


def configure_default_handler() -> None:
    """Install a stderr handler on ``hermes_otel`` if none is configured.

    Idempotent. Skips installation when the consumer has already added
    a non-null handler — that way apps that wire up their own logging
    (Hermes itself, downstream integrations, pytest's caplog) stay in
    control.
    """
    for h in logger.handlers:
        if not isinstance(h, logging.NullHandler):
            return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
