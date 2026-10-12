"""Keyset paging for trace and log searches (#403).

Every page is newest-first and its cursor is a timestamp: "the next page" is
the rows strictly older than the oldest row shown, which survives new rows
arriving where an offset would not. Backends bound time at their own
precision (ms, us), so these helpers widen the query by one unit and cut the
page exactly at the nanosecond afterwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from .base import LogFilter, StructuredFilter

# ── Trace pages ─────────────────────────────────────────────────────────


def strictly_older_traces(
    traces: List[Dict[str, Any]], f: "StructuredFilter"
) -> List[Dict[str, Any]]:
    """Drop trace rows that started at or after the cursor (backends bound time
    at their own precision; this keeps the page exact)."""
    if not f.before_ns:
        return traces
    cut = int(f.before_ns)
    return [t for t in traces if int(t.get("startTimeUnixNano") or 0) < cut]


def trace_page(traces: List[Dict[str, Any]], limit: int) -> Dict[str, Any]:
    """The envelope a trace search returns: newest-first rows, the start of the
    oldest row as the next cursor, and whether asking is worthwhile. ``traces``
    should carry at least one row beyond ``limit`` so ``has_more`` is exact."""
    rows = sorted(traces, key=lambda t: int(t.get("startTimeUnixNano") or 0), reverse=True)
    n = int(limit)
    page, rest = rows[:n], rows[n:]
    starts = [int(t.get("startTimeUnixNano") or 0) for t in page if t.get("startTimeUnixNano")]
    has_more = bool(rest)
    return {
        "traces": page,
        "next_before_ns": min(starts) if has_more and starts else None,
        "has_more": has_more,
    }


_UNIT_DIV = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000}


def trace_end_bound(
    f: "StructuredFilter", end_s: int, unit: str = "ns", round_up: bool = False
) -> int:
    """The query's end bound in ``unit`` (``ns`` / ``us`` / ``ms`` / ``s``):
    the window end, tightened by the paging cursor when there is one.
    ``round_up`` adds one unit for a backend that bounds time at that
    precision, so the row the cursor was taken from stays inside the window
    and :func:`strictly_older_traces` makes the cut exact afterwards."""
    div = _UNIT_DIV[unit]
    end = int(end_s) * (1_000_000_000 // div)
    if f.before_ns:
        end = min(end, int(f.before_ns) // div + (1 if round_up else 0))
    return end


def widened_fetch(
    limit: int, roots_only: bool, rows_per_trace: int = 4, inclusive: bool = False
) -> int:
    """How many rows to ask for: one beyond the page when every row is a
    trace (so ``has_more`` is exact), several per requested trace when the
    rows are spans (a widened search returns the matching spans, several of
    one trace, and they are folded into one card each). ``inclusive`` is the
    other spelling in use, ``(limit + 1) * rows_per_trace``."""
    n = int(limit)
    if roots_only:
        return n + 1
    return (n + 1) * rows_per_trace if inclusive else n * rows_per_trace + 1


def mark_full_fetch(
    page: Dict[str, Any], fetched: int, fetch: int, widened: bool
) -> Dict[str, Any]:
    """A widened search fetched spans, several per trace, so a full fetch
    means older rows exist even when they collapsed into fewer traces than
    the page holds: say so."""
    if widened and fetched >= fetch:
        page["has_more"] = True
    return page


# ── Log pages ───────────────────────────────────────────────────────────


def log_end_ns(end_s: int, f: "LogFilter") -> int:
    """The exclusive upper bound of a logs query in ns: the window end, tightened
    by the paging cursor when there is one."""
    end_ns = int(end_s) * 1_000_000_000
    return min(end_ns, int(f.before_ns)) if f.before_ns else end_ns


def strictly_older(records: List[Dict[str, Any]], f: "LogFilter") -> List[Dict[str, Any]]:
    """Drop rows at or after the cursor. Backends bound time at their own
    precision (ms, µs); this keeps the page exact at the nanosecond so a
    cursor never repeats the row it was taken from."""
    if not f.before_ns:
        return records
    cut = int(f.before_ns)
    return [r for r in records if int(r.get("time_unix_nano") or 0) < cut]


# Adapters fetch this many rows beyond the page so log_page() can see whether
# the last row's timestamp continues past the page boundary.
LOG_PAGE_SLACK = 50


def log_page(records: List[Dict[str, Any]], limit: int) -> Dict[str, Any]:
    """The envelope every logs route returns: the rows plus where the next
    page starts and whether asking is worthwhile.

    The cursor is a timestamp, and timestamps are not unique (a burst can log
    several lines in one nanosecond). A page therefore keeps every row that
    shares its last row's timestamp, so the next page — "strictly older than
    that timestamp" — neither repeats nor loses a tied row. ``records`` must be
    newest-first and should carry ``LOG_PAGE_SLACK`` rows beyond ``limit``.
    """
    rows = list(records)
    n = int(limit)
    if len(rows) > n:
        boundary = int(rows[n - 1].get("time_unix_nano") or 0)
        while n < len(rows) and int(rows[n].get("time_unix_nano") or 0) == boundary:
            n += 1
    page, rest = rows[:n], rows[n:]
    times = [int(r.get("time_unix_nano") or 0) for r in page if r.get("time_unix_nano")]
    has_more = bool(rest)
    return {
        "logs": page,
        "next_before_ns": min(times) if has_more and times else None,
        "has_more": has_more,
    }
