"""Prometheus HTTP API helpers for the metrics half of Grafana-stack adapters (#194).

Prometheus (or Mimir, which speaks the same API) is where the LGTM
collector's OTLP metrics land. OTLP instrument names arrive translated:
``hermes.token.usage`` becomes ``hermes_token_usage_total``, histograms
grow ``_bucket`` / ``_sum`` / ``_count`` and dotted attribute keys turn
into underscored labels. The adapter shows those Prometheus names as the
instruments, the same way the OpenObserve adapter shows its stream names.

:class:`PrometheusMetrics` is the metrics half of an adapter whose store
speaks this API (Tempo / LGTM, Gigapipe, VictoriaMetrics, GreptimeDB's
metric names): the adapter sets the read URL once, the mixin answers
``metric_names`` / ``metrics_query`` with the adapter's own ``_headers()``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from urllib import parse as _urlparse

from .base import ConfigError, http_get_json, validate_label_name, validate_metric_name
from .metrics_math import LOOKBEHIND_S, bucketize, fold_samples, series_ident

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


# How a counter's raw samples are fetched: ``(base, name, start_s, end_s,
# bucket_s, headers)`` → the Prometheus ``matrix`` JSON.
SampleFetch = Callable[[str, str, int, int, int, Optional[Dict[str, str]]], Any]


def _instant_range_vector(
    base: str, name: str, start_s: int, end_s: int, bucket_s: int, headers: Optional[Dict[str, str]]
) -> Any:
    """Every raw sample of ``name`` from :data:`LOOKBEHIND_S` before the window
    to its end, as one range-vector instant query.

    A range-vector instant query returns every raw sample with its own
    timestamp. ``query_range`` at ``step = bucket_s`` does not: it reads the
    latest sample within the staleness delta at each evaluation instant, so a
    one-shot process whose single sample lands after the last instant (the
    grid is ``start + k·step``, not aligned to ``end``) is never seen, and a
    sample is reported at the instant rather than when it was written, which
    can move a series' first value into the window and count it twice (#296).
    """
    span_s = int(end_s) - int(start_s) + max(LOOKBEHIND_S, int(bucket_s))
    raw = {"query": f"{name}[{max(1, span_s)}s]", "time": int(end_s)}
    return http_get_json(
        f"{base}/api/v1/query?{_urlparse.urlencode(raw)}", headers=headers, timeout=30.0
    )


def metrics_query(
    base: str,
    name: str,
    start_s: int,
    end_s: int,
    bucket_s: int,
    group_by: Optional[str] = None,
    agg: str = "sum",
    headers: Optional[Dict[str, str]] = None,
    counter_samples: SampleFetch = _instant_range_vector,
) -> Dict[str, Any]:
    """Counters as raw samples, gauges as ``query_range`` at ``step = bucket_s``,
    both folded onto the shared bucket grid.

    Counters are read as raw cumulative samples (with :data:`LOOKBEHIND_S` of
    history, by ``counter_samples``: the range-vector instant query unless the
    store cannot answer one) and turned into increases by :func:`fold_samples`,
    because PromQL's ``increase()`` never counts a series' first sample: a
    one-shot ``hermes -z`` run exports its counters once or twice with the
    same value, so ``increase()`` reads 0 for every turn it ever ran (#296).
    Gauges keep the aggregate PromQL.
    """
    label = group_by.replace(".", "_") if group_by else None
    start_ns, end_ns = int(start_s) * 1_000_000_000, int(end_s) * 1_000_000_000
    if is_counter(name):
        validate_metric_name(name)
        validate_label_name(group_by)
        data = counter_samples(base, name, start_s, end_s, bucket_s, headers)
        samples: List[tuple] = []
        labels_of: Dict[str, str] = {}
        for series in ((data.get("data") or {}).get("result")) if isinstance(data, dict) else []:
            metric = series.get("metric") or {}
            ident = series_ident(metric, drop=("__name__",))
            labels_of[ident] = str(metric.get(label, "—")) if label else "_"
            for ts, value in series.get("values") or []:
                try:
                    samples.append((int(float(ts)) * 1_000_000_000, float(value), ident))
                except (TypeError, ValueError):
                    continue
        out = fold_samples(
            samples, labels_of, start_ns, end_ns, bucket_s, agg, name=name, cumulative=True
        )
        out["promql"] = name
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


class PrometheusMetrics:
    """The metrics half of an adapter whose store speaks the Prometheus API:
    the adapter calls :meth:`_use_prometheus` once it knows the read URL
    (``None`` when the entry has no metrics store), the reads above then run
    with the adapter's own ``_headers()`` and ``/status`` names the URL.
    ``_counter_samples`` is how a counter's raw samples are fetched; Gigapipe
    swaps it."""

    prometheus_url: Optional[str] = None
    metrics_match: Optional[str] = None
    _counter_samples = staticmethod(_instant_range_vector)

    def _use_prometheus(self, url: Optional[str], cfg: Dict[str, Any]) -> None:
        self.prometheus_url = url
        self.metrics_match = cfg.get("metrics_match") or None
        self.supports_metrics = url is not None

    def _prometheus(self) -> str:
        if not self.prometheus_url:
            raise ConfigError("This backend has no prometheus_url configured")
        return self.prometheus_url

    def status(self) -> Dict[str, Any]:
        out = super().status()
        if self.prometheus_url:
            out["prometheus_url"] = self.prometheus_url
        return out

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        return metric_names(
            self._prometheus(), start_s, end_s, self.metrics_match, headers=self._headers()
        )

    def metrics_query(
        self,
        name: str,
        start_s: int,
        end_s: int,
        bucket_s: int,
        group_by: Optional[str] = None,
        agg: str = "sum",
    ) -> Dict[str, Any]:
        return metrics_query(
            self._prometheus(),
            name,
            start_s,
            end_s,
            bucket_s,
            group_by=group_by,
            agg=agg,
            headers=self._headers(),
            counter_samples=self._counter_samples,
        )
