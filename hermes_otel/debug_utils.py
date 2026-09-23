"""Shared debug + logging helpers for hermes_otel.

Two flavours of output:

* :func:`debug_log` — opt-in via ``HERMES_OTEL_DEBUG=true``. Writes
  verbose per-span lines to ``$HERMES_HOME/plugins/hermes_otel/debug.log``
  (``~/.hermes`` when ``HERMES_HOME`` is unset) so they never pollute
  Hermes' stdout. The file is opened once, line-buffered, on the first
  line written.

* SDK forwarding — with debug on, everything the OpenTelemetry SDK logs at
  WARNING or above (export failures such as ``Failed to export span batch
  code: 405``, dropped-span warnings) is copied into the same file, so the
  reason an export never arrived is next to the spans that were sent
  (:func:`install_sdk_log_forwarding`).

* The ``hermes_otel`` logger — stock :mod:`logging` for user-visible
  startup / warning / error messages. As a library, we add a
  :class:`~logging.NullHandler` at import time so downstream apps
  don't get "no handler" warnings. :func:`configure_default_handler`
  installs a stderr handler at INFO level when no handler has been
  configured yet — called from ``register()`` so users always see
  "✓ backend connected" without having to wire up logging themselves.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Optional, TextIO

_DEBUG_ENABLED = os.getenv("HERMES_OTEL_DEBUG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_debug_file: Optional[TextIO] = None
_debug_lock = threading.Lock()


# Module-level logger shared across the plugin. Named "hermes_otel" (not
# __name__) so consumers that want to silence / reroute plugin logs only
# need to know the one name.
logger = logging.getLogger("hermes_otel")
logger.addHandler(logging.NullHandler())


def debug_log_path() -> str:
    """Where :func:`debug_log` writes: ``<hermes home>/plugins/hermes_otel/debug.log``.

    The home is the profile's own under a multiplexed gateway (#70), see
    :mod:`hermes_otel.hermes_home`; ``$HERMES_HOME`` / ``~/.hermes`` otherwise.
    """
    from .hermes_home import resolve_hermes_home

    return os.path.join(str(resolve_hermes_home()), "plugins", "hermes_otel", "debug.log")


def debug_log(msg: str) -> None:
    """Write a debug line if debug logging is enabled. Never raises."""
    if not _DEBUG_ENABLED:
        return
    global _debug_file
    try:
        with _debug_lock:
            if _debug_file is None:
                path = debug_log_path()
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _debug_file = open(path, "a", encoding="utf-8", buffering=1)
            _debug_file.write(f"{msg}\n")
    except Exception:
        pass


def close_debug_log() -> None:
    """Close the debug log file (tracer shutdown); the next line reopens it."""
    global _debug_file
    with _debug_lock:
        f, _debug_file = _debug_file, None
    if f is not None:
        try:
            f.close()
        except Exception:
            pass


_SDK_HANDLER_MARKER = "_hermes_otel_sdk_forwarder"
_SDK_LOGGER = "opentelemetry"


class _SdkToDebugLog(logging.Handler):
    """Copies OpenTelemetry SDK log records into the debug log."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            debug_log(f"[sdk] {record.name} {record.levelname}: {record.getMessage()}")
        except Exception:
            pass


def install_sdk_log_forwarding() -> bool:
    """Attach the SDK-to-debug-log forwarder (idempotent; no-op unless debug is on).

    The OTLP exporters report failures only through the ``opentelemetry.*``
    loggers, which nothing displays by default; with ``HERMES_OTEL_DEBUG`` on
    they belong in ``debug.log``. Returns True when the handler is attached.
    """
    if not _DEBUG_ENABLED:
        return False
    target = logging.getLogger(_SDK_LOGGER)
    if any(getattr(h, _SDK_HANDLER_MARKER, False) for h in target.handlers):
        return True
    handler = _SdkToDebugLog(level=logging.WARNING)
    setattr(handler, _SDK_HANDLER_MARKER, True)
    target.addHandler(handler)
    # WARNING records must reach the handler: the logger itself may be left
    # at NOTSET (inherits root, usually WARNING) — only lower it if needed.
    if target.level > logging.WARNING:
        target.setLevel(logging.WARNING)
    return True


def remove_sdk_log_forwarding() -> None:
    """Detach the forwarder installed by :func:`install_sdk_log_forwarding`."""
    target = logging.getLogger(_SDK_LOGGER)
    for h in list(target.handlers):
        if getattr(h, _SDK_HANDLER_MARKER, False):
            target.removeHandler(h)


def configure_default_handler() -> None:
    """Install a stderr handler on ``hermes_otel`` if none is configured.

    Idempotent. Skips installation when the consumer has already added
    a non-null handler — that way apps that wire up their own logging
    (Hermes itself, downstream integrations, pytest's caplog) stay in
    control.
    """
    for h in logger.handlers:
        if not isinstance(h, logging.NullHandler):
            return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
