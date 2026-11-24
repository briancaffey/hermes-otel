"""A fake of the core config lookup behind an endpoint (the endpoint-key path).

``_endpoint_api_key`` reads Hermes's own config: the provider entry behind a base
URL, matched by origin, and its declared ``key_env``. Tests have no Hermes
install, so this installs the two modules that lookup imports and mirrors the
core's origin semantics — default ports, and http vs https as different origins.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Optional, Tuple
from urllib.parse import urlsplit


def base_url_origin(base_url: str) -> Tuple[str, str, int]:
    """The core's ``(scheme, hostname, effective_port)``; ``("", "", 0)`` on no host/bad port."""
    parsed = urlsplit((base_url or "").strip())
    host = (parsed.hostname or "").lower()
    if not host:
        return ("", "", 0)
    scheme = (parsed.scheme or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return ("", "", 0)
    default = {"https": 443, "http": 80}.get(scheme, 0)
    return (scheme, host, default if port is None else port)


def install_config(monkeypatch, providers: Any, env: Optional[dict] = None) -> None:
    """Install fake ``agent.models_dev`` and ``utils``; set ``env`` in the process env."""
    utils_mod = types.ModuleType("utils")
    utils_mod.base_url_origin = base_url_origin  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "utils", utils_mod)

    models_dev = types.ModuleType("agent.models_dev")
    models_dev._cfg_get = lambda *keys, default=None: providers  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent.models_dev", models_dev)
    pkg = sys.modules.get("agent")
    if pkg is None:
        pkg = types.ModuleType("agent")
        monkeypatch.setitem(sys.modules, "agent", pkg)
    pkg.models_dev = models_dev  # type: ignore[attr-defined]

    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)
