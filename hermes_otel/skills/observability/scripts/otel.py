#!/usr/bin/env python3
"""Launcher for the hermes-otel terminal query tool (``hermes_otel.query_cli``).

Hermes lists this file under the ``hermes_otel:observability`` skill's linked
files and expands ``${HERMES_SKILL_DIR}`` in the skill text, so the agent can run

    python3 ${HERMES_SKILL_DIR}/scripts/otel.py trace last

from the chat. The plugin directory is not on ``sys.path`` (Hermes loads
plugins by path), so this adds the package's parent and hands over to
``query_cli.main``. Only the standard library is needed for the live store;
``--source <backend>`` additionally needs ``pyyaml`` (the Hermes venv has it).
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parents[3]  # …/hermes_otel
if str(_PKG.parent) not in sys.path:
    sys.path.insert(0, str(_PKG.parent))

from hermes_otel.query_cli import main  # noqa: E402  (after the path shim)

if __name__ == "__main__":
    sys.exit(main())
