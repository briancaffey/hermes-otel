"""Shared debug helpers for hermes_otel.

Debug logging is opt-in via HERMES_OTEL_DEBUG=true to avoid noisy file writes
in normal operation.
"""

from __future__ import annotations

import os

_DEBUG_LOG = os.path.expanduser("~/.hermes/plugins/hermes_otel/debug.log")
_DEBUG_ENABLED = os.getenv("HERMES_OTEL_DEBUG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def debug_log(msg: str) -> None:
    """Write a debug line if debug logging is enabled."""
    if not _DEBUG_ENABLED:
        return
    try:
        with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass


def mask_secret(value: str, visible: int = 4) -> str:
    """Return a minimally identifying masked representation of secrets."""
    if not value:
        return "NOT SET"
    if len(value) <= visible:
        return "*" * len(value)
    return f"{value[:visible]}***"
