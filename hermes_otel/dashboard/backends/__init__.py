"""Backend adapter registry for the dashboard tab.

Adapters self-register via :func:`register` at import time. Importing
this package triggers registration of every adapter module below.
Look up the active adapter via :func:`resolve_adapter`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from .base import BackendAdapter, ConfigError

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


def _hermes_home() -> Path:
    """Hermes's scope-aware home (the profile's own inside a multiplexed
    gateway, #70) when Hermes is importable, else ``$HERMES_HOME`` / ``~/.hermes``."""
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found]

        home = get_hermes_home()
        if home:
            return Path(home)
    except Exception:
        pass
    env_home = os.environ.get("HERMES_HOME", "").strip()
    return Path(env_home).expanduser() if env_home else Path.home() / ".hermes"


def _candidate_config_paths() -> List[Path]:
    """Where the plugin's config may live, most preferred first.

    Mirrors ``hermes_otel.plugin_config.resolve_config_path`` — the tracer and
    the dashboard must read the SAME file or the Traces tab silently sees no
    backends: ``HERMES_OTEL_CONFIG`` > ``$HERMES_HOME/hermes_otel.yaml`` (the
    documented, reinstall-safe location) > the legacy ``config.yaml`` inside
    the plugin directory. (Not imported from ``plugin_config`` because this
    package is loaded by file path as a top-level ``backends`` package.)
    """
    paths: List[Path] = []
    override = os.environ.get("HERMES_OTEL_CONFIG", "").strip()
    if override:
        paths.append(Path(override).expanduser())
    home = _hermes_home()
    paths.append(home / "hermes_otel.yaml")
    here = Path(__file__).resolve().parent  # dashboard/backends/
    plugin_root = here.parent.parent  # plugin root (…/hermes_otel/)
    paths.append(plugin_root / "config.yaml")
    paths.append(home / "plugins" / "hermes_otel" / "config.yaml")
    seen: set = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


def resolve_config_path() -> Optional[Path]:
    for p in _candidate_config_paths():
        if p.exists():
            return p
    return None


def candidate_config_paths() -> List[Path]:
    """Exposed so /status can report where we looked when nothing matches."""
    return _candidate_config_paths()


# ── Caches (#290) ──────────────────────────────────────────────────────
#
# The dashboard polls every few seconds; parsing the YAML and rebuilding the
# adapter on every request threw away every per-instance cache (Phoenix's
# project id, OpenObserve's schema, Uptrace's dialect) and cost two or three
# round trips per request. Both caches key on the file's identity (path,
# mtime, size), so an edit is picked up on the next request and nothing
# needs restarting.

_CONFIG_CACHE: Dict[str, Tuple[Tuple[float, int], Dict[str, Any]]] = {}
_ADAPTER_CACHE: Dict[Tuple[Any, ...], BackendAdapter] = {}


def clear_caches() -> None:
    """Forget parsed configs and adapter instances (tests, or a config reload)."""
    _CONFIG_CACHE.clear()
    _ADAPTER_CACHE.clear()


