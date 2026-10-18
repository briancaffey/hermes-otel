"""Tempo / LGTM adapter.

Traces come from Tempo. For ``type: lgtm`` (and any Tempo entry that names
them) metrics come from the stack's Prometheus and logs from its Loki, via
the ``PrometheusMetrics`` / ``LokiLogs`` mixins (#194): ``prometheus_url``
and ``loki_url`` default to ports 9090 and 3100 on the Tempo host, which is
the ``grafana/otel-lgtm`` layout.

Tempo is the reference backend — the card + detail response shapes the
rest of the plugin consumes were copied from Tempo's search + trace
JSON. Everything else translates *to* these shapes.

Query language is TraceQL. The adapter composes a ``| select()``
pipeline so each search hit comes back with the attributes the card
renderer needs (model, provider, tokens, input/output, tool name,
status). If the user supplies their own query the raw text is passed
through; ``select()`` is appended only when they haven't added one.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import register
from ._loki import LokiLogs
from ._prometheus import PrometheusMetrics
from .base import (
    FILTER_KEYS,
    BackendAdapter,
    StructuredFilter,
    first_accepted,
    http_get_json,
    not_found,
    query_origin,
)
from .paging import strictly_older_traces, trace_end_bound, trace_page
from .shapes import CARD_ATTRIBUTE_KEYS, dedupe_spans, span_count_of, to_int

_SELECT_PIPELINE = re.compile(r"\|\s*select\s*\(")

# Default Tempo HTTP API port in the otel-lgtm and standalone tempo
# images. Override per deployment by adding ``query_port`` to the
# backend entry in hermes_otel.yaml.
_DEFAULT_TEMPO_QUERY_PORT = 3200
# The otel-lgtm image's Prometheus and Loki ports, used when the entry does
# not name ``prometheus_url`` / ``loki_url`` explicitly.
_DEFAULT_PROMETHEUS_PORT = 9090
_DEFAULT_LOKI_PORT = 3100
_DEFAULT_SEARCH_FETCH = 500

# The ``| select()`` list: the card attributes as span attributes, plus the
# intrinsics the card reads.
_CARD_SELECT_ATTRS = tuple(f".{k}" for k in CARD_ATTRIBUTE_KEYS) + ("status", "name")


def _re_esc(text: str) -> str:
    """Escape ``text`` for a TraceQL regex inside a double-quoted string."""
    out = []
    for ch in text:
        if ch in ".^$*+?()[]{}|\\":
            out.append("\\\\" + ch)
        elif ch == '"':
            out.append('\\"')
        else:
            out.append(ch)
    return "".join(out)


def _esc(s: str) -> str:
    """Quote a string for safe inclusion in a TraceQL string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


_DISABLED = ("off", "none", "disabled", "false")


def card_matched_span(row: Dict[str, Any], span: Dict[str, Any]) -> None:
    """Card a widened row as its span set's ``span`` (a kind filter, #558):
    the span's name, start and duration replace the trace's, so the row does
    not read as a tool that ran as long as the whole turn (#598). A field the
    span lacks (``name`` is an attribute on older builds; a build may leave
    the timing out) keeps the trace's value."""
    name = span.get("name") or next(
        (
            (a.get("value") or {}).get("stringValue")
            for a in span.get("attributes") or []
            if a.get("key") == "name"
        ),
        None,
    )
    if name:
        row["rootTraceName"] = name
    start = to_int(span.get("startTimeUnixNano"))
    if start:
        row["startTimeUnixNano"] = str(start)
    if span.get("durationNanos") is not None:
        row["durationMs"] = to_int(span.get("durationNanos")) // 1_000_000


def _sibling_url(explicit: Any, is_stack: bool, scheme: str, host: str, port: int) -> Optional[str]:
    """An explicit URL wins, ``off`` disables the signal, and an ``lgtm`` entry
    otherwise defaults to the stack port on the Tempo host."""
    if explicit is False or (isinstance(explicit, str) and explicit.strip().lower() in _DISABLED):
        return None
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().rstrip("/")
    if is_stack:
        return f"{scheme}://{host}:{port}"
    return None


