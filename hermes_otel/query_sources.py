"""Where the query CLI reads from: the live store or a dashboard adapter (#215).

:class:`LiveSource` wraps the SQLite live store, :class:`BackendSource` a
dashboard adapter (Phoenix, Langfuse, Jaeger, …) behind the same six methods,
so the commands in :mod:`hermes_otel.query_cli` do not care which one answers.
A backend's OTLP JSON is turned into live-store-shaped span dicts here, with
the package's decoders (``dashboard/backends/shapes.py``), so every command
renders both sources through one code path.

Only the standard library is needed for the live store. The adapters need
``pyyaml`` to read the config file, which :func:`_import_backends` checks
before touching them.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import semconv as _sc
from .coerce import optional_number
from .live_store import LiveStore, _default_db_path
from .live_summary import span_kind
from .severity import python_level


class CliError(Exception):
    """A user-facing failure; ``query_cli.main`` prints it and returns 1."""


# ── span normalisation ────────────────────────────────────────────────────


def spans_from_otlp(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The adapters' ``{"batches": [...]}`` trace → live-store-shaped span dicts."""
    # Imported here, not at the top: the backends package imports every adapter
    # and (when installed) fastapi, ~0.3 s the live-store path does not pay for.
    from .dashboard.backends.shapes import otlp_attrs_to_dict, otlp_status

    spans: List[Dict[str, Any]] = []
    for batch in payload.get("batches") or payload.get("resourceSpans") or []:
        resource = otlp_attrs_to_dict((batch.get("resource") or {}).get("attributes"))
        for scope in batch.get("scopeSpans") or []:
            for sp in scope.get("spans") or []:
                start = int(sp.get("startTimeUnixNano") or 0)
                end = int(sp.get("endTimeUnixNano") or 0)
                status = sp.get("status") or {}
                # Tempo returns the enum name (``STATUS_CODE_OK``); ``otlp_status``
                # normalises int and name alike.
                code = otlp_status(status.get("code") if isinstance(status, dict) else None)
                attrs = otlp_attrs_to_dict(sp.get("attributes"))
                if resource.get("service.name") and "service.name" not in attrs:
                    attrs["service.name"] = resource["service.name"]
                spans.append(
                    {
                        "trace_id": sp.get("traceId"),
                        "span_id": sp.get("spanId"),
                        "parent_span_id": sp.get("parentSpanId") or None,
                        "name": sp.get("name") or "",
                        "start_time_unix_nano": start,
                        "end_time_unix_nano": end,
                        "duration_ms": (end - start) / 1e6 if start and end else None,
                        "status": {2: "ERROR", 1: "OK"}.get(code.get("code"), "UNSET"),
                        "status_message": (
                            status.get("message") if isinstance(status, dict) else None
                        ),
                        "attributes": attrs,
                    }
                )
    return spans


def trace_row_from_card(card: Dict[str, Any]) -> Dict[str, Any]:
    """A search card (``{"traces": [...]}`` item) → the ``summarize_trace`` shape."""
    from .dashboard.backends.shapes import otlp_attrs_to_dict  # see spans_from_otlp

    attrs: Dict[str, Any] = {}
    for ss in card.get("spanSets") or []:
        for sp in ss.get("spans") or []:
            attrs.update(otlp_attrs_to_dict(sp.get("attributes")))
    start = int(card.get("startTimeUnixNano") or 0)
    dur = optional_number(card.get("durationMs")) or 0.0
    return {
        "traceId": card.get("traceID") or card.get("traceId"),
        "rootName": card.get("rootTraceName") or "",
        "rootKind": span_kind(card.get("rootTraceName") or "", attrs),
        "service": card.get("rootServiceName") or attrs.get("service.name") or "hermes",
        "startNs": start,
        "endNs": start + int(dur * 1e6),
        "durationMs": dur,
        "spanCount": card.get("spanCount"),
        "model": _sc.first(attrs, _sc.MODEL_KEYS),
        "tokens": optional_number(_sc.first(attrs, _sc.TOTAL_TOKEN_KEYS)),
        "cost": optional_number(attrs.get(_sc.COST_KEY)),
        "error": str(attrs.get("status") or "").lower() == "error" or bool(attrs.get("error")),
        "session": _sc.first(attrs, _sc.SESSION_KEYS),
        "turn": optional_number(attrs.get("hermes.turn.number")),
        "platform": attrs.get("hermes.platform"),
    }


# ── sources ───────────────────────────────────────────────────────────────