def _file_identity(path: Path) -> Optional[Tuple[float, int]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def _load_raw_config() -> Tuple[Optional[Path], Dict[str, Any]]:
    """Parse config.yaml into a plain dict. Returns ``(path, data)``.

    Returns ``(path, {})`` on any parse failure so callers never need
    to handle exceptions. Cached per file identity.
    """
    cfg_path = resolve_config_path()
    if cfg_path is None:
        return None, {}
    ident = _file_identity(cfg_path)
    cached = _CONFIG_CACHE.get(str(cfg_path))
    if cached is not None and ident is not None and cached[0] == ident:
        return cfg_path, cached[1]
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
        data = {}
    if ident is not None:
        _CONFIG_CACHE[str(cfg_path)] = (ident, data)
    return cfg_path, data


def top_level_config() -> Dict[str, Any]:
    """Return the full top-level config dict (for adapters that care
    about options like ``project_name`` declared above ``backends:``)."""
    _, data = _load_raw_config()
    return data


def default_service_name(cfg: Dict[str, Any]) -> str:
    """The service an adapter scopes its queries to when the filter names none:
    the entry's ``service_name``, else the plugin's own
    ``resource_attributes.service.name``, else ``hermes-agent`` (the plugin's
    default resource). Jaeger needs one for every search; Uptrace pins its
    log queries to it (#295, #298)."""
    explicit = cfg.get("service_name")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    try:
        resource = top_level_config().get("resource_attributes") or {}
        name = resource.get("service.name") if isinstance(resource, dict) else None
        if isinstance(name, str) and name.strip():
            return name.strip()
    except Exception:
        pass
    return "hermes-agent"


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


def _instantiate(b: Dict[str, Any], cfg_path: Optional[Path] = None) -> Optional[BackendAdapter]:
    """The adapter for one entry, reused across requests while the config file
    is unchanged. A constructor that raises is a config error the user should
    see (``project_id: abc``), not "no adapter for this type"."""
    cls = find_adapter_class(b.get("type", ""))
    if cls is None:
        return None
    try:
        entry_key = json.dumps(b, sort_keys=True, default=str)
    except Exception:
        entry_key = repr(b)
    key = (str(cfg_path) if cfg_path else None, _file_identity(cfg_path) if cfg_path else None)
    key = key + (entry_key,)
    cached = _ADAPTER_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        adapter = cls(b)
    except ConfigError:
        raise
    except Exception as e:
        raise ConfigError(
            f"Backend {backend_label(b)!r} ({b.get('type')}) could not be set up: {e}"
        )
    # One entry per key; drop stale instances of the same file so the cache
    # does not grow with every edit.
    for stale in [k for k in _ADAPTER_CACHE if k[0] == key[0] and k != key]:
        _ADAPTER_CACHE.pop(stale, None)
    _ADAPTER_CACHE[key] = adapter
    return adapter


def backend_label(b: Dict[str, Any]) -> str:
    """The name a backend entry is addressed by (``name``, else ``type``)."""
    return str(b.get("name") or b.get("type") or "")


def _find(backends: List[Dict[str, Any]], wanted: str) -> Optional[Dict[str, Any]]:
    """The entry addressed by ``wanted``: an exact ``name`` first, then the
    first entry of that ``type`` — so two entries of one type (say a local and
    a cluster ``lgtm``) are still addressable by their names."""
    for b in backends:
        if backend_label(b) == wanted:
            return b
    for b in backends:
        if b.get("type") == wanted:
            return b
    return None


def resolve_adapter(
    name: Optional[str] = None,
) -> Tuple[Optional[BackendAdapter], List[Dict[str, Any]], Optional[Path], Optional[str]]:
    """Pick the adapter for a request.

    1. ``name`` (a request's ``backend=`` parameter), matched against the
       entry's ``name`` or ``type``. A name that matches no configured entry
       raises ``KeyError`` listing the configured names (#177); a match
       whose type has no adapter returns ``(None, ...)``.
    2. Otherwise the ``query_backend`` pin, by ``name`` or ``type``.
    3. Otherwise the first configured backend whose type has an adapter.
    4. ``(None, ...)`` only when nothing matches.
    """
    cfg_path, backends, pin = load_config()

    if name:
        match = _find(backends, name)
        if match is None:
            raise KeyError(", ".join(backend_label(b) for b in backends) or "(none configured)")
        return _instantiate(match, cfg_path), backends, cfg_path, pin

    if pin:
        match = _find(backends, pin)
        if match is not None:
            adapter = _instantiate(match, cfg_path)
            if adapter is not None:
                return adapter, backends, cfg_path, pin

    # The first entry that has an adapter AND sets up cleanly; a broken entry
    # is skipped here (asking for it by name reports the error).
    for b in backends:
        try:
            adapter = _instantiate(b, cfg_path)
        except ConfigError:
            continue
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