@register
class TempoAdapter(PrometheusMetrics, LokiLogs, BackendAdapter):
    handles = frozenset({"lgtm", "tempo"})
    query_lang_label = "TraceQL"
    raw_placeholder = '{ .llm.provider = "openai" }'
    filter_support = {
        "service": "server",
        "name": "server",
        "model": "server",
        "session": "server",
        "tool": "server",
        "min_duration": "server",
        "status_error": "server",
        "status_ok": "server",
        "free_text": "server",
        "raw": "server",
        "roots_only": "server",
    }

    def filter_support_for(self, f: StructuredFilter) -> Dict[str, str]:
        """A raw TraceQL query is sent verbatim and replaces every structured
        predicate (roots-only, status, names, attributes, text): those fields
        are reported as ignored so the page can say so. ``min_duration`` is
        still honoured, client-side on the rows' ``durationMs``."""
        if not (f.raw or "").strip():
            return {}
        out = {k: "none" for k in FILTER_KEYS}
        out["raw"] = "server"
        out["min_duration"] = "client"
        return out

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        self.query_url = query_origin(cfg, _DEFAULT_TEMPO_QUERY_PORT)
        origin = _urlparse.urlparse(self.query_url)
        scheme, host = origin.scheme, origin.hostname or "localhost"
        # Metrics and logs: on for ``lgtm`` (the stack ships Prometheus + Loki
        # next to Tempo) and for any entry that points at them explicitly.
        is_stack = cfg.get("type") == "lgtm"
        self._use_prometheus(
            _sibling_url(
                cfg.get("prometheus_url"), is_stack, scheme, host, _DEFAULT_PROMETHEUS_PORT
            ),
            cfg,
        )
        self._use_loki(
            _sibling_url(cfg.get("loki_url"), is_stack, scheme, host, _DEFAULT_LOKI_PORT), cfg
        )
        # How many traces to ask Tempo for per page (``search_fetch``); the
        # page is cut to ``limit`` after sorting newest-first. See
        # ``_search_params``.
        try:
            self.search_fetch = max(1, int(cfg.get("search_fetch") or _DEFAULT_SEARCH_FETCH))
        except (TypeError, ValueError):
            self.search_fetch = _DEFAULT_SEARCH_FETCH

    def _predicates(self, f: StructuredFilter) -> List[str]:
        predicates: List[str] = []
        if f.roots_only:
            # The root span has no parent in Tempo's nested-set model; asking
            # for it here means every matched span IS the root, so the card
            # attributes come from the root and nothing is dropped afterwards
            # (a client-side check on the matched span's name never matched:
            # Tempo's span sets carry no ``name`` field, #296). A free-text
            # search keeps this predicate too: the text is matched by its own
            # span set (see ``_build_traceql``) so any span of the trace may
            # carry it while the card is still built from the root.
            predicates.append("nestedSetParent < 0")
        if f.service:
            predicates.append(f'resource.service.name = "{_esc(f.service)}"')
        if f.name_prefix:
            # Tempo anchors a regex at both ends (RE2 full match): ``^tool\.``
            # alone matches only the literal ``tool.``, so the prefix needs a
            # trailing ``.*`` to match ``tool.terminal``.
            predicates.append(f'name =~ "^{_re_esc(f.name_prefix)}.*"')
        if f.name_regex:
            predicates.append(f'name =~ "{_esc(f.name_regex)}"')
        if f.status == "error":
            predicates.append("status = error")
        elif f.status == "ok":
            predicates.append("status = ok")
        if f.min_duration_ms and f.min_duration_ms > 0:
            # In the query itself: ``minDuration`` is the legacy tag search's
            # parameter and is not documented next to a TraceQL ``q``.
            predicates.append(f"duration >= {int(f.min_duration_ms)}ms")
        for k, v in f.attr_equals.items():
            predicates.append(f'.{k} = "{_esc(str(v))}"')
        if f.free_text and not f.roots_only:
            predicates.append(self._free_text_predicate(f.free_text))
        return predicates

    @staticmethod
    def _free_text_predicate(text: str) -> str:
        """Any span whose captured input or output mentions the text;
        regex-escaped so a marker with dots or brackets matches literally."""
        pat = ".*" + _re_esc(text) + ".*"
        return f'(span.input.value =~ "{pat}" || span.output.value =~ "{pat}")'

    def _build_traceql(self, f: StructuredFilter, with_select: bool = True) -> str:
        """Compose the effective TraceQL query.

        Structured filter predicates are AND'd into a ``{}``-style
        expression when no raw query is supplied. When a raw query *is*
        supplied we honour it verbatim and only decorate it with
        ``| select()`` (``with_select=False`` leaves that off: the retry
        for Tempo builds that reject the pipeline keeps every predicate).
        """
        user_q = (f.raw or "").strip()
        if user_q:
            base = user_q
        else:
            predicates = self._predicates(f)
            base = "{ " + " && ".join(predicates) + " }" if predicates else "{}"
            if f.free_text and f.roots_only:
                # Two span sets AND'd at the trace level: the trace must have a
                # root matching the structured filters AND some span mentioning
                # the text. With ``spss=1`` the returned span set is the root
                # alone, so the card carries the turn's totals, not an api
                # span's per-call usage (Tempo caps a span set at three spans
                # by default and the root was the one cut, #296).
                base += " && { " + self._free_text_predicate(f.free_text) + " }"

        if not with_select or _SELECT_PIPELINE.search(base):
            return base
        return base + " | select(" + ", ".join(_CARD_SELECT_ATTRS) + ")"

    def _search_params(self, f: StructuredFilter, start_s: int, end_s: int, limit: int, q: str):
        # Tempo bounds the search in whole seconds; the cursor rounds up so
        # the row it was taken from is still inside the window.
        end = trace_end_bound(f, end_s, "s", round_up=True)
        params: Dict[str, Any] = {
            # Tempo stops at ``limit`` matches in whatever order its blocks
            # answer, not the newest ones (verified on Tempo 3.0.3: ``limit=3``
            # over four traces skipped a newer trace for an older one in
            # about half the calls, ``most_recent=true`` included). Over-fetch
            # and take the newest ``limit`` rows here, so keyset paging never
            # loses a trace that sits between two pages.
            "limit": max(int(limit) + 1, int(self.search_fetch)),
            "start": int(start_s),
            "end": end,
            "q": q,
        }
        if not (f.raw or "").strip():
            # One span per set: the root in roots-only mode, the first matched
            # span when widened (one card per trace, as the other backends).
            params["spss"] = 1
        # ``minDuration`` is NOT sent next to a TraceQL ``q``: Tempo ignores it
        # there (verified on grafana/otel-lgtm 0.34, 2026-10-09). Without a raw
        # query the bound is a predicate in the query; with one it is applied
        # to the rows' ``durationMs`` after the fetch (see ``search``).
        return params

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        def fetch(with_select: bool) -> Any:
            q = self._build_traceql(f, with_select=with_select)
            params = self._search_params(f, start_s, end_s, limit, q)
            return http_get_json(f"{self.query_url}/api/search?{_urlparse.urlencode(params)}")

        # Older Tempo builds reject the ``| select()`` pipeline with a 4xx; the
        # same predicates are retried without it (#296).
        data = first_accepted(lambda: fetch(True), lambda: fetch(False))

        result: Dict[str, Any] = data if isinstance(data, dict) else {"traces": []}
        traces = [t for t in (result.get("traces") or []) if isinstance(t, dict)]

        if (f.raw or "").strip() and f.min_duration_ms and f.min_duration_ms > 0:
            # A raw query carries no ``duration >=`` predicate of ours and
            # Tempo ignores ``minDuration`` next to ``q``: bound the rows here.
            floor = float(f.min_duration_ms)
            traces = [t for t in traces if float(t.get("durationMs") or 0) >= floor]

        # Roots-only is in the query (``nestedSetParent < 0``); a raw query is
        # the user's own and is not second-guessed.
        if f.roots_only and f.name_prefix and not (f.raw or "").strip():
            traces = [t for t in traces if (t.get("rootTraceName") or "").startswith(f.name_prefix)]

        for t in traces:
            if not f.roots_only and f.name_prefix:
                # Widened by a kind: the card is the matched span, named and
                # timed like the other backends card it, not the trace's root
                # (#598). Widened by a tool filter alone the row stays the
                # root, its span set the matched span (#558).
                sets = t.get("spanSets") or []
                spans = (sets[0].get("spans") or []) if sets else []
                if spans:
                    card_matched_span(t, spans[0])
            # Tempo prints a trace id without its leading zeros; the live
            # store, Loki and the other backends carry all 32 hex digits, and
            # the Logs sub-tab correlates by the exact string (#296).
            tid = str(t.get("traceID") or "")
            if tid and len(tid) < 32 and all(c in "0123456789abcdefABCDEF" for c in tid):
                t["traceID"] = tid.lower().zfill(32)
            # Tempo's per-service stats carry the whole-trace span count.
            stats = t.get("serviceStats")
            if isinstance(stats, dict) and "spanCount" not in t:
                total = sum(
                    int((s or {}).get("spanCount") or 0)
                    for s in stats.values()
                    if isinstance(s, dict)
                )
                if total:
                    t["spanCount"] = total

        page = trace_page(strictly_older_traces(traces, f), limit)
        # Tempo's own extras (``metrics``) stay next to the page.
        for k, v in result.items():
            if k != "traces":
                page.setdefault(k, v)
        return page

    def trace_url(self, trace_id: str) -> Optional[str]:
        """The trace in Grafana Explore when the entry names a Grafana
        (``ui_url``, e.g. ``http://localhost:3000/explore``); the Tempo
        datasource uid is ``grafana_datasource_uid`` (default ``tempo``, what
        ``grafana/otel-lgtm`` provisions). Tempo itself has no UI, so without
        ``ui_url`` there is no link."""
        base = str(self.cfg.get("ui_url") or "").rstrip("/")
        if not base:
            return None
        uid = str(self.cfg.get("grafana_datasource_uid") or "tempo")
        panes = json.dumps(
            {
                "a": {
                    "datasource": uid,
                    "queries": [
                        {
                            "refId": "A",
                            "datasource": {"type": "tempo", "uid": uid},
                            "queryType": "traceql",
                            "query": trace_id,
                        }
                    ],
                }
            },
            separators=(",", ":"),
        )
        return f"{base}?schemaVersion=1&{_urlparse.urlencode({'panes': panes})}"

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = f"{self.query_url}/api/traces/{trace_id}"
        data = dedupe_spans(http_get_json(url, timeout=20.0))
        if not isinstance(data, dict):
            raise not_found(trace_id, "Tempo")
        n = span_count_of(data.get("batches") or [])
        if n == 0:
            raise not_found(trace_id, "Tempo")
        data["span_count"] = n
        data.setdefault("truncated", False)
        return data
