"""Backend adapter registry for the dashboard tab.

Adapters self-register via :func:`register` at import time. Importing
this package triggers registration of every adapter module below.
Look up the active adapter via :func:`resolve_adapter`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from .base import BackendAdapter

# ── Registry ───────────────────────────────────────────────────────────

_ADAPTERS: List[Type[BackendAdapter]] = []


def register(cls: Type[BackendAdapter]) -> Type[BackendAdapter]:
    """Class decorator — adapters use this at the bottom of their module."""
    _ADAPTERS.append(cls)
    return cls


def adapters() -> List[Type[BackendAdapter]]:
    """Snapshot of all registered adapter classes."""
    return list(_ADAPTERS)


def find_adapter_class(backend_type: str) -> Optional[Type[BackendAdapter]]:
    for cls in _ADAPTERS:
        if backend_type in cls.handles:
            return cls
    return None


# ── Config loader ──────────────────────────────────────────────────────


def _candidate_config_paths() -> List[Path]:
    here = Path(__file__).resolve().parent  # dashboard/backends/
    plugin_root = here.parent.parent  # plugin root (…/hermes_otel/)
    paths: List[Path] = [plugin_root / "config.yaml"]
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        paths.append(Path(env_home) / "plugins" / "hermes_otel" / "config.yaml")
    paths.append(Path.home() / ".hermes" / "plugins" / "hermes_otel" / "config.yaml")
    return paths


def resolve_config_path() -> Optional[Path]:
    for p in _candidate_config_paths():
        if p.exists():
            return p
    return None


def candidate_config_paths() -> List[Path]:
    """Exposed so /status can report where we looked when nothing matches."""
    return _candidate_config_paths()


def _load_raw_config() -> Tuple[Optional[Path], Dict[str, Any]]:
    """Parse config.yaml into a plain dict. Returns ``(path, data)``.

    Returns ``(path, {})`` on any parse failure so callers never need
    to handle exceptions.
    """
    cfg_path = resolve_config_path()
    if cfg_path is None:
        return None, {}
    try:
        import yaml  # type: ignore
    except ImportError:
        return cfg_path, {}
    try:
        with cfg_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return cfg_path, {}
    if not isinstance(data, dict):
        return cfg_path, {}
    return cfg_path, data


def top_level_config() -> Dict[str, Any]:
    """Return the full top-level config dict (for adapters that care
    about options like ``project_name`` declared above ``backends:``)."""
    _, data = _load_raw_config()
    return data


def load_config() -> Tuple[Optional[Path], List[Dict[str, Any]], Optional[str]]:
    """Parse config.yaml. Returns ``(path, backends_list, query_backend_pin)``.

    Absent file, unparseable yaml, and missing ``backends:`` all come
    back as empty. The caller decides how to report this to the user.
    """
    cfg_path, data = _load_raw_config()
    raw = data.get("backends") if data else None
    backends: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("type"), str):
                backends.append(item)

    pin = data.get("query_backend") if data else None
    pin_str = pin.strip() if isinstance(pin, str) and pin.strip() else None

    return cfg_path, backends, pin_str


# ── Resolution ─────────────────────────────────────────────────────────


def _instantiate(b: Dict[str, Any]) -> Optional[BackendAdapter]:
    cls = find_adapter_class(b.get("type", ""))
    if cls is None:
        return None
    try:
        return cls(b)
    except Exception:
        return None


def resolve_adapter() -> (
    Tuple[Optional[BackendAdapter], List[Dict[str, Any]], Optional[Path], Optional[str]]
):
    """Pick the active adapter following the locked precedence rules.

    1. Try ``query_backend`` pin (by ``name`` or ``type``).
    2. Silently fall through to the first configured backend whose type
       has a registered adapter.
    3. Return ``(None, ...)`` only when nothing matches.
    """
    cfg_path, backends, pin = load_config()

    if pin:
        for b in backends:
            name_or_type = b.get("name") or b.get("type")
            if name_or_type == pin or b.get("type") == pin:
                adapter = _instantiate(b)
                if adapter is not None:
                    return adapter, backends, cfg_path, pin

    for b in backends:
        adapter = _instantiate(b)
        if adapter is not None:
            return adapter, backends, cfg_path, pin

    return None, backends, cfg_path, pin


# ── Eager import so adapters self-register ────────────────────────────
# Imports are at the bottom so the registry + helpers above are fully
# defined before adapter modules start hitting them.
# Each import is try/except so a single broken adapter doesn't take the
# whole dashboard offline.


def _safe_import(name: str) -> None:
    try:
        __import__(f"{__name__}.{name}", fromlist=[name])
    except Exception as e:  # pragma: no cover — defensive
        import logging

        logging.getLogger(__name__).warning(
            "hermes_otel: failed to load backend adapter %s: %s", name, e
        )


for _name in ("tempo", "phoenix", "signoz", "uptrace", "openobserve", "langfuse", "jaeger"):
    _safe_import(_name)
