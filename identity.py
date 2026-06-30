"""Hermes runtime identity helpers for telemetry attributes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional

from .helpers import truncate_string


def active_profile_name(extra_kwargs: Optional[Mapping[str, Any]] = None) -> str:
    """Return the active Hermes profile name for telemetry.

    Hermes profile identity can arrive either as hook context, as the
    ``HERMES_PROFILE`` environment variable, or implicitly via ``HERMES_HOME``.
    The root ``~/.hermes`` home is the default profile; profile-local homes use
    ``~/.hermes/profiles/<profile>`` so their final path component is the name.
    """
    extra_kwargs = extra_kwargs or {}
    for key in ("agent_name", "profile", "hermes_profile", "hermes.profile", "gen_ai.agent.name"):
        raw = extra_kwargs.get(key)
        value = truncate_string(raw, 120) if raw is not None else ""
        if value:
            return value

    raw_profile = os.getenv("HERMES_PROFILE")
    value = truncate_string(raw_profile, 120) if raw_profile is not None else ""
    if value:
        return value

    raw_home = os.getenv("HERMES_HOME")
    hermes_home = truncate_string(raw_home, 500) if raw_home is not None else ""
    if hermes_home:
        path = Path(hermes_home)
        value = "default" if path.name == ".hermes" else truncate_string(path.name, 120)
        if value:
            return value

    return "hermes-agent"


def profile_attributes(extra_kwargs: Optional[Mapping[str, Any]] = None) -> dict[str, str]:
    """Return canonical Hermes profile span/resource attributes."""
    return {"hermes.profile": active_profile_name(extra_kwargs)}
