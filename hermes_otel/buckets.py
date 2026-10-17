"""Time-bucket aggregation shared by the live store and the backend adapters.

A leaf module (no plugin imports) so ``live_store`` and
``dashboard/backends/metrics_math`` can both use it: the backends package
imports every adapter eagerly, so the live store must not import through it
(#518). Every metrics source renders the same chart shape from here.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple


def bucketize(
    points: Iterable[Tuple[int, Optional[float], str]],
    start_ns: int,
    end_ns: int,
    bucket_s: int,
    agg: str = "sum",
) -> Dict[str, Any]:
    """Fold ``(ts_ns, value, label)`` points into the dashboard's bucket shape.

    ``points`` counts every point given, including one outside the window or
    one whose value is ``None`` (a live-store row whose value did not parse);
    only the ones with a value inside the window land in a series. An empty
    label is the ungrouped series ``"_"``.
    """
    bucket_ns = max(1, int(bucket_s)) * 1_000_000_000
    start_ns = int(start_ns) - int(start_ns) % bucket_ns
    n = max(1, int((int(end_ns) - start_ns) // bucket_ns) + 1)
    series: Dict[str, List[List[float]]] = {}
    count = 0
    for ts, value, label in points:
        count += 1
        if value is None:
            continue
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
