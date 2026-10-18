"""Backend adapter registry for the dashboard tab.

Adapters self-register via :func:`register` at import time. Importing
this package triggers registration of every adapter module below.
Look up the active adapter via :func:`resolve_adapter`.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from hermes_otel import plugin_config as _plugin_config
from hermes_otel.backend_specs import SPECS, canonical_type
from hermes_otel.coerce import coerce_bool
from hermes_otel.helpers import named_profile
from hermes_otel.hermes_home import resolve_profile_name

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
    """The adapter for a type or one of its manifest aliases (``hyperdx``)."""
    wanted = {backend_type, canonical_type(backend_type)}
    for cls in _ADAPTERS:
        if wanted & set(cls.handles):
            return cls
    return None


# ── Config loader ──────────────────────────────────────────────────────
#
# The dashboard reads the SAME file the tracer does, through the tracer's
# resolver (``plugin_config.resolve_config_path_with_reason``: ``HERMES_OTEL_CONFIG``
# > ``$HERMES_HOME/hermes_otel.yaml`` > the legacy ``config.yaml`` inside the
# plugin directory), or the Traces tab silently sees no backends (#413, #519).


def candidate_config_paths() -> List[Path]:
    """Where the tracer looks, most preferred first; ``/status`` reports them
    when nothing matches."""
    override = os.environ.get(_plugin_config.CONFIG_PATH_ENV, "").strip()
    paths = [Path(override).expanduser()] if override else []
    paths += [_plugin_config.durable_config_path(), _plugin_config.legacy_config_path()]
    return list(dict.fromkeys(paths))


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
    """The config file the tracer resolves and its contents as a plain dict:
    ``(path, data)``, ``{}`` for a missing or unparseable file (``load_yaml``
    warns, the caller never handles an exception). Cached per file identity.
    """
    cfg_path, _reason = _plugin_config.resolve_config_path_with_reason()
    if cfg_path is None:
        return None, {}
    ident = _file_identity(cfg_path)
    cached = _CONFIG_CACHE.get(str(cfg_path))
    if cached is not None and ident is not None and cached[0] == ident:
        return cfg_path, cached[1]
    data = _plugin_config.load_yaml(cfg_path)
    if ident is not None:
        _CONFIG_CACHE[str(cfg_path)] = (ident, data)
    return cfg_path, data


def top_level_config() -> Dict[str, Any]:
    """Return the full top-level config dict (for adapters that care
    about options like ``project_name`` declared above ``backends:``)."""
    _, data = _load_raw_config()
    return data


def _profile_service_name(top: Dict[str, Any]) -> str:
    """The viewed profile's name when ``service_name_from_profile`` is on (the
    ``HERMES_OTEL_SERVICE_NAME_FROM_PROFILE`` env var when set, else the file)
    and a named profile is active, else ``""`` (#395).

    The dashboard resolves the home per request (``?profile=``), so this is the
    profile being viewed, not the one the process started in.
    """
    env = coerce_bool(os.environ.get("HERMES_OTEL_SERVICE_NAME_FROM_PROFILE"))
    enabled = env if env is not None else coerce_bool(top.get("service_name_from_profile"))
    if not enabled:
        return ""
    return named_profile(resolve_profile_name())


def default_service_name(cfg: Dict[str, Any]) -> str:
    """The service an adapter scopes its queries to when the filter names none:
    the entry's ``service_name``, else the plugin's own
    ``resource_attributes.service.name``, else the service the plugin's
    resource reports for the requested profile: ``hermes-agent``, or the
    profile's name with ``service_name_from_profile`` (#395). Jaeger needs one
    for every search; Uptrace pins its log queries to it (#295, #298)."""
    explicit = cfg.get("service_name")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    try:
        top = top_level_config()
        resource = top.get("resource_attributes") or {}
        name = resource.get("service.name") if isinstance(resource, dict) else None
        if isinstance(name, str) and name.strip():
            return name.strip()
        profile = _profile_service_name(top)
        if profile:
            return profile
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


def expand_entry(b: Dict[str, Any]) -> Dict[str, Any]:
    """The entry with ``${VAR}`` expanded in its string values, one level into
    mappings and lists (``headers``), the way the exporter's loader expands
    them (``plugin_config.expand_env_refs``; an unset variable stays literal
    and is warned about by name, never by value). Only the adapter sees the
    result: ``/status`` and the CLI keep reporting the entry as written (#614).
    """
    where = f"backends[{backend_label(b)}]"

    def _one(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: _plugin_config.expand_env_refs(x, where=where) for k, x in v.items()}
        if isinstance(v, list):
            return [_plugin_config.expand_env_refs(x, where=where) for x in v]
        return _plugin_config.expand_env_refs(v, where=where)

    return {k: _one(v) for k, v in b.items()}


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
        adapter = cls(expand_entry(b))
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
# The manifest names the adapter modules, in manifest order. Imports are at
# the bottom so the registry + helpers above are fully defined before adapter
# modules start hitting them. Each import is try/except so a single broken
# adapter doesn't take the whole dashboard offline.


def _safe_import(name: str) -> None:
    try:
        __import__(f"{__name__}.{name}", fromlist=[name])
    except Exception:  # pragma: no cover — defensive
        logging.getLogger(__name__).warning(
            "hermes_otel: failed to load backend adapter %s", name, exc_info=True
        )


for _name in dict.fromkeys(s.adapter_module for s in SPECS if s.adapter_module):
    _safe_import(_name)