class LiveSource:
    """Queries over the SQLite live store."""

    label = "live"

    def __init__(self, store: LiveStore):
        self.store = store

    def traces(
        self, f: argparse.Namespace, start_ns: Optional[int], end_ns: Optional[int]
    ) -> List[Dict[str, Any]]:
        res = self.store.query_traces(
            start_ns=start_ns,
            end_ns=end_ns,
            session=f.session or None,
            status=f.status or None,
            name=f.name or None,
            text=f.text or None,
            limit=f.limit,
            min_duration_ms=f.min_duration or None,
            model=f.model or None,
            tool=f.tool or None,
        )
        return res.get("traces") or []

    def trace_spans(self, trace_id: str) -> List[Dict[str, Any]]:
        return self.store.trace(trace_id).get("spans") or []

    def spans_for_traces(self, ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        return self.store.spans_for_traces(list(ids))

    def sessions(
        self, start_ns: Optional[int], end_ns: Optional[int], limit: int
    ) -> List[Dict[str, Any]]:
        return (
            self.store.sessions(start_ns=start_ns, end_ns=end_ns, limit=limit).get("sessions") or []
        )

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        return self.store.metric_names()

    def metric_buckets(
        self, name: str, start_s: int, end_s: int, bucket_s: int, group_by: Optional[str], agg: str
    ) -> Dict[str, Any]:
        return self.store.metric_buckets(
            name, int(start_s * 1e9), int(end_s * 1e9), bucket_s, group_by, agg
        )

    def logs(
        self, f: argparse.Namespace, start_ns: Optional[int], end_ns: Optional[int]
    ) -> List[Dict[str, Any]]:
        return self.store.query_logs(
            trace_id=f.trace or None,
            session=f.session or None,
            level_min=python_level(f.level, 0) or None,
            logger=f.logger or None,
            text=f.text or None,
            start_ns=start_ns,
            end_ns=end_ns,
            limit=f.limit,
            event_name=getattr(f, "event", None) or None,
            events_only=bool(getattr(f, "events_only", False)),
        )


def _import_backends():
    """The dashboard's ``backends`` package, imported the way ``plugin_api.py`` does.

    The tests replace this function on this module to inject fake adapters;
    ``query_cli`` calls it through the module for that reason.
    """
    try:
        import yaml  # noqa: F401  (the adapters read the config file with it)
    except ImportError:
        raise CliError(
            "backend queries read the config file with pyyaml, which this interpreter lacks. "
            "Run the script with the interpreter Hermes uses (`hermes --version` prints the "
            "install directory; use its venv/bin/python) or `pip install pyyaml`."
        )
    # Relative to this package, never as a top-level ``backends`` module.
    from .dashboard import backends

    return backends


class BackendSource:
    """Queries through a dashboard adapter (Phoenix, Langfuse, Jaeger, …)."""

    def __init__(self, adapter: Any, label: str):
        self.adapter = adapter
        self.label = label

    def traces(
        self, f: argparse.Namespace, start_ns: Optional[int], end_ns: Optional[int]
    ) -> List[Dict[str, Any]]:
        backends = _import_backends()
        attr_equals: Dict[str, str] = {}
        if f.model:
            attr_equals["llm.model_name"] = f.model
        if f.session:
            attr_equals["hermes.session_id"] = f.session
        if f.tool:
            attr_equals["tool.name"] = f.tool
        sf = backends.base.StructuredFilter(
            name_regex=f.name or None,
            attr_equals=attr_equals,
            min_duration_ms=int(f.min_duration) if f.min_duration else None,
            status=f.status or None,
            free_text=f.text or None,
            roots_only=not f.tool,
        )
        start_s = int((start_ns or 0) / 1e9) or int(time.time() - 3600)
        end_s = int((end_ns or 0) / 1e9) or int(time.time())
        res = self.adapter.search(sf, start_s, end_s, f.limit)
        return [trace_row_from_card(c) for c in (res.get("traces") or [])]

    def trace_spans(self, trace_id: str) -> List[Dict[str, Any]]:
        return spans_from_otlp(self.adapter.get_trace(trace_id) or {})

    def spans_for_traces(self, ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        return {t: self.trace_spans(t) for t in ids}

    def sessions(
        self, start_ns: Optional[int], end_ns: Optional[int], limit: int
    ) -> List[Dict[str, Any]]:
        raise CliError(
            f"sessions is a live-store view; {self.label} has no session index (use traces --session)"
        )

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        if not getattr(self.adapter, "supports_metrics", False):
            raise CliError(f"backend {self.label!r} does not serve metrics; use the live store")
        return self.adapter.metric_names(start_s, end_s)

    def metric_buckets(
        self, name: str, start_s: int, end_s: int, bucket_s: int, group_by: Optional[str], agg: str
    ) -> Dict[str, Any]:
        if not getattr(self.adapter, "supports_metrics", False):
            raise CliError(f"backend {self.label!r} does not serve metrics; use the live store")
        return self.adapter.metrics_query(name, start_s, end_s, bucket_s, group_by, agg)

    def logs(
        self, f: argparse.Namespace, start_ns: Optional[int], end_ns: Optional[int]
    ) -> List[Dict[str, Any]]:
        if not getattr(self.adapter, "supports_logs", False):
            raise CliError(f"backend {self.label!r} does not serve logs; use the live store")
        backends = _import_backends()
        lf = backends.base.LogFilter(
            trace_id=f.trace or None,
            session=f.session or None,
            min_level=python_level(f.level, 0),
            logger=f.logger or None,
            text=f.text or None,
            event_name=getattr(f, "event", None) or None,
            events_only=bool(getattr(f, "events_only", False)),
        )
        start_s = int((start_ns or 0) / 1e9) or int(time.time() - 3600)
        end_s = int((end_ns or 0) / 1e9) or int(time.time())
        return self.adapter.logs_search(lf, start_s, end_s, f.limit)


def open_live_store(db_path: Optional[str]) -> LiveStore:
    path = db_path or _default_db_path()
    if not Path(path).exists():
        raise CliError(
            f"no live store at {path}. Run one Hermes turn with the plugin enabled, or set "
            "HERMES_OTEL_LIVE_DB / --db to the file (dashboard_live must be true)."
        )
    return LiveStore(db_path=path)


def resolve_source(args: argparse.Namespace) -> Any:
    """``--source live`` (default) → :class:`LiveSource`, else a backend adapter."""
    wanted = (getattr(args, "source", None) or "live").strip()
    if wanted.lower() == "live":
        return LiveSource(open_live_store(getattr(args, "db", None)))
    backends = _import_backends()
    try:
        adapter, configured, cfg_path, _pin = backends.resolve_adapter(wanted)
    except KeyError as e:
        raise CliError(f"no configured backend named {wanted!r}; configured: {e.args[0]}")
    if adapter is None:
        raise CliError(
            f"backend {wanted!r} has no query adapter; the live store or the backend UI are the options"
        )
    return BackendSource(adapter, wanted)
