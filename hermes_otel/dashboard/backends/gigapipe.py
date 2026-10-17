"""Gigapipe (formerly qryn) adapter — the Tempo, Prometheus and Loki adapters
on one port, with the four places Gigapipe answers differently (#370).

Gigapipe serves Tempo's ``/api/search`` + ``/api/traces/{id}``, Prometheus'
``/api/v1/*`` and Loki's ``/loki/api/v1/*`` from the port it ingests on, so
this is the LGTM adapter with every sibling URL defaulting to the entry's
own origin. Verified against ``ghcr.io/metrico/gigapipe`` v5.5.3 on
2026-10-11, these are the differences the subclass absorbs:

* ``/api/search`` with a TraceQL ``q`` returns one row per trace but an
  empty attribute list from ``| select(...)``, so the card's model, tokens
  and session come from the trace detail's matched span instead.
* ``/api/traces/{id}`` is pre-1.0 OTLP JSON: ``resourceSpans`` holding
  ``instrumentationLibrarySpans``, every attribute a ``stringValue``, no
  ``kind``, the resource reduced to ``collector=gigapipe`` and the service on
  each span's ``serviceName``. It is reshaped into the ``batches`` /
  ``scopeSpans`` the frontend consumes, values coerced back.
* The Prometheus route answers a range-vector instant query
  (``name[3600s]``) with an empty result, so counters are read with
  ``query_range`` at the 15 s rollup step (the reader's ``counter_samples``
  hook) and turned into increases by the shared fold.
* Loki streams carry every attribute as a label with dots turned into
  underscores, a ``level`` label instead of ``severity_text`` /
  ``severity_number``, and no scope (logger) label, so the LogQL stages and
  the row mapping are Gigapipe's own; the logger filter and the loggers
  list cannot be served.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib import parse as _urlparse

from . import register
from ._attrs import dotted as _dotted
from ._loki import MAX_RANGE_S, _quote
from .base import (
    BackendError,
    LogColumns,
    LogFilter,
    StructuredFilter,
    http_get_json,
    log_row,
    not_found,
    query_origin,
)
from .metrics_math import LOOKBEHIND_S
from .paging import log_end_ns, strictly_older
from .severity import level_names_at_least
from .shapes import (
    SpanRow,
    card_from_trace,
    coerce_tag,
    dedupe_spans,
    otlp_attrs_to_dict,
    otlp_event,
    otlp_kind,
    span_count_of,
    to_int,
    trace_detail,
)
from .tempo import TempoAdapter

_DEFAULT_PORT = 3100
# Gigapipe serves PromQL from its 15 s rollup; a finer step repeats samples.
_ROLLUP_STEP_S = 15

# Gigapipe's label set: a ``level`` label, no scope and no severity number.
_LOG_COLUMNS = LogColumns(
    logger="",
    severity_number="",
    session_id="hermes_session_id",
    drop=("service_name", "flags"),
    attr_key=_dotted,
)


def _typed_attrs(items: Any) -> Dict[str, Any]:
    """Gigapipe's attribute list, every value a ``stringValue``, coerced back
    to its type (numbers, booleans) except on id-like keys."""
    return {k: coerce_tag(str(k), v) for k, v in otlp_attrs_to_dict(items).items()}


def _row(sp: Dict[str, Any]) -> SpanRow:
    """A span of Gigapipe's pre-1.0 OTLP JSON as a row, values coerced back,
    the service from the span's own ``serviceName``."""
    return SpanRow(
        str(sp.get("traceId") or sp.get("traceID") or "").lower(),
        sp.get("spanId") or sp.get("spanID"),
        sp.get("parentSpanId") or sp.get("parentSpanID"),
        str(sp.get("name") or ""),
        start_ns=to_int(sp.get("startTimeUnixNano")),
        end_ns=to_int(sp.get("endTimeUnixNano")),
        attrs=_typed_attrs(sp.get("attributes")),
        service=str(sp.get("serviceName") or ""),
        kind=otlp_kind(sp.get("kind")),
        status=sp.get("status") or {},
        events=[
            otlp_event(ev.get("name"), ev.get("timeUnixNano"), _typed_attrs(ev.get("attributes")))
            for ev in sp.get("events") or []
            if isinstance(ev, dict)
        ],
    )


