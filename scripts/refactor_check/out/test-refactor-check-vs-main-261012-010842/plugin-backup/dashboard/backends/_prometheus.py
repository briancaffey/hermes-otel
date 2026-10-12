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

from .base import (
    bucketize,
    counter_increases,
    http_get_json,
    validate_label_name,
    validate_metric_name,
)

_HISTOGRAM_INTERNAL_SUFFIXES = ("_bucket",)

# Counters carry the OTLP-to-Prometheus ``_total`` suffix (histogram ``_sum``
# and ``_count`` series are cumulative too); everything else is treated as a
# gauge. Only these five aggregates exist on the dashboard side.
_COUNTER_SUFFIXES = ("_total", "_sum", "_count")
# A counter series with no sample in the five minutes before the window is a
# new one (a fresh process): its first value is an increase. Hermes exports
# every 60 s, so a live gateway always has a sample inside the look-behind.
LOOKBEHIND_S = 300
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
    """Counters as raw samples, gauges as ``query_range`` at ``step = bucket_s``,
    both folded onto the shared bucket grid.

    Counters are read as raw cumulative samples (with :data:`LOOKBEHIND_S` of
    history) and turned into increases by :func:`counter_increases`, because
    PromQL's ``increase()`` never counts a series' first sample: a one-shot
    ``hermes -z`` run exports its counters once or twice with the same value,
    so ``increase()`` reads 0 for every turn it ever ran (#296). Gauges keep
    the aggregate PromQL.
    """
    label = group_by.replace(".", "_") if group_by else None
    start_ns, end_ns = int(start_s) * 1_000_000_000, int(end_s) * 1_000_000_000
    if is_counter(name):
        validate_metric_name(name)
        validate_label_name(group_by)
        # A range-vector instant query returns every raw sample with its own
        # timestamp. ``query_range`` at ``step = bucket_s`` does not: it reads
        # the latest sample within the staleness delta at each evaluation
        # instant, so a one-shot process whose single sample lands after the
        # last instant (the grid is ``start + k·step``, not aligned to ``end``)
        # is never seen, and a sample is reported at the instant rather than
        # when it was written, which can move a series' first value into the
        # window and count it twice (#296).
        span_s = int(end_s) - int(start_s) + max(LOOKBEHIND_S, int(bucket_s))
        raw = {"query": f"{name}[{max(1, span_s)}s]", "time": int(end_s)}
        data = http_get_json(
            f"{base}/api/v1/query?{_urlparse.urlencode(raw)}", headers=headers, timeout=30.0
        )
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
    out = bucketize(points, start_ns, end_ns, bucket_s, "sum")
    out["agg"] = agg
    out["agg_requested"] = agg
    out["name"] = name
    out["cumulative"] = False
    out["promql"] = params["query"]
    return out
