"""Metric arithmetic shared by the adapters (#403): native name → plugin
instrument name, one series identity, cumulative counter samples →
increases, and points → the live store's bucket shape, so every source
renders the same chart. :func:`fold_samples` is the whole epilogue of a
raw-sample ``metrics_query`` (#515); the adapters only fetch and label."""

from __future__ import annotations

from typing import Any, Container, Dict, Iterable, List, Mapping, Optional, Tuple

# Units the OTLP-to-Prometheus translation appends to a name; ``{token}``
# style units are dropped by it, so no ``_tokens`` here.
_PROM_UNIT_SUFFIXES = (
    "_milliseconds",
    "_seconds",
    "_bytes",
    "_ratio",
    "_percent",
    "_watts",
)


_UNDERSCORE_SEGMENTS = ("gen_ai", "prompt_cache")

# A counter series with no sample in the five minutes before the window is a
# new one (a fresh process): its first value is an increase. Hermes exports
# every 60 s, so a live gateway always has a sample inside the look-behind.
# Every adapter that folds raw samples fetches this much history (#296).
LOOKBEHIND_S = 300


def otlp_metric_name(native: str) -> str:
    """The plugin's dotted instrument name behind a backend's native spelling,
    so the UI can match its curated panels on every source (#284).

    ``hermes_token_usage_total`` → ``hermes.token.usage``;
    ``hermes_tool_duration_milliseconds_sum`` → ``hermes.tool.duration``;
    a dotted name passes through. Best effort: the Prometheus translation
    appends ``_total`` to counters and the unit to everything with one.
    """
    n = str(native or "")
    if "." in n:
        base = n
        for suffix in (".sum", ".count", ".bucket", ".min", ".max"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        return base
    base = n
    for suffix in ("_total", "_sum", "_count", "_bucket"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    for suffix in _PROM_UNIT_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    # Name segments that carry an underscore of their own (``gen_ai``,
    # ``prompt_cache``) are kept whole; every other underscore was a dot.
    toks = base.split("_")
    out: List[str] = []
    i = 0
    while i < len(toks):
        pair = f"{toks[i]}_{toks[i + 1]}" if i + 1 < len(toks) else None
        if pair in _UNDERSCORE_SEGMENTS:
            out.append(pair)
            i += 2
        else:
            out.append(toks[i])
            i += 1
    return ".".join(out)


def bucketize(
    points: Iterable[Tuple[int, float, str]],
    start_ns: int,
    end_ns: int,
    bucket_s: int,
    agg: str = "sum",
) -> Dict[str, Any]:
    """Fold ``(ts_ns, value, label)`` points into the live store's bucket shape.

    Shared by the backend adapters so every source renders identically.
    """
    bucket_ns = max(1, int(bucket_s)) * 1_000_000_000
    start_ns = int(start_ns) - int(start_ns) % bucket_ns
    n = max(1, int((int(end_ns) - start_ns) // bucket_ns) + 1)
    series: Dict[str, List[List[float]]] = {}
    count = 0
    for ts, value, label in points:
        count += 1
        idx = int((int(ts) - start_ns) // bucket_ns)
        if idx < 0 or idx >= n:
            continue
        series.setdefault(label or "_", [[] for _ in range(n)])[idx].append(float(value))

    def reduce(vals: List[float]) -> Optional[float]:
        if not vals:
            return None
        if agg == "count":
            return float(len(vals))
        if agg == "avg":
            return sum(vals) / len(vals)
        if agg == "max":
            return max(vals)
        if agg == "last":
            return vals[-1]
        return sum(vals)

    return {
        "agg": agg,
        "bucketS": int(bucket_s),
        "buckets": [start_ns + i * bucket_ns for i in range(n)],
        "series": {label: [reduce(v) for v in vals] for label, vals in series.items()},
        "points": count,
    }


def counter_increases(
    samples: Iterable[Tuple[int, float, str]],
    *,
    started_in_window: Optional[Iterable[str]] = None,
    window_start_ns: Optional[int] = None,
) -> List[Tuple[int, float, str]]:
    """Turn cumulative counter samples into per-sample increases.

    OTLP counters arrive cumulative (the exporter's temporality); a bucketed
    "tokens per 15 s" chart needs the increase between consecutive samples of
    the same series. A drop (process restart) counts the new value in full.

    A series' first sample is an increase too when the series started inside
    the window: every ``hermes -z`` run is a new process whose counters start
    at zero, so its one or two cumulative samples carry the whole turn.
    Callers say which series those are, either by name (``started_in_window``,
    for stores that record the counter's start time) or by giving
    ``window_start_ns`` and passing samples from before the window as well: a
    series whose first sample lies at or after the window start is new, one
    with an earlier sample is not (and its earlier value is the baseline).
    Without either, the first sample is a baseline only (the old behaviour).
    """
    new = set(started_in_window or ())
    last: Dict[str, float] = {}
    out: List[Tuple[int, float, str]] = []
    for ts, value, label in sorted(samples, key=lambda p: (p[2], p[0])):
        prev = last.get(label)
        if prev is None:
            if label in new or (window_start_ns is not None and int(ts) >= int(window_start_ns)):
                out.append((ts, value, label))
        else:
            out.append((ts, value if value < prev else value - prev, label))
        last[label] = value
    return out


def series_ident(
    labels: Mapping[str, Any], *, drop: Container[str] = (), instance: Optional[Any] = None
) -> str:
    """One series' identity while its counter is folded: the labels as sorted
    ``k=v`` pairs joined with ``|``, plus the exporting process (``instance``)
    when the store keeps it apart from the labels: a counter is per process,
    and two processes folded into one series would read the second one's first
    value as "no increase". The ident never leaves the adapter."""
    parts = [f"{k}={v}" for k, v in sorted(labels.items()) if k not in drop]
    if instance is not None:
        parts.append(f"instance={instance}")
    return "|".join(parts)


def increases_per_process(
    samples: Iterable[Tuple[int, float, str, Any]], *, window_start_ns: int
) -> List[Tuple[int, float, str]]:
    """``(ts_ns, value, label, instance)`` cumulative samples → ``(ts_ns,
    increase, label)``: each process's counter is its own series for
    :func:`counter_increases`, the chart label is what comes back out."""
    keyed = [(ts, value, f"{label}\x00{instance}") for ts, value, label, instance in samples]
    return [
        (ts, value, ident.split("\x00", 1)[0])
        for ts, value, ident in counter_increases(keyed, window_start_ns=window_start_ns)
    ]


def fold_samples(
    samples: Iterable[Tuple[int, float, str]],
    labels_of: Mapping[str, str],
    start_ns: int,
    end_ns: int,
    bucket_s: int,
    agg: str,
    *,
    name: str,
    cumulative: bool,
    started_in_window: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Raw ``(ts_ns, value, ident)`` samples → the ``metrics_query`` answer.

    A cumulative series is turned into increases first: a series that starts
    inside the window counts its first value, told by ``started_in_window``
    when the store records the counter's start, else by its first sample lying
    inside the window (the caller fetched :data:`LOOKBEHIND_S` of history, the
    rule the answer states as ``series_start_rule``). The points, labelled
    through ``labels_of``, are folded onto the shared bucket grid; ``last`` of
    a counter's increases has no per-bucket meaning, so it is folded with
    ``sum`` and reported as ``sum`` while ``agg_requested`` keeps the ask. A
    gauge answer carries neither ``cumulative`` nor ``series_start_rule``.
    """
    if cumulative:
        if started_in_window is not None:
            samples = counter_increases(samples, started_in_window=started_in_window)
        else:
            samples = counter_increases(samples, window_start_ns=start_ns)
    points = [(ts, v, labels_of.get(ident, "_")) for ts, v, ident in samples]
    fold = "sum" if (cumulative and agg == "last") else agg
    out = bucketize(points, start_ns, end_ns, bucket_s, fold)
    out["agg_requested"] = agg
    out["name"] = name
    if cumulative:
        out["cumulative"] = True
        if started_in_window is None:
            out["series_start_rule"] = (
                f"a series with no sample in the {LOOKBEHIND_S}s before the window "
                "counts its first value"
            )
    return out
