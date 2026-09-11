"""Compatibility helpers for Hermes profile-scoped runtime state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def active_hermes_home() -> Path:
    """Return the active Hermes home, including context-local profile overrides."""
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).expanduser()
    except (ImportError, AttributeError):
        pass

    raw = os.environ.get("HERMES_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".hermes"


def profile_name_from_context(ctx: Any) -> str:
    """Return the host-provided profile name with an older-Hermes fallback."""
    try:
        profile_name = ctx.profile_name
    except Exception:
        return "default"

    if not isinstance(profile_name, str) or not profile_name.strip():
        return "default"
    return profile_name.strip()
