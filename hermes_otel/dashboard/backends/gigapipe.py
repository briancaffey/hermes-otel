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
  ``query_range`` at the 15 s rollup step and turned into increases here.
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
from ._prometheus import LOOKBEHIND_S, effective_agg, is_counter
from .base import (
    BackendError,
    LogFilter,
    StructuredFilter,
    bucketize,
    counter_increases,
    finish_log_row,
    http_get_json,
    log_end_ns,
    otlp_attrs_from_dict,
    severity_number_for,
    strictly_older,
    validate_label_name,
    validate_metric_name,
)
from .tempo import TempoAdapter, _dedupe_spans
from .zipkin import coerce_tag

_DEFAULT_PORT = 3100
# Gigapipe serves PromQL from its 15 s rollup; a finer step repeats samples.
_ROLLUP_STEP_S = 15

_CARD_KEYS = frozenset(
    {
        "llm.model_name",
        "llm.provider",
        "llm.api_mode",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.total_tokens",
        "llm.response.finish_reason",
        "llm.response.tool_calls",
        "tool.name",
        "input.value",
        "output.value",
        "llm.output.content",
        "hermes.session_id",
        "hermes.turn.number",
    }
)

# Level names at or above each Python logging floor, for the ``level`` label.
_LEVELS_AT_LEAST = {
    10: ("DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL"),
    20: ("INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL"),
    30: ("WARN", "WARNING", "ERROR", "CRITICAL", "FATAL"),
    40: ("ERROR", "CRITICAL", "FATAL"),
    50: ("CRITICAL", "FATAL"),
}

_ROW_LABELS = frozenset(
    {"level", "trace_id", "span_id", "hermes_session_id", "event_name", "service_name", "flags"}
)


def _origin(url: str) -> str:
    parsed = _urlparse.urlparse(url)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme or 'http'}://{parsed.hostname or 'localhost'}{port}"


def normalize_trace(data: Any) -> Dict[str, Any]:
    """Gigapipe's ``/api/traces/{id}`` JSON → the Tempo ``batches`` shape with
    typed attributes, one batch per ``serviceName``."""
    groups = (data or {}).get("resourceSpans") if isinstance(data, dict) else None
    by_service: Dict[str, List[Dict[str, Any]]] = {}
    for rs in groups or []:
        if not isinstance(rs, dict):
            continue
        scopes = rs.get("instrumentationLibrarySpans") or rs.get("scopeSpans") or []
        for scope in scopes:
            for sp in (scope or {}).get("spans") or []:
                if not isinstance(sp, dict):
                    continue
                attrs = {
                    a["key"]: coerce_tag(str(a["key"]), _unwrap(a.get("value")))
                    for a in sp.get("attributes") or []
                    if isinstance(a, dict) and a.get("key")
                }
                events = []
                for ev in sp.get("events") or []:
                    if isinstance(ev, dict):
                        events.append(
                            {
                                "name": ev.get("name") or "",
                                "timeUnixNano": str(ev.get("timeUnixNano") or 0),
                                "attributes": otlp_attrs_from_dict(
                                    {
                                        a["key"]: coerce_tag(str(a["key"]), _unwrap(a.get("value")))
                                        for a in ev.get("attributes") or []
                                        if isinstance(a, dict) and a.get("key")
                                    }
                                ),
                            }
                        )
                span = {
                    "traceId": str(sp.get("traceId") or sp.get("traceID") or "").lower(),
                    "spanId": sp.get("spanId") or sp.get("spanID"),
                    "parentSpanId": sp.get("parentSpanId") or sp.get("parentSpanID") or None,
                    "name": sp.get("name") or "",
                    "kind": int(sp.get("kind") or 1),
                    "startTimeUnixNano": str(sp.get("startTimeUnixNano") or 0),
                    "endTimeUnixNano": str(sp.get("endTimeUnixNano") or 0),
                    "attributes": otlp_attrs_from_dict(attrs),
                    "status": sp.get("status") or {},
                }
                if events:
                    span["events"] = events
                by_service.setdefault(str(sp.get("serviceName") or ""), []).append(span)
    batches = []
    for service, spans in by_service.items():
        resource = otlp_attrs_from_dict({"service.name": service} if service else {})
        batches.append({"resource": {"attributes": resource}, "scopeSpans": [{"spans": spans}]})
    return {"batches": batches}


