"""Where a Hermes profile lives, for the maintainer scripts (#524).

Imported by the harnesses under ``scripts/`` the way ``gen_semconv.py``
imports ``gen_config_docs``: the script puts ``scripts/`` on ``sys.path`` and
does ``from _common import profile_home``. Standard library only, so every
script stays runnable with ``uv run --extra dev python scripts/<x>.py``.
"""

from __future__ import annotations

import os
from pathlib import Path


def hermes_root() -> Path:
    """The Hermes root that holds ``profiles/`` (``$HERMES_HOME`` or ``~/.hermes``)."""
    env = os.environ.get("HERMES_HOME")
    return Path(env).expanduser() if env else Path.home() / ".hermes"


def profile_home(profile: str) -> Path:
    """The profile's home: the root itself for ``default`` (or ``""``)."""
    root = hermes_root()
    return root if profile in ("", "default") else root / "profiles" / profile