def normalize_trace(data: Any) -> Dict[str, Any]:
    """Gigapipe's ``/api/traces/{id}`` JSON → the detail every adapter answers
    (see :func:`trace_detail`), typed attributes, one batch per ``serviceName``."""
    groups = (data or {}).get("resourceSpans") if isinstance(data, dict) else None
    return trace_detail(
        [
            _row(sp)
            for rs in groups or []
            if isinstance(rs, dict)
            for scope in (rs.get("instrumentationLibrarySpans") or rs.get("scopeSpans") or [])
            for sp in (scope or {}).get("spans") or []
            if isinstance(sp, dict)
        ]
    )


def _detail_rows(batches: List[Dict[str, Any]]) -> List[SpanRow]:
    """A detail's spans (see :func:`normalize_trace`) back as rows, by start:
    what a search card is picked from."""
    rows = [
        SpanRow(
            sp.get("traceId"),
            sp.get("spanId"),
            sp.get("parentSpanId"),
            str(sp.get("name") or ""),
            start_ns=to_int(sp.get("startTimeUnixNano")),
            end_ns=to_int(sp.get("endTimeUnixNano")),
            attrs=otlp_attrs_to_dict(sp.get("attributes")),
            status=sp.get("status") or {},
        )
        for b in batches
        for sc in b.get("scopeSpans") or []
        for sp in sc["spans"]
    ]
    rows.sort(key=lambda r: r.start_ns)
    return rows


# Concurrent detail fetches per search page (one per card that needs one).
_DETAIL_WORKERS = 8


def _query_range_rollup(
    base: str, name: str, start_s: int, end_s: int, bucket_s: int, headers: Optional[Dict[str, str]]
) -> Any:
    """A counter's raw samples as ``query_range`` at the 15 s rollup step, from
    ``LOOKBEHIND_S`` before the window: Gigapipe answers the range-vector
    instant query the Prometheus reader uses with an empty result."""
    params = {
        "query": name,
        "start": int(start_s) - LOOKBEHIND_S,
        "end": int(end_s),
        "step": _ROLLUP_STEP_S,
    }
    url = f"{base}/api/v1/query_range?{_urlparse.urlencode(params)}"
    return http_get_json(url, headers=headers, timeout=30.0)


