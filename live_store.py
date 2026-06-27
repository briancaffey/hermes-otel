"""In-process bounded telemetry store for the zero-config dashboard.

The plugin normally fires telemetry off to OTLP backends and keeps nothing
locally. For the dashboard's **Live** mode we additionally retain a small,
bounded, in-memory window of recent spans / metric events / logs so the
dashboard (which runs in the *same* process and reads :func:`get_live_store`)
can render the agent's activity in real time — with **no external backend
required**.

Design:
- Three ring buffers (``collections.deque(maxlen=…)``) — old items drop off.
- Every item carries a monotonically increasing ``seq`` cursor so the dashboard
  can poll/stream incrementally (``since=<cursor>``) instead of re-fetching.
- Thread-safe: Hermes dispatches hooks across executor threads, so all mutation
  and reads take a lock. Reads return shallow copies.
- Zero allocation when disabled: the store is only created/fed when
  ``dashboard_live`` is on.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional


class LiveStore:
    """Bounded, thread-safe ring buffers for recent spans / metrics / logs."""

    def __init__(
        self,
        max_spans: int = 1000,
        max_metrics: int = 5000,
        max_logs: int = 2000,
    ) -> None:
        self._spans: Deque[Dict[str, Any]] = deque(maxlen=max_spans)
        self._metrics: Deque[Dict[str, Any]] = deque(maxlen=max_metrics)
        self._logs: Deque[Dict[str, Any]] = deque(maxlen=max_logs)
        self._lock = threading.Lock()
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ── writers (called from the hot path — keep cheap, never raise) ──────

    def add_span(self, span: Dict[str, Any]) -> None:
        try:
            with self._lock:
                span["seq"] = self._next_seq()
                self._spans.append(span)
        except Exception:  # pragma: no cover — telemetry must never break the agent
            pass

    def add_metric(self, name: str, value: float, attributes: Dict[str, Any], ts_ns: int) -> None:
        try:
            with self._lock:
                self._metrics.append(
                    {
                        "seq": self._next_seq(),
                        "name": name,
                        "value": value,
                        "attributes": dict(attributes or {}),
                        "time_unix_nano": ts_ns,
                    }
                )
        except Exception:  # pragma: no cover
            pass

    def add_log(self, record: Dict[str, Any]) -> None:
        try:
            with self._lock:
                record["seq"] = self._next_seq()
                self._logs.append(record)
        except Exception:  # pragma: no cover
            pass

    # ── readers (called from the dashboard API) ───────────────────────────

    @staticmethod
    def _since(items: Deque[Dict[str, Any]], since: int, limit: int) -> List[Dict[str, Any]]:
        out = [it for it in items if it.get("seq", 0) > since]
        if limit and len(out) > limit:
            out = out[-limit:]
        return out

    def spans(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            return self._since(self._spans, since, limit)

    def metrics(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            return self._since(self._metrics, since, limit)

    def logs(self, since: int = 0, limit: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            return self._since(self._logs, since, limit)

    def cursor(self) -> int:
        with self._lock:
            return self._seq

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "spans": len(self._spans),
                "metrics": len(self._metrics),
                "logs": len(self._logs),
                "cursor": self._seq,
            }

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()
            self._metrics.clear()
            self._logs.clear()
            self._seq = 0


# Module-level singleton so the dashboard API (loaded separately by the Hermes
# web server, but in the same process) reads the exact store the tracer feeds.
_LIVE_STORE: Optional[LiveStore] = None
_LIVE_LOCK = threading.Lock()


def get_live_store(create: bool = False, **kwargs: Any) -> Optional[LiveStore]:
    """Return the process-wide :class:`LiveStore`.

    ``create=True`` lazily builds it (used by the tracer when ``dashboard_live``
    is enabled). The dashboard API calls with ``create=False`` and treats
    ``None`` as "live mode unavailable".
    """
    global _LIVE_STORE
    if _LIVE_STORE is None and create:
        with _LIVE_LOCK:
            if _LIVE_STORE is None:
                _LIVE_STORE = LiveStore(**kwargs)
    return _LIVE_STORE
