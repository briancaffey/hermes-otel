"""Langfuse adapter — public REST API at ``/api/public/``.

Auth is HTTP Basic with ``public_key`` + ``secret_key`` (generate both
in the Langfuse UI under Settings → API Keys). Both are required;
there's no unauthenticated path for self-hosted Langfuse.

Langfuse models trace observations differently from OTel: a ``Trace``
has many ``Observation`` items of type ``SPAN``, ``GENERATION``,
``EVENT``, etc. We map ``GENERATION`` → the LLM call span shape the
rest of the plugin expects (model, provider, usage tokens) and every
other type to a generic internal span.

Two servers answer two APIs and the adapter picks one strategy per request
(#516): :class:`_LangfuseTracesApi` for Langfuse 3 (the trace endpoints),
:class:`_LangfuseObservationsApi` for Langfuse 4 in ``events_only`` mode (the
v2 observations list plus the v2 metrics API); :class:`_LangfuseApi` holds
what they share. The v3 trace list filters on ``name``, ``userId``, ``sessionId``,
``release``, ``version``, ``tags`` and a time window, and pages with
``page=``. Nothing else of the search bar reaches it: the model, tool and
status fields and the free text are declared unsupported in
``filter_support`` rather than silently dropped (#294); the minimum
duration is applied to the rows the list returned, fetching further pages
until the page is full or ``_MAX_PAGES`` is reached.

Search enrichment: the Langfuse list endpoint doesn't include
per-trace observations, so the trace list shows trace-level metadata
only (name, timestamp, latency, user/session). Full observation
attributes appear in the detail view.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib import parse as _urlparse

from hermes_otel.coerce import optional_number

from . import default_service_name, register
from .base import (
    BackendAdapter,
    BackendError,
    ConfigError,
    StructuredFilter,
    basic_auth,
    http_get_json,
    not_found,
    otlp_attrs_from_dict,
    otlp_status,
    parse_kv_tokens,
    query_origin,
    register_cache,
    require_credential,
    resolve_env_or_literal,
)
from .paging import strictly_older_traces, trace_end_bound, trace_page
from .shapes import attach_root, card_row, otlp_batches, otlp_span
from .timeconv import iso_to_ns, ns_from_s, ns_to_rfc3339

_DEFAULT_LANGFUSE_PORT = 3000
# How far the client-side duration filter walks through the list.
_MAX_PAGES = 5
# The public API refuses ``limit`` above 100.
_MAX_PAGE_SIZE = 100
# Root-span attributes the card shows, read from the list item's
# ``metadata.attributes``: the card keys Langfuse's metadata carries (see
# ``shapes.CARD_ATTRIBUTE_KEYS``) plus the semconv model keys, in the order
# the card reads them.
_CARD_ATTRIBUTE_KEYS = (
    "llm.model_name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "llm.provider",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
    "hermes.session_id",
    "hermes.turn.number",
    "tool.name",
)
# OpenInference span kinds the exporter sets, as Langfuse types them.
_OBSERVATION_TYPES = {"tool": "TOOL", "api": "GENERATION", "llm": "GENERATION", "agent": "AGENT"}

# Which API a server answers (``v3`` / ``v4``), probed once per query URL;
# ``query_api: v3|v4`` on the entry pins it.
_API_CACHE: Dict[str, str] = register_cache({})
_V2_OBSERVATIONS = "/api/public/v2/observations"
_V2_METRICS = "/api/public/v2/metrics"
_V4_FILTER_SUPPORT = {
    "service": "none",
    "name": "client",  # ``name=`` is exact; a prefix is checked on the rows
    "model": "none",
    "session": "server",  # ``sessionId=``
    "tool": "none",
    "min_duration": "client",  # ``latency`` on the rows
    "status_error": "server",  # ``level=ERROR`` on the listed observation
    "status_ok": "none",
    "free_text": "none",
    "raw": "server",  # ``k=v`` query parameters of the v2 list
    "roots_only": "server",  # the root is the AGENT observation (``isRootObservation``)
}


def _span_name(obs: Dict[str, Any]) -> str:
    name = str(obs.get("name") or obs.get("type") or "")
    if obs.get("type") == "TOOL" and name and not name.startswith("tool."):
        return f"tool.{name}"
    return name


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


# One observation's attributes for the span tree. An OTLP-ingested
# observation (every one the plugin exports) keeps the span's attributes
# verbatim under ``metadata.attributes`` (verified on Langfuse 3 against the
# 2026-10 image, #246); those are the attributes every other backend returns
# and the ones the detail header reads (model, the turn's token roll-up on
# the ``agent`` root, tools, cost), so they are lifted first-class. Langfuse's
# own derived fields (``model``, ``usage``, ``input``/``output``) are the
# fallback for observations written through its SDKs, which carry no
# ``metadata.attributes``. ``usage`` is skipped for an OTLP observation: it
# is derived from the same ``gen_ai.usage.*`` attributes for a GENERATION and
# zero-filled for every other type, and a zero on the root stopped the header
# from summing the ``api.*`` spans (#346).
def obs_to_card_attrs(
    obs: Dict[str, Any], span_attrs: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    out: Dict[str, Any] = {k: v for k, v in (span_attrs or {}).items() if v is not None}
    otlp = bool(out)
    out["name"] = obs.get("name") or ""
    t = obs.get("type")
    if isinstance(t, str):
        out["langfuse.type"] = t
    if obs.get("model"):
        out.setdefault("llm.model_name", obs["model"])
    # Langfuse doesn't model "provider" as a distinct field — fall
    # through to modelParameters / metadata.
    params = obs.get("modelParameters")
    if isinstance(params, dict):
        for k, v in params.items():
            out.setdefault(f"llm.parameters.{k}", v)

    usage = obs.get("usage") or {}
    if not otlp and isinstance(usage, dict):
        if usage.get("input") is not None:
            out["gen_ai.usage.input_tokens"] = usage["input"]
        if usage.get("output") is not None:
            out["gen_ai.usage.output_tokens"] = usage["output"]
        if usage.get("total") is not None:
            out["gen_ai.usage.total_tokens"] = usage["total"]

    # input / output come through as Langfuse JSON objects — serialize
    # as strings to line up with the way other backends render them.
    inp = obs.get("input")
    if inp is not None:
        out.setdefault("input.value", _as_text(inp))
    outp = obs.get("output")
    if outp is not None:
        out.setdefault("output.value", _as_text(outp))

    level = obs.get("level")
    if level:
        # Langfuse levels: DEFAULT, DEBUG, WARNING, ERROR. Map ERROR
        # into the shared ``status`` attribute used by the card.
        out["status"] = "error" if level == "ERROR" else "ok"
    return out


def _span_attributes_of(obs: Dict[str, Any]) -> Dict[str, Any]:
    """The exported span's attributes Langfuse keeps under ``metadata.attributes``
    for an OTLP-ingested observation; ``{}`` for one written through an SDK."""
    meta = obs.get("metadata") if isinstance(obs.get("metadata"), dict) else {}
    attrs = meta.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def _observation_span(trace_id: str, obs: Dict[str, Any], attrs: Dict[str, Any]) -> Dict[str, Any]:
    """One observation as a span of the detail tree. Langfuse drops the
    exporter's ``tool.`` prefix from a TOOL observation's name; the tree
    uses the name the exporter gave the span, as every other backend does.
    Langfuse's types are semantic, not transport kinds: every span is
    INTERNAL (the default)."""
    return otlp_span(
        trace_id,
        obs.get("id"),
        obs.get("parentObservationId"),
        _span_name(obs),
        start_ns=iso_to_ns(obs.get("startTime")) or 0,
        end_ns=iso_to_ns(obs.get("endTime")) or 0,
        attrs=attrs,
        status=otlp_status(
            "error" if obs.get("level") == "ERROR" else "ok", obs.get("statusMessage") or ""
        ),
    )


class _LangfuseApi:
    """What both APIs share: the adapter (credentials, query URL, the service
    the spans are shown under), the list decode, the paging loop over an
    observations list and one observation as a card row. ``filter_support``
    is what the API changes about the adapter's declared table."""

    filter_support: Dict[str, str] = {}

    def __init__(self, adapter: "LangfuseAdapter"):
        self.adapter = adapter

    def _list(self, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """One page of a ``/api/public`` list: its ``data`` rows that are objects."""
        url = f"{self.adapter.query_url}{path}?" + _urlparse.urlencode(params, doseq=True)
        data = http_get_json(url, headers=self.adapter._headers(), timeout=15.0)
        items = data.get("data") if isinstance(data, dict) else None
        return [o for o in (items if isinstance(items, list) else []) if isinstance(o, dict)]

    def _observation_pages(
        self,
        path: str,
        params: Dict[str, Any],
        f: StructuredFilter,
        start_s: int,
        end_s: int,
        limit: int,
    ) -> Iterator[Tuple[Dict[str, Any], str, Dict[str, Any]]]:
        """The observations of ``path`` in the start window, one card per
        trace, as ``(observation, card_name, row)``; ``params`` are the list's
        own filters. Pages until one more row than ``limit`` is in hand (for
        ``has_more``) or a page comes back short. The name prefix is checked
        with the ``tool.`` prefix Langfuse drops put back; unless the kind
        filter widened the search to every span, a nested root (a sub-agent's
        AGENT observation) is not a turn and is skipped."""
        prefix = f.name_prefix or ""
        widened = bool(not f.roots_only and prefix)
        want = int(limit) + 1
        yielded = 0
        seen: set = set()
        for page in range(1, _MAX_PAGES + 1):
            items = self._list(
                path,
                {
                    "page": page,
                    "limit": _MAX_PAGE_SIZE,
                    "fromStartTime": ns_to_rfc3339(ns_from_s(start_s), digits=0),
                    "toStartTime": ns_to_rfc3339(ns_from_s(end_s), digits=0),
                    **params,
                },
            )
            for o in items:
                if not widened and o.get("isRootObservation") is False:
                    continue
                name = str(o.get("name") or "")
                card_name = f"tool.{name}" if o.get("type") == "TOOL" else name
                if prefix and not card_name.startswith(prefix):
                    continue
                trace_id = o.get("traceId")
                if not trace_id or trace_id in seen:
                    continue
                row = self._observation_row(o, trace_id, card_name, f)
                if row is None:
                    continue
                seen.add(trace_id)
                yielded += 1
                yield o, card_name, row
            if yielded >= want or len(items) < _MAX_PAGE_SIZE:
                break

    def _observation_row(
        self, o: Dict[str, Any], trace_id: str, card_name: str, f: StructuredFilter
    ) -> Optional[Dict[str, Any]]:
        start_ns = iso_to_ns(o.get("startTime"))
        latency = optional_number(o.get("latency"))
        duration_ms = int(latency * 1000) if latency is not None else 0
        if f.min_duration_ms and duration_ms < int(f.min_duration_ms):
            return None
        attrs: Dict[str, Any] = {"name": card_name}
        span_attrs = _span_attributes_of(o)
        for k in _CARD_ATTRIBUTE_KEYS:
            if span_attrs.get(k) not in (None, ""):
                attrs[k] = span_attrs[k]
        if o.get("type") == "TOOL":
            attrs.setdefault("tool.name", o.get("name") or "")
        if o.get("model"):
            attrs.setdefault("llm.model_name", o["model"])
        for key, field_name in (("input.value", "input"), ("output.value", "output")):
            v = o.get(field_name)
            if v not in (None, "") and key not in attrs:
                attrs[key] = v if isinstance(v, str) else json.dumps(v)
        if str(o.get("level") or "").upper() == "ERROR":
            attrs["status"] = "error"
        return card_row(
            trace_id,
            span_id=o.get("id"),
            name=card_name,
            attrs=attrs,
            start_ns=start_ns or 0,
            duration_ms=duration_ms,
            service=self.adapter.default_service,
            # One observation per row: the trace's count is not known here
            # (the v4 list joins it from the metrics API afterwards).
        )


class _LangfuseTracesApi(_LangfuseApi):
    """Langfuse 3: the trace list and the trace detail with its observations."""

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        if not f.roots_only and f.name_prefix and not (f.raw or "").strip():
            return self._search_observations(f, start_s, end_s, limit)
        want = int(limit) + 1
        traces: List[Dict[str, Any]] = []
        # One page beyond the limit is enough for has_more unless rows are
        # filtered out here (duration, name prefix) or the page is capped
        # (the public API refuses more than 100); then walk further pages.
        page_size = min(want, _MAX_PAGE_SIZE)
        for page in range(1, _MAX_PAGES + 1):
            try:
                items = self._list(
                    "/api/public/traces", self._list_params(f, start_s, end_s, page_size, page)
                )
            except BackendError as exc:
                # Langfuse v4 in events_only mode answers 404 with a sentence
                # about the mode: that is a deployment choice, not an outage
                # (#246), so it is reported as configuration.
                if "events_only" in str(exc.detail):
                    raise ConfigError(
                        "This Langfuse runs v4 in events_only mode, which has no "
                        "/api/public/traces for the dashboard to query (#246). Use the "
                        "Live source, or a Langfuse with the tracing API enabled."
                    )
                raise
            for t in items:
                row = self._trace_row(t, f)
                if row is not None:
                    traces.append(row)
            if len(traces) >= want or len(items) < page_size:
                break
            if not (f.min_duration_ms or f.name_prefix) and page_size >= want:
                break
        return trace_page(strictly_older_traces(traces, f), limit)

    def _list_params(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int, page: int = 1
    ) -> Dict[str, Any]:
        end_ns = int(end_s) * 1_000_000_000
        if f.before_ns:
            end_ns = min(end_ns, int(f.before_ns))
        params: Dict[str, Any] = {
            "fromTimestamp": ns_to_rfc3339(ns_from_s(start_s), digits=0),
            # Round up so the row the cursor was taken from is inside the
            # window; strictly_older_traces() then cuts exactly.
            "toTimestamp": ns_to_rfc3339(ns_from_s(-(-end_ns // 1_000_000_000)), digits=0),
            "limit": int(limit),
            "page": int(page),
        }
        # Langfuse supports these first-class: name, userId, sessionId,
        # release, version, tags (latter multi-value).
        for k in ("name", "userId", "sessionId", "release", "version"):
            v = f.attr_equals.get(k)
            if v:
                params[k] = v
        if f.attr_equals.get("hermes.session_id") and "sessionId" not in params:
            params["sessionId"] = f.attr_equals["hermes.session_id"]
        if f.name_regex and "name" not in params:
            # The native query field: Langfuse filters name by exact match.
            params["name"] = f.name_regex
        # The raw k=v pairs are the native escape hatch, but the page and the
        # page size stay the route's: ``page=`` would otherwise silently
        # replace the cursor.
        raw = parse_kv_tokens(f.raw)
        raw.pop("page", None)
        raw.pop("limit", None)
        params.update(raw)
        return params

    # The kind filter widens the search to every span: Langfuse keeps those as
    # observations, typed by the exporter's OpenInference kind. A ``tool.``
    # prefix is the TOOL type (Langfuse drops the prefix from the name),
    # ``api.`` / ``llm.`` are GENERATIONs with the name kept, ``agent`` is the
    # AGENT type; any other prefix is matched on the name alone (#246).
    def _search_observations(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> Dict[str, Any]:
        prefix = f.name_prefix or ""
        obs_type = _OBSERVATION_TYPES.get(prefix.rstrip(".")) if prefix else None
        rows = [
            row
            for _o, _name, row in self._observation_pages(
                "/api/public/observations",
                {"type": obs_type} if obs_type else {},
                f,
                start_s,
                trace_end_bound(f, end_s, "s", round_up=True),
                limit,
            )
        ]
        return trace_page(strictly_older_traces(rows, f), limit)

    def _trace_row(self, t: Dict[str, Any], f: StructuredFilter) -> Optional[Dict[str, Any]]:
        trace_id = t.get("id")
        if not trace_id:
            return None
        name = t.get("name") or ""
        if f.name_prefix and not name.startswith(f.name_prefix):
            return None
        start_ns = iso_to_ns(t.get("timestamp"))
        # The public API documents ``latency`` in seconds.
        latency = optional_number(t.get("latency"))
        duration_ms = int(latency * 1000) if latency is not None else 0
        if f.min_duration_ms and duration_ms < int(f.min_duration_ms):
            return None

        attrs: Dict[str, Any] = {"name": name}
        # The list item's ``metadata.attributes`` are the root span's
        # attributes (verified on Langfuse 3 against the 2026-10 image): the
        # model, the turn's token totals and the session id come from there,
        # so the card reads like the other backends' (#246).
        root_attrs = _span_attributes_of(t)
        for k in _CARD_ATTRIBUTE_KEYS:
            if root_attrs.get(k) not in (None, ""):
                attrs[k] = root_attrs[k]
        for key, field_name in (("input.value", "input"), ("output.value", "output")):
            v = t.get(field_name)
            if v not in (None, "") and key not in attrs:
                attrs[key] = v if isinstance(v, str) else json.dumps(v)
        # The trace list carries the trace's total cost; the token counts of
        # the observations are summed on the root (above).
        total_cost = optional_number(t.get("totalCost"))
        if total_cost is not None and total_cost > 0:
            attrs["hermes.cost.usage"] = total_cost
        observations = t.get("observations")
        span_count = len(observations) if isinstance(observations, list) else None
        for k in ("userId", "sessionId", "release", "version"):
            if t.get(k):
                attrs[f"langfuse.{k}"] = t[k]
        if t.get("tags"):
            attrs["langfuse.tags"] = t["tags"]

        return card_row(
            trace_id,
            # The list carries no observation ids; the trace id stands in
            # (see the docs' shape notes).
            span_id=trace_id,
            name=name,
            attrs=attrs,
            start_ns=start_ns or 0,
            duration_ms=duration_ms,
            service=self.adapter.default_service,
            span_count=span_count,
        )

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = f"{self.adapter.query_url}/api/public/traces/{trace_id}"
        data = http_get_json(url, headers=self.adapter._headers(), timeout=20.0)
        if not isinstance(data, dict) or not data.get("id"):
            raise not_found(trace_id, "Langfuse")

        observations = [o for o in (data.get("observations") or []) if isinstance(o, dict)]
        spans_otlp: List[Dict[str, Any]] = []
        for obs in observations:
            span_attrs = _span_attributes_of(obs)
            attrs = obs_to_card_attrs(obs, span_attrs)
            # Carry through any free-form metadata so it's still visible
            # in the attribute table; the span's own attributes were lifted
            # above and are not repeated as one ``metadata.attributes`` blob.
            metadata = obs.get("metadata") or {}
            if isinstance(metadata, dict):
                for k, v in metadata.items():
                    if k == "attributes" and span_attrs:
                        continue
                    attrs[f"metadata.{k}"] = v
            spans_otlp.append(_observation_span(trace_id, obs, attrs))

        trace_attrs: Dict[str, Any] = {"langfuse.trace_id": trace_id}
        for k in ("userId", "sessionId", "release", "version"):
            if data.get(k):
                trace_attrs[f"langfuse.{k}"] = data[k]
        if data.get("input") is not None:
            trace_attrs["input.value"] = _as_text(data["input"])
        if data.get("output") is not None:
            trace_attrs["output.value"] = _as_text(data["output"])

        # An OTLP-ingested trace keeps its real root observation (the plugin's
        # ``agent`` span) and the trace record's facts join its attributes
        # where it has none; otherwise a root built from the trace record
        # holds the tree together.
        trace_start = iso_to_ns(data.get("timestamp"))
        trace_end = max((iso_to_ns(o.get("endTime")) or 0 for o in observations), default=0)
        attach_root(
            spans_otlp,
            trace_id,
            extra_root_attrs=trace_attrs,
            synthetic_name=data.get("name") or "trace",
            start_ns=trace_start or 0,
            end_ns=trace_end or trace_start or 0,
            reason=(
                "Langfuse holds no single root observation for this trace; "
                "built from the trace record"
            ),
        )
        return {
            "batches": otlp_batches({self.adapter.default_service: spans_otlp}),
            "span_count": len(observations),
            "truncated": False,
        }


class _LangfuseObservationsApi(_LangfuseApi):
    """Langfuse 4 in its default ``events_only`` mode has no
    ``/api/public/traces`` (404 with a sentence about the mode, #246). What it
    keeps: the v2 observations list (one row per observation with ``traceId``,
    ``parentObservationId``, ``isRootObservation``, ``type``, ``name``,
    ``startTime``/``endTime``, ``latency`` in seconds, ``level``, ``sessionId``;
    no input/output/usage/metadata) and the v2 metrics API, which answers token
    totals, cost, observation count and the model per trace."""

    filter_support = _V4_FILTER_SUPPORT

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        """The trace list from the v2 observations: root observations (the
        AGENT type, one per Hermes turn) in the window, or, widened by the kind
        filter, the typed observations with one card per trace. Token totals,
        cost, model and the span count join from the metrics API."""
        prefix = f.name_prefix or ""
        widened = bool(not f.roots_only and prefix)
        obs_type = _OBSERVATION_TYPES.get(prefix.rstrip(".")) if widened else "AGENT"
        end_s_eff = trace_end_bound(f, end_s, "s", round_up=True)
        session = f.attr_equals.get("hermes.session_id") or f.attr_equals.get("sessionId")
        params: Dict[str, Any] = {}
        if obs_type:
            params["type"] = obs_type
        if f.name_regex:
            params["name"] = f.name_regex
        if session:
            params["sessionId"] = session
        if f.status == "error":
            params["level"] = "ERROR"
        raw = parse_kv_tokens(f.raw)
        raw.pop("page", None)
        raw.pop("limit", None)
        params.update(raw)
        rows: List[Dict[str, Any]] = []
        card_attrs: Dict[str, Dict[str, Any]] = {}
        for o, card_name, row in self._observation_pages(
            _V2_OBSERVATIONS, params, f, start_s, end_s_eff, limit
        ):
            attrs: Dict[str, Any] = {"name": card_name}
            if o.get("sessionId"):
                attrs["hermes.session_id"] = o["sessionId"]
            if o.get("type") == "TOOL":
                attrs["tool.name"] = str(o.get("name") or "")
            if str(o.get("level") or "").upper() == "ERROR":
                attrs["status"] = "error"
            rows.append(row)
            card_attrs[row["traceID"]] = attrs
        page_out = trace_page(strictly_older_traces(rows, f), limit)
        facts = self._trace_facts(
            [t["traceID"] for t in page_out["traces"]], int(start_s), end_s_eff
        )
        for t in page_out["traces"]:
            attrs = dict(card_attrs.get(t["traceID"], {}))
            tf = facts.get(t["traceID"]) or {}
            attrs.update(self._facts_to_attrs(tf))
            if tf.get("count"):
                t["spanCount"] = int(tf["count"])
            t["spanSets"][0]["spans"][0]["attributes"] = otlp_attrs_from_dict(attrs)
        return page_out

    def _trace_facts(
        self, trace_ids: List[str], start_s: int, end_s: int
    ) -> Dict[str, Dict[str, Any]]:
        """Per trace, from the v2 metrics API: token totals, cost, observation
        count and the model (``providedModelName``), which the observation rows
        do not carry. One request per page; a failure of the metrics route
        leaves the cards without those numbers rather than failing the list."""
        if not trace_ids:
            return {}
        query = {
            "view": "observations",
            # children start after their root; give the window a margin
            "fromTimestamp": ns_to_rfc3339(ns_from_s(max(0, int(start_s) - 60)), digits=0),
            "toTimestamp": ns_to_rfc3339(ns_from_s(int(end_s) + 3600), digits=0),
            "metrics": [
                {"measure": "totalTokens", "aggregation": "sum"},
                {"measure": "inputTokens", "aggregation": "sum"},
                {"measure": "outputTokens", "aggregation": "sum"},
                {"measure": "totalCost", "aggregation": "sum"},
                {"measure": "count", "aggregation": "count"},
            ],
            "dimensions": [{"field": "traceId"}, {"field": "providedModelName"}],
            "filters": [
                {
                    "column": "traceId",
                    "operator": "any of",
                    "value": list(trace_ids),
                    "type": "stringOptions",
                }
            ],
            "orderBy": [{"field": "sum_totalTokens", "direction": "desc"}],
            "config": {"row_limit": max(100, len(trace_ids) * 8)},
        }
        url = f"{self.adapter.query_url}{_V2_METRICS}?query=" + _urlparse.quote(json.dumps(query))
        try:
            data = http_get_json(url, headers=self.adapter._headers(), timeout=20.0)
        except BackendError as exc:
            if exc.kind in ("auth", "config"):
                raise
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        rows = data.get("data") if isinstance(data, dict) else None
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or not r.get("traceId"):
                continue
            facts = out.setdefault(
                str(r["traceId"]), {"tokens": 0, "input": 0, "output": 0, "cost": 0.0, "count": 0}
            )
            for key, field in (
                ("tokens", "sum_totalTokens"),
                ("input", "sum_inputTokens"),
                ("output", "sum_outputTokens"),
                ("count", "count_count"),
            ):
                v = optional_number(r.get(field))
                if v is not None:
                    facts[key] += int(v)
            cost = optional_number(r.get("sum_totalCost"))
            if cost is not None:
                facts["cost"] += cost
            if r.get("providedModelName") and not facts.get("model"):
                facts["model"] = str(r["providedModelName"])
        return out

    @staticmethod
    def _facts_to_attrs(facts: Dict[str, Any]) -> Dict[str, Any]:
        attrs: Dict[str, Any] = {}
        if facts.get("tokens"):
            attrs["gen_ai.usage.total_tokens"] = facts["tokens"]
            attrs["gen_ai.usage.input_tokens"] = facts.get("input", 0)
            attrs["gen_ai.usage.output_tokens"] = facts.get("output", 0)
        if facts.get("cost"):
            attrs["hermes.cost.usage"] = float(facts["cost"])
        if facts.get("model"):
            attrs["llm.model_name"] = facts["model"]
        return attrs

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        """The trace's observations as spans. The v2 rows carry no input,
        output or per-observation usage; the trace's totals from the metrics
        API go on the root so the header can show them."""
        observations: List[Dict[str, Any]] = []
        for page in range(1, _MAX_PAGES + 1):
            items = self._list(
                _V2_OBSERVATIONS, {"traceId": trace_id, "limit": _MAX_PAGE_SIZE, "page": page}
            )
            observations.extend(items)
            if len(items) < _MAX_PAGE_SIZE:
                break
        if not observations:
            raise not_found(trace_id, "Langfuse")
        spans_otlp: List[Dict[str, Any]] = []
        for obs in observations:
            attrs: Dict[str, Any] = {"name": _span_name(obs)}
            if isinstance(obs.get("type"), str):
                attrs["langfuse.type"] = obs["type"]
            if obs.get("type") == "TOOL" and obs.get("name"):
                attrs["tool.name"] = obs["name"]
            for key in ("sessionId", "userId", "environment", "version"):
                if obs.get(key):
                    attrs[f"langfuse.{key}"] = obs[key]
            if obs.get("sessionId"):
                attrs["hermes.session_id"] = obs["sessionId"]
            spans_otlp.append(_observation_span(trace_id, obs, attrs))
        starts = [
            int(sp["startTimeUnixNano"]) for sp in spans_otlp if sp["startTimeUnixNano"] != "0"
        ]
        ends = [int(sp["endTimeUnixNano"]) for sp in spans_otlp if sp["endTimeUnixNano"] != "0"]

        def totals() -> Dict[str, Any]:
            # Only a real root carries the metrics totals; one request, and
            # none when the observations have no timestamps to window by.
            if not (starts and ends):
                return {}
            facts = self._trace_facts(
                [trace_id], min(starts) // 1_000_000_000, max(ends) // 1_000_000_000
            )
            return self._facts_to_attrs(facts.get(trace_id) or {})

        attach_root(
            spans_otlp,
            trace_id,
            extra_root_attrs=totals,
            synthetic_name="trace",
            start_ns=min(starts) if starts else 0,
            end_ns=max(ends) if ends else 0,
            reason="Langfuse holds no single root observation for this trace",
            synthetic_attrs={"langfuse.trace_id": trace_id},
        )
        return {
            "batches": otlp_batches({self.adapter.default_service: spans_otlp}),
            "span_count": len(observations),
            "truncated": False,
        }


@register
class LangfuseAdapter(BackendAdapter):
    handles = frozenset({"langfuse"})
    query_lang_label = "Langfuse params (k=v k=v)"
    raw_placeholder = "userId=user_123 name=chat"
    filter_support = {
        "service": "none",
        "name": "client",  # the list has an exact ``name=``; a prefix is checked on the rows
        "model": "none",
        "session": "server",
        "tool": "none",
        "min_duration": "client",
        "status_error": "none",
        "status_ok": "none",
        "free_text": "none",
        "raw": "server",
        "roots_only": "none",  # the list is already one row per trace
    }

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        # Langfuse self-hosted typically lives on :3000 (Next.js); the base URL
        # is the UI's, so its own port wins.
        endpoint = cfg.get("endpoint") or cfg.get("base_url") or ""
        self.query_url = query_origin(
            {**cfg, "endpoint": endpoint}, _DEFAULT_LANGFUSE_PORT, endpoint_port="first"
        )
        self.public_key = resolve_env_or_literal(cfg, "public_key", "public_key_env")
        self.secret_key = resolve_env_or_literal(cfg, "secret_key", "secret_key_env")
        # Langfuse keeps no resource attributes: the service a trace is shown
        # under is the one the plugin exports (the entry's ``service_name``,
        # else the plugin's ``service.name``, else ``hermes-agent``).
        self.default_service = default_service_name(cfg)
        self._project_id_cache: Optional[str] = None
        api = str(cfg.get("query_api") or "auto").strip().lower()
        self.query_api = api if api in ("v3", "v4") else "auto"

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_api"] = self._known_api() or "auto"
        base["auth_required"] = not (self.public_key and self.secret_key)
        if base["auth_required"]:
            base["auth_hint"] = (
                "Langfuse requires public_key + secret_key on the "
                "langfuse backend entry. Generate both in the Langfuse "
                "UI under Settings → API Keys."
            )
        return base

    def _headers(self) -> Dict[str, str]:
        require_credential(
            self.public_key and self.secret_key,
            "Langfuse requires public_key + secret_key. Set them "
            "(or *_env variants) on the langfuse backend entry.",
        )
        return basic_auth(self.public_key, self.secret_key)

    # ── Which API ────────────────────────────────────────────────────

    def _known_api(self) -> Optional[str]:
        """The API pinned by ``query_api`` or cached by an earlier probe; None
        when neither has decided yet."""
        return self.query_api if self.query_api != "auto" else _API_CACHE.get(self.query_url)

    def _api(self) -> str:
        """``v3`` (the trace endpoints answer) or ``v4`` (``events_only``: the
        trace list is a 404 naming the mode). Probed once per query URL; an
        unreachable or unauthenticated server is reported, not guessed."""
        known = self._known_api()
        if known:
            return known
        api = "v3"
        try:
            http_get_json(
                f"{self.query_url}/api/public/traces?limit=1", headers=self._headers(), timeout=10.0
            )
        except BackendError as exc:
            if exc.kind == "not_found" and "events_only" in str(exc.detail):
                api = "v4"
            elif exc.kind != "not_found":
                raise
        _API_CACHE[self.query_url] = api
        return api

    def _impl(self, *, probe: bool = True) -> _LangfuseApi:
        """The strategy for the server's API. A search or a detail probes an
        undecided server; ``/status`` and the filter table never do (an
        unreachable Langfuse used to cost every status poll a 10 s timeout,
        #413) and answer as v3 until a search decides."""
        api = self._api() if probe else self._known_api()
        return _LangfuseObservationsApi(self) if api == "v4" else _LangfuseTracesApi(self)

    def filter_support_for(self, f: Optional[StructuredFilter] = None) -> Dict[str, str]:
        """The v2 observations list honours more of the search bar than the
        v3 trace list (session, roots, an error level) and less of the rest."""
        return dict(self._impl(probe=False).filter_support)

    # ── Public API ───────────────────────────────────────────────────

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        return self._impl().search(f, start_s, end_s, limit)

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        return self._impl().get_trace(trace_id)

    def _project_id(self) -> Optional[str]:
        if self._project_id_cache:
            return self._project_id_cache
        try:
            data = http_get_json(
                f"{self.query_url}/api/public/projects", headers=self._headers(), timeout=10.0
            )
        except BackendError as exc:
            if exc.kind in ("auth", "config"):
                raise  # a wrong key is an error, not "no link"
            return None
        except Exception:
            return None
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list) and items and isinstance(items[0], dict) and items[0].get("id"):
            self._project_id_cache = str(items[0]["id"])
        return self._project_id_cache

    def trace_url(self, trace_id: str) -> Optional[str]:
        pid = self._project_id()
        return f"{self.query_url}/project/{pid}/traces/{trace_id}" if pid else None