@register
class GigapipeAdapter(TempoAdapter):
    handles = frozenset({"gigapipe", "qryn"})
    # Counters via query_range on the 15 s rollup (see ``_query_range_rollup``).
    _counter_samples = staticmethod(_query_range_rollup)

    def __init__(self, cfg: Dict[str, Any]):
        cfg = dict(cfg)
        # Every API on the one port (the ingest port) unless the entry says otherwise.
        origin = query_origin(cfg, _DEFAULT_PORT, endpoint_port="fallback")
        cfg["query_port"] = _urlparse.urlparse(origin).port
        cfg.setdefault("prometheus_url", origin)
        cfg.setdefault("loki_url", origin)
        super().__init__(cfg)

    def trace_url(self, trace_id: str) -> Optional[str]:
        # qryn-view at the origin has no documented deep link; ``ui_url`` offers one.
        return super().trace_url(trace_id) if self.cfg.get("ui_url") else None

    # ── traces ────────────────────────────────────────────────────────

    def _details_for(self, trace_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """One detail per card, fetched concurrently (bounded).

        A build that ignores ``select()`` lists cards without attributes, so
        each card costs a round trip; fetched one after another a page of 50
        cost 50 round trips (#413). A card whose detail fails is left as the
        list returned it."""
        out: Dict[str, Dict[str, Any]] = {}

        def one(tid: str) -> Tuple[str, Optional[Dict[str, Any]]]:
            try:
                return tid, self.get_trace(tid)
            except BackendError:
                return tid, None

        if len(trace_ids) <= 1:
            results = [one(t) for t in trace_ids]
        else:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(
                max_workers=min(_DETAIL_WORKERS, len(trace_ids)),
                thread_name_prefix="hermes-otel-gigapipe",
            ) as pool:
                results = list(pool.map(one, trace_ids))
        for tid, detail in results:
            if detail is not None:
                out[tid] = detail
        return out

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        page = super().search(f, start_s, end_s, limit)
        pending = []
        for row in page.get("traces") or []:
            sets = row.get("spanSets") or []
            spans = (sets[0].get("spans") if sets else None) or []
            if spans and spans[0].get("attributes"):
                continue  # a build that honours select(): nothing to add
            pending.append((row, spans))
        details = self._details_for([row["traceID"] for row, _spans in pending])
        for row, spans in pending:
            detail = details.get(row["traceID"])
            if detail is None:
                continue
            rows = _detail_rows(detail["batches"])
            # Roots-only ran on the server (``nestedSetParent < 0`` with every
            # predicate): the root is the card. Widened (kind / tool filter),
            # Gigapipe's one-span set names an arbitrary matching span: the
            # card is the first matching span of the trace, as the other
            # adapters show it, else the span the set named.
            card = card_from_trace(f, rows, check_all=True, server_roots=True)
            if card is None:
                wanted = spans[0].get("spanID") if spans else None
                named = next((r for r in rows if r.span_id == wanted), None)
                card = named.card(len(rows)) if named is not None else None
            if card is None:
                continue
            row.update({k: card[k] for k in ("spanSets", "spanCount", "rootTraceName")})
            if not row.get("rootServiceName"):
                for b in detail["batches"]:
                    service = otlp_attrs_to_dict((b.get("resource") or {}).get("attributes")).get(
                        "service.name"
                    )
                    if service:
                        row["rootServiceName"] = service
        return page

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = f"{self.query_url}/api/traces/{trace_id}"
        try:
            raw = http_get_json(url, timeout=20.0)
        except BackendError as exc:
            if exc.kind == "not_found":
                raise not_found(trace_id, "Gigapipe")
            raise
        data = dedupe_spans(normalize_trace(raw))
        n = span_count_of(data["batches"])
        if n == 0:
            raise not_found(trace_id, "Gigapipe")
        data["span_count"] = n  # after the dedupe
        return data

    # ── logs: Gigapipe's label set ────────────────────────────────────

    def _logql(self, f: LogFilter) -> str:
        stages: List[str] = []
        if f.trace_id:
            stages.append(f"| trace_id={_quote(f.trace_id)}")
        if f.span_id:
            stages.append(f"| span_id={_quote(f.span_id)}")
        if f.event_name:
            stages.append(f"| event_name={_quote(f.event_name)}")
        elif f.events_only:
            stages.append('| event_name!=""')
        if f.session:
            stages.append(f"| hermes_session_id={_quote(f.session)}")
        if f.min_level:
            stages.append('| level=~"' + "|".join(level_names_at_least(f.min_level)) + '"')
        if f.text:
            stages.append(f"|= {_quote(f.text)}")
        return " ".join([self.loki_selector, *stages])

    @staticmethod
    def _record(labels: Dict[str, Any], ts_ns: Any, line: str) -> Dict[str, Any]:
        return log_row({**labels, "body": line}, _LOG_COLUMNS, time_ns=int(ts_ns or 0))

    def logs_search(
        self, f: LogFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        if f.logger:
            return []  # no scope label to filter on
        end_ns = log_end_ns(end_s, f)
        start_ns = max(int(start_s) * 1_000_000_000, end_ns - MAX_RANGE_S * 1_000_000_000)
        # Gigapipe bounds the range at whole seconds (a nanosecond cursor as
        # ``end`` dropped the rest of that second on v5.5.3, and pages lost
        # lines); round the bound up and let strictly_older() make the cut.
        query_end_ns = ((end_ns + 999_999_999) // 1_000_000_000) * 1_000_000_000
        params = {
            "query": self._logql(f),
            "start": start_ns,
            "end": query_end_ns,
            "limit": int(limit),
            "direction": "backward",
        }
        url = f"{self._loki()}/loki/api/v1/query_range?{_urlparse.urlencode(params)}"
        data = http_get_json(url, timeout=30.0)
        out: List[Dict[str, Any]] = []
        for stream in ((data.get("data") or {}).get("result")) if isinstance(data, dict) else []:
            labels = stream.get("stream") or {}
            for ts_ns, line in stream.get("values") or []:
                out.append(self._record(labels, ts_ns, line))
        out.sort(key=lambda r: r["time_unix_nano"], reverse=True)
        return strictly_older(out, f)[: int(limit)]

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        return []  # no scope label in Gigapipe's streams
