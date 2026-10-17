"""Which :class:`~hermes_otel.live_store.LiveStore` a process uses (#518).

The gateway has one store (``get_live_store``, the writer the tracer and the
hooks share); a dashboard serving several profiles from one process opens
one reader per profile's file (``get_live_store_for_home``). Both processes
point at the same SQLite file for a given home, so they share data without
sharing memory. ``hermes_otel.testing.reset_state`` forgets the singleton.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

from . import live_store as _ls
from .live_store import LiveStore

# Per-process singleton; both processes point at the same SQLite file (default
# path), so they share data without sharing memory.
_LIVE_STORE: Optional[LiveStore] = None
_LIVE_LOCK = threading.Lock()
# Read-side stores by file, for a dashboard that serves several profiles from
# one process (#70): each profile has its own ``hermes_otel_live.db``.
_STORES_BY_PATH: Dict[str, LiveStore] = {}
# Readers kept at most, beyond the one being asked for: a dashboard that has
# served many profiles does not hold a connection per profile forever (#290).
MAX_READERS = 16


def get_live_store_for_home(create: bool = True) -> Optional[LiveStore]:
    """The live store of the Hermes home in force for the caller.

    In the dashboard, Hermes resolves the home per request (``?profile=``), so
    the Live source must open that profile's file rather than the one the
    process opened first. The process singleton is reused when it already
    points at the same file; other files get one reader each, kept for the
    life of the process. ``None`` when the file cannot be opened.
    """
    # Looked up on the module so the test suite's redirect of the default
    # path (``conftest``) reaches this reader too.
    path = os.environ.get("HERMES_OTEL_LIVE_DB") or _ls._default_db_path()
    current = _LIVE_STORE
    if current is not None and str(getattr(current, "db_path", "")) == path:
        return current
    with _LIVE_LOCK:
        _evict_stale_readers(keep=path)
        store = _STORES_BY_PATH.get(path)
        if store is None and create:
            try:
                store = LiveStore(db_path=path, **_configured_limits())
            except Exception:  # pragma: no cover
                return None
            _STORES_BY_PATH[path] = store
            _evict_stale_readers(keep=path)
    return store


def _evict_stale_readers(keep: str) -> None:
    """Drop readers whose file no longer exists, then the oldest beyond
    ``MAX_READERS``; ``keep`` is never dropped. Caller holds ``_LIVE_LOCK``."""
    for p in list(_STORES_BY_PATH):
        if p != keep and not os.path.exists(p):
            _close_reader(_STORES_BY_PATH.pop(p))
    while len(_STORES_BY_PATH) > MAX_READERS:
        oldest = next((p for p in _STORES_BY_PATH if p != keep), None)
        if oldest is None:
            break
        _close_reader(_STORES_BY_PATH.pop(oldest))


def _close_reader(store: Any) -> None:
    close = getattr(store, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # pragma: no cover
            pass


def _configured_limits() -> Dict[str, Any]:
    """The row cap and retention of this home's config, so a reader that trims
    on open never trims harder than the gateway that writes the file."""
    try:
        from .plugin_config import load_config

        cfg = load_config()
        return {
            "max_rows": int(cfg.dashboard_live_max_spans),
            "retention_hours": float(cfg.dashboard_live_retention_hours),
        }
    except Exception:  # pragma: no cover: defaults keep the reader working
        return {}


def get_live_store(
    create: bool = False,
    db_path: Optional[str] = None,
    max_rows: int = 4000,
    retention_hours: float = 168.0,
    **_ignored: Any,
) -> Optional[LiveStore]:
    """Return the process-wide :class:`LiveStore`.

    ``create=True`` lazily builds it (the tracer, when ``dashboard_live`` is on).
    The dashboard API calls with ``create=True`` too (read side) so the file is
    opened even if the dashboard process started first. ``None`` only when the
    SQLite backend can't be opened at all.
    """
    global _LIVE_STORE
    if _LIVE_STORE is None and create:
        with _LIVE_LOCK:
            if _LIVE_STORE is None:
                try:
                    _LIVE_STORE = LiveStore(
                        db_path=db_path or os.environ.get("HERMES_OTEL_LIVE_DB"),
                        max_rows=max_rows,
                        retention_hours=retention_hours,
                    )
                except Exception:  # pragma: no cover
                    return None
    return _LIVE_STORE
