"""Metric arithmetic shared by the adapters (#403): native name → plugin
instrument name, cumulative counter samples → increases, and points → the
live store's bucket shape, so every source renders the same chart."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

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
