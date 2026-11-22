"""YAML with whichever library the interpreter running Hermes has.

PyYAML when it is installed. Otherwise ruamel.yaml, which Hermes Agent has
used for its own config since September 2026: its published container image
ships ruamel.yaml and no PyYAML, and a process started from that image's base
environment would otherwise ignore ``hermes_otel.yaml``.

Both read YAML 1.1 (``yes`` / ``no`` / ``on`` / ``off`` are booleans), so a
config file means the same thing under either library.
"""

from __future__ import annotations

import io
from typing import Any


def _pyyaml() -> Any:
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    return yaml


def _ruamel() -> Any:
    try:
        from ruamel.yaml import YAML  # type: ignore
    except ImportError:
        return None
    parser = YAML(typ="safe", pure=True)
    parser.version = (1, 1)
    return parser


def available() -> bool:
    """True when a YAML library can be imported."""
    return _pyyaml() is not None or _ruamel() is not None


def safe_load(text: str) -> Any:
    """Parse ``text``. Raises ``ImportError`` when no YAML library is
    installed, and the library's own error when the text is malformed."""
    yaml = _pyyaml()
    if yaml is not None:
        return yaml.safe_load(text)
    parser = _ruamel()
    if parser is None:
        raise ImportError("neither PyYAML nor ruamel.yaml is installed")
    return parser.load(text)


def safe_dump(value: Any) -> str:
    """``value`` as block-style YAML with keys in the order given. Raises
    ``ImportError`` when no YAML library is installed."""
    yaml = _pyyaml()
    if yaml is not None:
        return yaml.safe_dump(value, sort_keys=False, default_flow_style=False, allow_unicode=True)
    parser = _ruamel()
    if parser is None:
        raise ImportError("neither PyYAML nor ruamel.yaml is installed")
    parser.version = None  # no "%YAML 1.1" directive at the top of the output
    parser.default_flow_style = False
    parser.allow_unicode = True
    parser.sort_base_mapping_type_on_output = False
    out = io.StringIO()
    parser.dump(value, out)
    return out.getvalue()