def _unwrap(value: Any) -> Any:
    """An OTLP JSON ``AnyValue`` → a Python value with its type (``intValue``
    arrives as a digit string)."""
    if not isinstance(value, dict):
        return value
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return value["intValue"]
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    return value


# Concurrent detail fetches per search page (one per card that needs one).
_DETAIL_WORKERS = 8


@register
class GigapipeAdapter(TempoAdapter):
    handles = frozenset({"gigapipe", "qryn"})

    def __init__(self, cfg: Dict[str, Any]):
        cfg = dict(cfg)
        endpoint = str(cfg.get("endpoint") or "")
        parsed = _urlparse.urlparse(endpoint)
        port = cfg.get("query_port") or parsed.port or _DEFAULT_PORT
        cfg["query_port"] = port
        origin = _origin(f"{parsed.scheme or 'http'}://{parsed.hostname or 'localhost'}:{port}")
        # Every API on the one port unless the entry says otherwise.
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
            all_spans = [
                sp
                for b in detail["batches"]
                for sc in b.get("scopeSpans") or []
                for sp in sc["spans"]
            ]
            all_spans.sort(key=lambda sp: int(sp.get("startTimeUnixNano") or 0))
            ids = {sp.get("spanId") for sp in all_spans}
            if f.roots_only:
                card = next(
                    (
                        sp
                        for sp in all_spans
                        if not sp.get("parentSpanId") or sp.get("parentSpanId") not in ids
                    ),
                    None,
                )
            else:
                # Widened (kind / tool filter): Gigapipe's one-span set names an
                # arbitrary matching span; the card is the first matching span
                # of the trace, as the other adapters show it.
                card = next(
                    (sp for sp in all_spans if self._span_matches(sp, f)),
                    None,
                )
                if card is None:
                    wanted = spans[0].get("spanID") if spans else None
                    card = next((sp for sp in all_spans if sp.get("spanId") == wanted), None)
            if card is None:
                continue
            attrs = {a["key"]: _unwrap(a.get("value")) for a in card.get("attributes") or []}
            keep = {k: v for k, v in attrs.items() if k in _CARD_KEYS}
            keep["name"] = card.get("name") or row.get("rootTraceName") or ""
            if (card.get("status") or {}).get("code") == 2:
                keep["status"] = "error"
            row["spanSets"] = [
                {
                    "spans": [
                        {
                            "spanID": card.get("spanId"),
                            "name": card.get("name") or "",
                            "attributes": otlp_attrs_from_dict(keep),
                        }
                    ],
                    "matched": len(all_spans),
                }
            ]
            row["spanCount"] = len(all_spans)
            row["rootTraceName"] = card.get("name") or row.get("rootTraceName") or ""
            if not row.get("rootServiceName"):
                for b in detail["batches"]:
                    for a in (b.get("resource") or {}).get("attributes") or []:
                        if a.get("key") == "service.name":
                            row["rootServiceName"] = _unwrap(a.get("value"))
        return page

    @staticmethod
    def _span_matches(span: Dict[str, Any], f: StructuredFilter) -> bool:
        """The widened search's structured predicates, re-applied to a detail span."""
        name = str(span.get("name") or "")
        if f.name_prefix and not name.startswith(f.name_prefix):
            return False
        if f.name_regex and name != f.name_regex:
            return False
        attrs = {a.get("key"): _unwrap(a.get("value")) for a in span.get("attributes") or []}
        for k, v in f.attr_equals.items():
            if str(attrs.get(k, "")).lower() != str(v).lower():
                return False
        if f.status == "error" and (span.get("status") or {}).get("code") != 2:
            return False
        return True

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = f"{self.query_url}/api/traces/{trace_id}"
        try:
            raw = http_get_json(url, timeout=20.0)
        except BackendError as exc:
            if exc.kind == "not_found":
                raise BackendError(404, f"Trace {trace_id} not found in Gigapipe", "not_found")
            raise
        data = _dedupe_spans(normalize_trace(raw))
        n = sum(
            len(sc.get("spans") or []) for b in data["batches"] for sc in b.get("scopeSpans") or []
        )
        if n == 0:
            raise BackendError(404, f"Trace {trace_id} not found in Gigapipe", "not_found")
        data["span_count"] = n
        data["truncated"] = False
        return data

    # ── metrics: counters via query_range on the 15 s rollup ──────────

    def metrics_query(
        self,
        name: str,
        start_s: int,
        end_s: int,
        bucket_s: int,
        group_by: Optional[str] = None,
        agg: str = "sum",
    ) -> Dict[str, Any]:
        if not is_counter(name):
            return super().metrics_query(name, start_s, end_s, bucket_s, group_by=group_by, agg=agg)
        validate_metric_name(name)
        label = validate_label_name(group_by)
        label = label.replace(".", "_") if label else None
        start_ns, end_ns = int(start_s) * 1_000_000_000, int(end_s) * 1_000_000_000
        params = {
            "query": name,
            "start": int(start_s) - LOOKBEHIND_S,
            "end": int(end_s),
            "step": _ROLLUP_STEP_S,
        }
        url = f"{self._prometheus()}/api/v1/query_range?{_urlparse.urlencode(params)}"
        data = http_get_json(url, timeout=30.0)
        samples: List[tuple] = []
        labels_of: Dict[str, str] = {}
        for series in ((data.get("data") or {}).get("result")) if isinstance(data, dict) else []:
            metric = series.get("metric") or {}
            ident = "|".join(f"{k}={v}" for k, v in sorted(metric.items()) if k != "__name__")
            labels_of[ident] = str(metric.get(label, "—")) if label else "_"
            for ts, value in series.get("values") or []:
                try:
                    samples.append((int(float(ts)) * 1_000_000_000, float(value), ident))
                except (TypeError, ValueError):
                    continue
        increases = counter_increases(samples, window_start_ns=start_ns)
        points = [(ts, v, labels_of.get(ident, "_")) for ts, v, ident in increases]
        out = bucketize(points, start_ns, end_ns, bucket_s, agg if agg != "last" else "sum")
        out["agg"] = effective_agg(name, agg)
        out["agg_requested"] = agg
        out["name"] = name
        out["cumulative"] = True
        out["promql"] = name
        out["series_start_rule"] = (
            f"a series with no sample in the {LOOKBEHIND_S}s before the window counts its first value"
        )
        return out

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
            band = max((lvl for lvl in _LEVELS_AT_LEAST if lvl <= int(f.min_level)), default=10)
            stages.append('| level=~"' + "|".join(_LEVELS_AT_LEAST[band]) + '"')
        if f.text:
            stages.append(f"|= {_quote(f.text)}")
        return " ".join([self.loki_selector, *stages])

    @staticmethod
    def _record(labels: Dict[str, Any], ts_ns: Any, line: str) -> Dict[str, Any]:
        level = str(labels.get("level") or "INFO").upper()
        row = {
            "level": level,
            "severity_number": severity_number_for(level),
            "logger": "",  # Gigapipe keeps no scope label
            "body": line or "",
            "time_unix_nano": int(ts_ns or 0),
            "trace_id": labels.get("trace_id") or None,
            "span_id": labels.get("span_id") or None,
            "session_id": labels.get("hermes_session_id") or None,
            "event_name": labels.get("event_name") or None,
        }
        return finish_log_row(
            row, {_dotted(k): v for k, v in labels.items() if k not in _ROW_LABELS}
        )

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
