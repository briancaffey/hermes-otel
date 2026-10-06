"""Prometheus HTTP API helpers for the metrics half of Grafana-stack adapters (#194).

Prometheus (or Mimir, which speaks the same API) is where the LGTM
collector's OTLP metrics land. OTLP instrument names arrive translated:
``hermes.token.usage`` becomes ``hermes_token_usage_total``, histograms
grow ``_bucket`` / ``_sum`` / ``_count`` and dotted attribute keys turn
into underscored labels. The adapter shows those Prometheus names as the
instruments, the same way the OpenObserve adapter shows its stream names.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from .base import bucketize, http_get_json, validate_label_name, validate_metric_name

_HISTOGRAM_INTERNAL_SUFFIXES = ("_bucket",)

# Counters carry the OTLP-to-Prometheus ``_total`` suffix (histogram ``_sum``
# and ``_count`` series are cumulative too); everything else is treated as a
# gauge. Only these five aggregates exist on the dashboard side.
_COUNTER_SUFFIXES = ("_total", "_sum", "_count")
_SPACE_AGG = {"sum": "sum", "count": "count", "avg": "avg", "max": "max", "last": "sum"}

# Without ``metrics_match`` the instrument list is the plugin's own namespaces
# (plus the host/GPU ones it mirrors), not every series Prometheus scrapes.
DEFAULT_MATCH = '{__name__=~"(hermes|gen_ai|process|system|hw)_.*"}'


def is_counter(name: str) -> bool:
    return name.endswith(_COUNTER_SUFFIXES)


def effective_agg(name: str, agg: str) -> str:
    """The aggregate the PromQL really computes: ``last`` on a counter has no
    per-bucket meaning, so it is the increase summed (reported as ``sum``)."""
    if agg == "last" and is_counter(name):
        return "sum"
    return agg


def promql_for(name: str, bucket_s: int, group_by: Optional[str], agg: str) -> str:
    """The PromQL for one dashboard query.

    Counters: the increase over each bucket, then the space aggregate across
    series. Gauges: the space aggregate of the last sample in each bucket.
    ``group_by`` is an OTLP attribute key; Prometheus stores it with dots
    replaced by underscores. Both names are validated first: they are spliced
    into the query text (#296).
    """
    validate_metric_name(name)
    label = validate_label_name(group_by)
    by = f" by ({label.replace('.', '_')})" if label else ""
    space = _SPACE_AGG.get(agg, "sum")
    window = f"[{max(1, int(bucket_s))}s]"
    if is_counter(name):
        return f"{space}{by} (increase({name}{window}))"
    return f"{space}{by} (last_over_time({name}{window}))"


def metric_names(
    base: str,
    start_s: int,
    end_s: int,
    match: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """``[{name}]`` for series present in the window (``match`` narrows it;
    the plugin's namespaces by default)."""
    params: List[tuple] = [("start", int(start_s)), ("end", int(end_s))]
    params.append(("match[]", match or DEFAULT_MATCH))
    url = f"{base}/api/v1/label/__name__/values?{_urlparse.urlencode(params)}"
    data = http_get_json(url, headers=headers, timeout=30.0)
    names = (data.get("data") if isinstance(data, dict) else None) or []
    return [
        {"name": n}
        for n in sorted(n for n in names if isinstance(n, str))
        if not n.endswith(_HISTOGRAM_INTERNAL_SUFFIXES)
    ]


def metrics_query(
    base: str,
    name: str,
    start_s: int,
    end_s: int,
    bucket_s: int,
    group_by: Optional[str] = None,
    agg: str = "sum",
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """``query_range`` at ``step = bucket_s`` folded onto the shared bucket grid."""
    label = group_by.replace(".", "_") if group_by else None
    params = {
        "query": promql_for(name, bucket_s, group_by, agg),
        "start": int(start_s),
        "end": int(end_s),
        "step": max(1, int(bucket_s)),
    }
    url = f"{base}/api/v1/query_range?{_urlparse.urlencode(params)}"
    data = http_get_json(url, headers=headers, timeout=30.0)
    points = []
    for series in ((data.get("data") or {}).get("result")) if isinstance(data, dict) else []:
        series_label = (series.get("metric") or {}).get(label, "—") if label else "_"
        for ts, value in series.get("values") or []:
            try:
                points.append((int(float(ts)) * 1_000_000_000, float(value), series_label))
            except (TypeError, ValueError):
                continue
    # Prometheus evaluated the aggregate per step already; one point per
    # bucket folded with ``sum`` reproduces it on the shared grid.
    out = bucketize(
        points, int(start_s) * 1_000_000_000, int(end_s) * 1_000_000_000, bucket_s, "sum"
    )
    out["agg"] = effective_agg(name, agg)
    out["agg_requested"] = agg
    out["name"] = name
    out["cumulative"] = is_counter(name)
    out["promql"] = params["query"]
    return out
