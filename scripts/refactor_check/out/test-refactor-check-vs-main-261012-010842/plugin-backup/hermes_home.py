"""Which Hermes home, and which profile, this plugin instance belongs to (#70).

Hermes keeps one plugin manager per home. A named profile runs from
``~/.hermes/profiles/<name>``; a multiplexed gateway serves several profiles
in one process, importing this plugin once per profile and binding every
discovery, load and request to that profile's home through a context-local
override (``hermes_constants.get_hermes_home``), not the ``HERMES_HOME``
environment variable, which stays at the process's own home.

Reading the environment therefore gives the wrong answer inside a multiplexed
gateway: every profile's plugin would share the default profile's config file,
live store and debug log. This module asks Hermes first and only falls back to
the environment when Hermes is not importable (tests, the standalone terminal
tool). Only the standard library is imported at module level.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _fallback_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "").strip()
    return Path(os.path.expandvars(raw)).expanduser() if raw else Path.home() / ".hermes"


def resolve_hermes_home() -> Path:
    """The Hermes home in force for the caller: Hermes's own scope-aware
    resolver when Hermes is importable, else ``$HERMES_HOME`` or ``~/.hermes``."""
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found]
    except Exception:
        return _fallback_home()
    try:
        home = get_hermes_home()
    except Exception:
        return _fallback_home()
    return Path(home) if home else _fallback_home()


def default_hermes_home() -> Path:
    """The pre-profile home: ``$HERMES_HOME`` of the process or ``~/.hermes``.

    A named profile's directory sits under ``<this>/profiles/<name>``. Hermes
    anchors the profiles root to its own root (not the scoped home), so this
    reads the process environment on purpose.
    """
    return _fallback_home()


def profile_name_for(home: Path, root: Optional[Path] = None) -> str:
    """``default``, the id under ``<root>/profiles/<id>``, or ``custom`` (pure).

    Mirrors ``hermes_cli.profiles.get_active_profile_name`` so the plugin
    reports the same name Hermes would, with or without Hermes importable.
    """
    root = root or default_hermes_home()
    try:
        resolved = Path(home).expanduser().resolve()
        root_resolved = Path(root).expanduser().resolve()
    except OSError:
        return "custom"
    if resolved == root_resolved:
        return "default"
    try:
        parts = resolved.relative_to(root_resolved / "profiles").parts
    except ValueError:
        return "custom"
    if len(parts) == 1 and _PROFILE_ID_RE.match(parts[0]):
        return parts[0]
    return "custom"


def resolve_profile_name() -> str:
    """The active profile's name, as Hermes names it.

    Hermes's ``get_active_profile_name`` is used when importable so the answer
    matches ``hermes profile list`` exactly; otherwise the name is derived from
    the resolved home the same way Hermes does it.
    """
    try:
        from hermes_cli.profiles import get_active_profile_name  # type: ignore[import-not-found]
    except Exception:
        return _profile_name_without_hermes(resolve_hermes_home())
    try:
        name = get_active_profile_name()
    except Exception:
        return _profile_name_without_hermes(resolve_hermes_home())
    return str(name) if name else "default"


def _profile_name_without_hermes(home: Path) -> str:
    """Without Hermes the profiles root is unknowable from ``HERMES_HOME`` alone
    (``hermes -p x`` sets it to the profile directory itself), so a home whose
    parent directory is ``profiles`` is read as that profile; anything else is
    ``default``. Used by the standalone terminal tool."""
    try:
        resolved = Path(home).expanduser().resolve()
    except OSError:
        return "default"
    if resolved.parent.name == "profiles" and _PROFILE_ID_RE.match(resolved.name):
        return resolved.name
    return "default"
