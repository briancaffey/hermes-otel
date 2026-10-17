"""Terminal query tool behind the ``hermes_otel:observability`` skill (#215).

The agent runs ``skills/observability/scripts/otel.py`` (a launcher for
:func:`main`) from the Hermes chat to look at its own telemetry without a
dashboard or a backend UI: recent turns, one turn as the span tree the README
draws, sessions, window totals, metrics, logs, and raw read-only SQL.

Sources
-------
* the **live store** (``$HERMES_HOME/hermes_otel_live.db``), the default: it is
  on by default, local, and holds every span the plugin produced whether or not
  a backend is configured;
* any **configured backend** that has a dashboard adapter (``--source
  <name|type>``), through the same adapters the dashboard tab uses.

Only the standard library is needed for the live store. The adapters need
``pyyaml`` to read the config file and the network; ``fastapi`` is optional
(``dashboard/backends/base.py`` falls back to a stdlib exception).

Everything is a plain function over dicts so it is unit-testable without a
terminal; output goes to a ``TextIO`` the caller passes in. The sources live
in :mod:`hermes_otel.query_sources`, the formatters and the span tree in
:mod:`hermes_otel.query_render`; this module is the commands and the parser.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple

from . import query_sources
from . import semconv as _sc
from .coerce import optional_number
from .live_store import LiveStore, _default_db_path
from .live_summary import summarize_trace
from .query_render import (
    TURN_KINDS,
    fmt_cost,
    fmt_ms,
    fmt_num,
    fmt_when,
    log_line,
    one_line,
    render_sessions,
    render_table,
    render_traces,
    render_tree,
)
from .query_sources import CliError, LiveSource, resolve_source

_READ_ONLY_SQL = re.compile(r"^\s*(select|with|explain|pragma)\b", re.IGNORECASE)
_SINCE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhdw])$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_since(text: Optional[str], now_s: Optional[float] = None) -> Optional[int]:
    """``30m`` / ``2h`` / ``3d`` / ``1w`` / ISO date or datetime → unix seconds.

    ``None`` or ``""`` → ``None`` (no lower bound). Raises :class:`CliError`
    for anything else.
    """
    if not text:
        return None
    now = time.time() if now_s is None else now_s
    m = _SINCE_RE.match(text.strip())
    if m:
        return int(now - float(m.group(1)) * _UNIT_S[m.group(2)])
    raw = text.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return int(dt.replace(tzinfo=timezone.utc).timestamp())
    raise CliError(f"--since {text!r}: use 30m / 2h / 3d / 1w or an ISO date (2026-09-20T10:00)")


# ── commands ──────────────────────────────────────────────────────────────


def _window(args: argparse.Namespace) -> Tuple[Optional[int], Optional[int]]:
    since_s = parse_since(getattr(args, "since", None))
    until_s = parse_since(getattr(args, "until", None))
    return (int(since_s * 1e9) if since_s else None, int(until_s * 1e9) if until_s else None)


def _emit_json(obj: Any, out: TextIO) -> None:
    out.write(json.dumps(obj, indent=2, default=str) + "\n")


def cmd_status(args: argparse.Namespace, out: TextIO) -> int:
    path = getattr(args, "db", None) or _default_db_path()
    from .hermes_home import resolve_hermes_home, resolve_profile_name

    info: Dict[str, Any] = {
        "hermes_home": str(resolve_hermes_home()),
        "profile": resolve_profile_name(),
        "live_store": {"path": path, "exists": Path(path).exists()},
        "backends": [],
        "config_file": None,
    }
    if Path(path).exists():
        store = LiveStore(db_path=path)
        try:
            st = store.stats()
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                lo, hi = c.execute("SELECT MIN(ts), MAX(ts) FROM events").fetchone()
            finally:
                c.close()
            info["live_store"].update(
                {
                    "spans": st["spans"],
                    "metrics": st["metrics"],
                    "logs": st["logs"],
                    "oldest": fmt_when(lo),
                    "newest": fmt_when(hi),
                }
            )
        finally:
            store.close()
    try:
        backends = query_sources._import_backends()
        cfg_path, configured, pin = backends.load_config()
        info["config_file"] = str(cfg_path) if cfg_path else None
        info["query_backend_pin"] = pin
        for b in configured:
            cls = backends.find_adapter_class(str(b.get("type") or ""))
            # Capabilities are per entry, not per type (an ``lgtm`` entry may
            # switch Loki off, a ``tempo`` entry may add Prometheus), so ask an
            # instance when one can be built; fall back to the class flags.
            try:
                probe = cls(b) if cls else None
            except Exception:
                probe = cls
            info["backends"].append(
                {
                    "name": backends.backend_label(b),
                    "type": b.get("type"),
                    "endpoint": b.get("endpoint") or b.get("host") or b.get("base_url"),
                    "queryable": cls is not None,
                    "metrics": bool(getattr(probe, "supports_metrics", False)) if cls else False,
                    "logs": bool(getattr(probe, "supports_logs", False)) if cls else False,
                }
            )
    except Exception as exc:  # adapters need pyyaml; report, don't die
        info["backends_error"] = f"{exc.__class__.__name__}: {exc}"
    if args.json:
        _emit_json(info, out)
        return 0
    ls = info["live_store"]
    out.write(f"hermes home   {info['hermes_home']}  (profile: {info['profile']})\n")
    out.write(f"live store    {ls['path']}\n")
    if ls["exists"]:
        out.write(
            f"              {fmt_num(ls['spans'])} spans · {fmt_num(ls['metrics'])} metric points · "
            f"{fmt_num(ls['logs'])} logs · {ls['oldest']} → {ls['newest']} (UTC)\n"
        )
    else:
        out.write("              (missing: no turn recorded yet, or dashboard_live is false)\n")
    out.write(f"config file   {info['config_file'] or '(none found: env vars only)'}\n")
    if info.get("backends_error"):
        out.write(f"backends      unavailable: {info['backends_error']}\n")
    elif not info["backends"]:
        out.write("backends      none configured (the live store is the only source)\n")
    else:
        out.write("backends\n")
        for b in info["backends"]:
            caps = (
                "traces" + (" · metrics" if b["metrics"] else "") + (" · logs" if b["logs"] else "")
            )
            q = f"queryable here: {caps}" if b["queryable"] else "no query adapter (use its own UI)"
            out.write(
                f"  {b['name']:<14} {b['type']:<12} {b['endpoint'] or '-':<48} "
                f"{_reachability(b['endpoint']):<22} {q}\n"
            )
        if info.get("query_backend_pin"):
            out.write(f"  default --source: {info['query_backend_pin']} (query_backend)\n")
    return 0


def _reachability(endpoint: Any) -> str:
    """One TCP connect to the endpoint (#481): ``reachable (3 ms)`` or the error."""
    from .probe import parse_host_port, probe_tcp

    parsed = parse_host_port(endpoint)
    if parsed is None:
        return "-"
    ok, ms, error = probe_tcp(parsed[0], parsed[1], timeout_s=1.0)
    return f"reachable ({ms:.0f} ms)" if ok else f"unreachable: {error}"


def cmd_traces(args: argparse.Namespace, out: TextIO) -> int:
    src = resolve_source(args)
    start_ns, end_ns = _window(args)
    rows = src.traces(args, start_ns, end_ns)
    if args.json:
        _emit_json(rows, out)
        return 0
    render_traces(rows, out)
    if rows:
        out.write(
            f"{len(rows)} trace(s), newest first; full ids with --json; `trace <id>` for the tree\n"
        )
    return 0


def _trace_filter(session: Optional[str], limit: int) -> argparse.Namespace:
    """The ``traces`` filters with only a session and a limit set (``trace last``, ``stats``)."""
    return argparse.Namespace(
        session=session,
        status=None,
        name=None,
        text=None,
        limit=limit,
        min_duration=None,
        model=None,
        tool=None,
    )


def _pick_trace_id(src: Any, ref: str, args: argparse.Namespace) -> str:
    """``last`` / ``last-2`` → the n-th newest trace in the window; else the id as given."""
    m = re.match(r"^last(?:-(\d+))?$", ref.strip())
    if not m:
        return ref.strip()
    n = int(m.group(1) or 1)
    start_ns, end_ns = _window(args)
    rows = src.traces(_trace_filter(getattr(args, "session", None), n), start_ns, end_ns)
    if len(rows) < n:
        raise CliError(f"only {len(rows)} trace(s) in the window; cannot pick {ref}")
    return str(rows[n - 1]["traceId"])


def cmd_trace(args: argparse.Namespace, out: TextIO) -> int:
    src = resolve_source(args)
    trace_id = _pick_trace_id(src, args.trace_ref, args)
    spans = src.trace_spans(trace_id)
    if not spans:
        raise CliError(f"trace {trace_id} not found in {src.label}")
    if args.json:
        _emit_json({"trace": summarize_trace(spans), "spans": spans}, out)
        return 0
    summary = summarize_trace(spans)
    head = [f"trace {trace_id}", f"source {src.label}", fmt_when(summary.get("startNs")) + " UTC"]
    if summary.get("session"):
        head.append(f"session {summary['session']}")
    if summary.get("model"):
        head.append(f"model {summary['model']}")
    out.write(" · ".join(head) + "\n\n")
    render_tree(
        spans,
        out,
        width=args.width,
        show_attrs=args.attrs,
        show_io=args.io,
        io_chars=args.io_chars,
        bars=not args.no_bars,
    )
    return 0


def cmd_span(args: argparse.Namespace, out: TextIO) -> int:
    src = resolve_source(args)
    span: Optional[Dict[str, Any]] = None
    if isinstance(src, LiveSource):
        row = None
        try:
            c = sqlite3.connect(f"file:{src.store.db_path}?mode=ro", uri=True)
            try:
                row = c.execute(
                    "SELECT data FROM events WHERE kind='span' AND span_id=? LIMIT 1",
                    (args.span_id,),
                ).fetchone()
            finally:
                c.close()
        except sqlite3.Error as exc:
            raise CliError(f"live store query failed: {exc}")
        span = json.loads(row[0]) if row else None
    elif args.trace:
        span = next(
            (s for s in src.trace_spans(args.trace) if s.get("span_id") == args.span_id), None
        )
    else:
        raise CliError("span on a backend needs --trace <trace id> (backends index by trace)")
    if span is None:
        raise CliError(f"span {args.span_id} not found in {src.label}")
    if args.json:
        _emit_json(span, out)
        return 0
    out.write(f"{span.get('name')}  ({fmt_ms(span.get('duration_ms'))}, {span.get('status')})\n")
    out.write(
        f"trace {span.get('trace_id')}  span {span.get('span_id')}  parent {span.get('parent_span_id') or '-'}\n"
    )
    out.write(
        f"start {fmt_when(span.get('start_time_unix_nano'))}  end {fmt_when(span.get('end_time_unix_nano'))} (UTC)\n"
    )
    a = span.get("attributes") or {}
    for k in sorted(a):
        v = a[k]
        text = v if isinstance(v, str) else json.dumps(v, default=str)
        if "\n" in text or len(text) > 160:
            out.write(f"{k} =\n")
            for ln in text.splitlines():
                out.write(f"    {ln}\n")
        else:
            out.write(f"{k} = {text}\n")
    return 0


def cmd_sessions(args: argparse.Namespace, out: TextIO) -> int:
    src = resolve_source(args)
    start_ns, end_ns = _window(args)
    rows = src.sessions(start_ns, end_ns, args.limit)
    if args.json:
        _emit_json(rows, out)
        return 0
    render_sessions(rows, out)
    return 0


def compute_stats(by_trace: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Window totals from ``{trace_id: spans}`` (pure; the ``stats`` command's core)."""
    turns = 0
    spans_n = 0
    errors = 0
    tokens = 0.0
    cost = 0.0
    sessions = set()
    tools: Dict[str, Dict[str, int]] = {}
    models: Dict[str, Dict[str, float]] = {}
    skills: Dict[str, int] = {}
    approvals: Dict[str, int] = {}
    slowest: List[Dict[str, Any]] = []
    for tid, spans in by_trace.items():
        if not spans:
            continue
        s = summarize_trace(spans)
        if s.get("rootKind") in TURN_KINDS:
            turns += 1
        spans_n += len(spans)
        tokens += s.get("tokens") or 0
        cost += s.get("cost") or 0
        if s.get("session"):
            sessions.add(s["session"])
        slowest.append(
            {
                "traceId": tid,
                "rootName": s.get("rootName"),
                "durationMs": s.get("durationMs"),
                "startNs": s.get("startNs"),
            }
        )
        for sp in spans:
            a = sp.get("attributes") or {}
            name = str(sp.get("name") or "")
            kind = _sc.span_kind(name, a)
            short = name.partition(".")[2]  # ``tool.bash`` → ``bash``
            if sp.get("status") == "ERROR":
                errors += 1
            if kind == "tool":
                row = tools.setdefault(
                    short or name, {"calls": 0, "errors": 0, "completed": 0, "ms": 0.0}
                )
                row["calls"] += 1
                oc = str(a.get("hermes.tool.outcome") or "")
                if oc == "completed":
                    row["completed"] += 1
                if sp.get("status") == "ERROR" or oc in ("error", "timeout"):
                    row["errors"] += 1
                row["ms"] += optional_number(sp.get("duration_ms")) or 0.0
            elif kind == "api":
                model = str(_sc.first(a, _sc.MODEL_KEYS) or short or "?")
                row = models.setdefault(
                    model, {"calls": 0, "in": 0.0, "out": 0.0, "cost": 0.0, "errors": 0, "ms": 0.0}
                )
                row["calls"] += 1
                row["in"] += optional_number(_sc.first(a, _sc.INPUT_TOKEN_KEYS)) or 0.0
                row["out"] += optional_number(_sc.first(a, _sc.OUTPUT_TOKEN_KEYS)) or 0.0
                row["cost"] += optional_number(a.get(_sc.COST_KEY)) or 0.0
                row["ms"] += optional_number(sp.get("duration_ms")) or 0.0
                if sp.get("status") == "ERROR":
                    row["errors"] += 1
            elif kind == "skill":
                skills[short or name] = skills.get(short or name, 0) + 1
            elif kind == "approval":
                ch = str(a.get("hermes.approval.choice") or "?")
                approvals[ch] = approvals.get(ch, 0) + 1
    slowest.sort(key=lambda r: r.get("durationMs") or 0, reverse=True)
    return {
        "traces": len([t for t in by_trace.values() if t]),
        "turns": turns,
        "sessions": len(sessions),
        "spans": spans_n,
        "errors": errors,
        "tokens": tokens,
        "cost": cost,
        "tools": dict(sorted(tools.items(), key=lambda kv: kv[1]["calls"], reverse=True)),
        "models": dict(sorted(models.items(), key=lambda kv: kv[1]["calls"], reverse=True)),
        "skills": dict(sorted(skills.items(), key=lambda kv: kv[1], reverse=True)),
        "approvals": approvals,
        "slowest": slowest[:5],
    }


def cmd_stats(args: argparse.Namespace, out: TextIO) -> int:
    src = resolve_source(args)
    start_ns, end_ns = _window(args)
    rows = src.traces(_trace_filter(args.session, args.limit), start_ns, end_ns)
    by_trace = src.spans_for_traces([r["traceId"] for r in rows if r.get("traceId")])
    st = compute_stats(by_trace)
    st["window"] = {
        "since": args.since,
        "until": args.until,
        "traces_considered": len(rows),
        "limit": args.limit,
    }
    if args.json:
        _emit_json(st, out)
        return 0
    win = f"since {args.since}" if args.since else "all retained"
    out.write(
        f"window: {win} · source {src.label} · {st['traces']} traces (limit {args.limit})\n\n"
    )
    out.write(
        f"turns {fmt_num(st['turns'])} · sessions {fmt_num(st['sessions'])} · spans {fmt_num(st['spans'])} · "
        f"errors {fmt_num(st['errors'])} · tokens {fmt_num(st['tokens'])} · cost {fmt_cost(st['cost'])}\n"
    )
    if st["models"]:
        out.write("\nmodels\n")
        render_table(
            [
                {
                    "model": m,
                    "calls": fmt_num(r["calls"]),
                    "in": fmt_num(r["in"]),
                    "out": fmt_num(r["out"]),
                    "cost": fmt_cost(r["cost"]),
                    "errors": fmt_num(r["errors"]),
                    "avg": fmt_ms(r["ms"] / r["calls"] if r["calls"] else None),
                }
                for m, r in st["models"].items()
            ],
            [
                ("model", "model"),
                ("calls", "api calls"),
                ("in", "in tok"),
                ("out", "out tok"),
                ("cost", "cost"),
                ("errors", "errors"),
                ("avg", "avg"),
            ],
            out,
        )
    if st["tools"]:
        out.write("\ntools\n")
        render_table(
            [
                {
                    "tool": t,
                    "calls": fmt_num(r["calls"]),
                    "completed": fmt_num(r["completed"]),
                    "errors": fmt_num(r["errors"]),
                    "avg": fmt_ms(r["ms"] / r["calls"] if r["calls"] else None),
                }
                for t, r in st["tools"].items()
            ],
            [
                ("tool", "tool"),
                ("calls", "calls"),
                ("completed", "completed"),
                ("errors", "errors/timeouts"),
                ("avg", "avg"),
            ],
            out,
        )
    if st["skills"]:
        out.write(
            "\nskills loaded: " + ", ".join(f"{k} ×{v}" for k, v in st["skills"].items()) + "\n"
        )
    if st["approvals"]:
        out.write("approvals: " + ", ".join(f"{k} ×{v}" for k, v in st["approvals"].items()) + "\n")
    if st["slowest"]:
        out.write("\nslowest turns\n")
        render_table(
            [
                {
                    "when": fmt_when(r["startNs"]),
                    "trace": r["traceId"],
                    "root": one_line(r["rootName"], 24),
                    "duration": fmt_ms(r["durationMs"]),
                }
                for r in st["slowest"]
            ],
            [
                ("when", "when (UTC)"),
                ("trace", "trace"),
                ("root", "root"),
                ("duration", "duration"),
            ],
            out,
        )
    return 0


def _bucket_seconds(text: str) -> int:
    m = _SINCE_RE.match(text.strip())
    if not m:
        raise CliError(f"--bucket {text!r}: use 30s / 5m / 1h / 1d")
    return max(1, int(float(m.group(1)) * _UNIT_S[m.group(2)]))


def cmd_metrics(args: argparse.Namespace, out: TextIO) -> int:
    src = resolve_source(args)
    since_s = parse_since(args.since) or int(time.time() - 24 * 3600)
    until_s = parse_since(args.until) or int(time.time())
    if not args.name:
        names = src.metric_names(since_s, until_s)
        if args.json:
            _emit_json(names, out)
            return 0
        render_table(
            [
                {
                    "name": n.get("name"),
                    "points": fmt_num(n.get("count")),
                    "last": fmt_when(n.get("lastTs")),
                }
                for n in names
            ],
            [("name", "instrument"), ("points", "points"), ("last", "last (UTC)")],
            out,
        )
        if names:
            out.write(
                "`metrics <name> [--group-by attr] [--bucket 5m] [--agg sum|count|avg|max|last]` for values\n"
            )
        return 0
    bucket_s = _bucket_seconds(args.bucket)
    res = src.metric_buckets(args.name, since_s, until_s, bucket_s, args.group_by or None, args.agg)
    if args.json:
        _emit_json(res, out)
        return 0
    series: Dict[str, List[Optional[float]]] = res.get("series") or {}
    buckets: List[int] = res.get("buckets") or []
    out.write(
        f"{res.get('name')} · agg {res.get('agg')} · bucket {args.bucket} · {fmt_num(res.get('points'))} points · source {src.label}\n"
    )
    if not series:
        out.write("(no data in the window)\n")
        return 0
    totals = []
    for label, vals in series.items():
        present = [v for v in vals if v is not None]
        if args.agg in ("avg", "last", "max"):
            total = (
                (sum(present) / len(present))
                if present and args.agg == "avg"
                else (
                    present[-1]
                    if present and args.agg == "last"
                    else (max(present) if present else None)
                )
            )
        else:
            total = sum(present) if present else None
        totals.append(
            {
                "group": label if label != "_" else "(all)",
                "total": fmt_num(total),
                "buckets": fmt_num(len(present)),
            }
        )
    render_table(
        totals,
        [
            ("group", args.group_by or "group"),
            ("total", "window total" if args.agg in ("sum", "count") else args.agg),
            ("buckets", "buckets with data"),
        ],
        out,
    )
    if args.buckets:
        out.write("\n")
        rows = []
        for i, b in enumerate(buckets):
            row = {"bucket": fmt_when(b)}
            for label, vals in series.items():
                row[label] = fmt_num(vals[i]) if i < len(vals) and vals[i] is not None else ""
            if any(row[k] for k in series):
                rows.append(row)
        render_table(
            rows,
            [("bucket", "bucket (UTC)")] + [(k, k if k != "_" else "value") for k in series],
            out,
        )
    return 0


def cmd_logs(args: argparse.Namespace, out: TextIO) -> int:
    src = resolve_source(args)
    if getattr(args, "follow", False):
        return _follow_logs(src, args, out)
    start_ns, end_ns = _window(args)
    rows = src.logs(args, start_ns, end_ns)
    if args.json:
        _emit_json(rows, out)  # the full record: attributes, event_name, span_id, severity_number
        return 0
    if not rows:
        out.write(
            "(no log records; logs.capture or logs.events.enabled must be on for the plugin to record any)\n"
        )
        return 0
    for r in rows:
        out.write(log_line(r))
    events = sum(1 for r in rows if r.get("event_name"))
    out.write(
        f"{len(rows)} record(s), newest first"
        + (f" ({events} event{'s' if events != 1 else ''}, marked ⚡)" if events else "")
        + "\n"
    )
    return 0


def _follow_logs(src: Any, args: argparse.Namespace, out: TextIO) -> int:
    """Print new records as they arrive, oldest first, until interrupted (live store only)."""
    if not isinstance(src, LiveSource):
        raise CliError("--follow reads the live store; drop --source to follow")
    seen: set = set()
    start_ns = time.time_ns() - 60 * 1_000_000_000  # start with the last minute
    out.write("following the live store (Ctrl-C to stop)\n")
    out.flush()
    try:
        while True:
            rows = src.logs(args, start_ns, None)
            fresh = []
            for r in rows:
                key = (r.get("seq"), r.get("time_unix_nano"), r.get("body"))
                if key in seen:
                    continue
                seen.add(key)
                fresh.append(r)
            for r in sorted(fresh, key=lambda x: int(x.get("time_unix_nano") or 0)):
                out.write(json.dumps(r, default=str) + "\n" if args.json else log_line(r))
            out.flush()
            if rows:
                newest = max(int(r.get("time_unix_nano") or 0) for r in rows)
                start_ns = max(start_ns, newest - 5_000_000_000)  # overlap 5 s; dedupe by key
            if len(seen) > 20_000:
                seen = set(list(seen)[-5_000:])
            time.sleep(float(getattr(args, "interval", 2.0) or 2.0))
    except KeyboardInterrupt:
        out.write("\n")
        return 0


def cmd_sql(args: argparse.Namespace, out: TextIO) -> int:
    query = args.query.strip()
    if not _READ_ONLY_SQL.match(query):
        raise CliError(
            "only SELECT / WITH / EXPLAIN / PRAGMA statements are accepted (the store is opened read-only)"
        )
    path = getattr(args, "db", None) or _default_db_path()
    if not Path(path).exists():
        raise CliError(f"no live store at {path}")
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise CliError(f"cannot open {path}: {exc}")
    try:
        try:
            cur = c.execute(query)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(args.limit) if args.limit else cur.fetchall()
        except (sqlite3.Error, sqlite3.Warning) as exc:
            # Python 3.9 raises sqlite3.Warning (not an Error) for a second
            # statement in the string; newer versions raise ProgrammingError.
            raise CliError(f"sqlite: {exc}")
    finally:
        c.close()
    if args.json:
        _emit_json([dict(zip(cols, r)) for r in rows], out)
        return 0
    render_table(
        [{k: one_line(v, 120) for k, v in zip(cols, r)} for r in rows], [(k, k) for k in cols], out
    )
    out.write(
        f"{len(rows)} row(s)"
        + (f" (limit {args.limit})" if args.limit and len(rows) >= args.limit else "")
        + "\n"
    )
    return 0


def cmd_mcp_config(args: argparse.Namespace, out: TextIO) -> int:
    """``mcp-config``: the opentelemetry-mcp server config for the configured backends (#304)."""
    from . import mcp_config as mc
    from .hermes_home import resolve_hermes_home

    backends = query_sources._import_backends()
    cfg_path, configured, pin = backends.load_config()
    results = mc.classify(configured)
    wanted = (getattr(args, "source", None) or "").strip() or None
    if wanted and wanted.lower() == "live":
        wanted = None
    target = mc.pick_target(results, wanted=wanted, pin=pin)
    name = getattr(args, "name", None) or mc.DEFAULT_SERVER_NAME
    home = resolve_hermes_home()
    existing = mc.existing_server_names(home)
    report: Dict[str, Any] = {
        "config_file": str(cfg_path) if cfg_path else None,
        "package": mc.MCP_PACKAGE_SPEC,
        "server_name": name,
        "backends": [
            (
                {"name": r.name, "type": r.backend_type, "queryable": True, "env": r.env()}
                if isinstance(r, mc.QueryTarget)
                else {
                    "name": r.name,
                    "type": r.backend_type,
                    "queryable": False,
                    "reason": r.reason,
                }
            )
            for r in results
        ],
        "selected": target.name if target else None,
        "existing_mcp_servers": existing,
    }
    if target is not None:
        report["mcp_servers_yaml"] = mc.render_yaml(target, name)
        report["hermes_mcp_add"] = mc.hermes_add_argv(target, name)
        report["claude_mcp_add"] = mc.render_claude_code_add(target, name)
        report["mcp_json"] = mc.render_mcp_json(target, name)
    applied: List[List[str]] = []
    if getattr(args, "apply", False):
        if target is None:
            raise CliError("nothing to apply: no configured backend is queryable over MCP")
        try:
            applied = mc.apply(target, home, name, force=bool(getattr(args, "force", False)))
        except mc.ApplyRefused as exc:
            raise CliError(str(exc))
        except Exception as exc:  # subprocess failures: hermes missing, add rejected
            raise CliError(f"hermes mcp add failed: {exc.__class__.__name__}: {exc}")
        report["applied"] = applied
    if args.json:
        _emit_json(report, out)
        return 0
    out.write(f"config file   {report['config_file'] or '(none found: env vars only)'}\n")
    if not results:
        out.write("backends      none configured; the MCP server needs a Jaeger or Tempo entry\n")
    else:
        out.write("backends\n")
        for b in report["backends"]:
            if b["queryable"]:
                env = b["env"]
                q = f"queryable over MCP: BACKEND_TYPE={env['BACKEND_TYPE']} BACKEND_URL={env['BACKEND_URL']}"
            else:
                q = f"not queryable over MCP ({b['reason']})"
            out.write(f"  {b['name']:<14} {b['type']:<12} {q}\n")
    if wanted and target is None:
        raise CliError(f"--source {wanted!r} names no configured backend the MCP server can read")
    if target is None:
        out.write(
            "\nNo entry the server can read. Add a Jaeger (v1) or Tempo backend, or query the live "
            "store with this tool instead.\n"
        )
        return 0
    out.write(f"\nselected      {target.name} ({target.backend_type})\n")
    out.write(f"\n# {home / 'config.yaml'} (what `hermes mcp add` writes)\n")
    out.write(report["mcp_servers_yaml"])
    out.write("\n# Hermes (answer Y to the enable prompt)\n")
    out.write(mc.render_hermes_add(target, name) + "\n")
    out.write("\n# Claude Code\n")
    out.write(report["claude_mcp_add"] + "\n")
    out.write("\n# Cursor / Claude Desktop (mcp.json)\n")
    out.write(json.dumps(report["mcp_json"], indent=2) + "\n")
    if name in existing and not applied:
        out.write(
            f"\nnote: an MCP server named {name!r} already exists in config.yaml; "
            "--apply needs --force to replace it.\n"
        )
    if applied:
        out.write("\napplied: " + "; ".join(" ".join(a) for a in applied) + "\n")
        out.write("Start a new Hermes session (or /reload-mcp) to use the tools.\n")
    out.write(
        "\nThe server reads traces only and returns stored prompts to whoever connects; "
        "caveats: https://briancaffey.github.io/hermes-otel/configuration/mcp-query-server#caveats\n"
    )
    return 0


# ── argument parsing ──────────────────────────────────────────────────────


def _add_common(
    p: argparse.ArgumentParser, window: bool = True, limit: Optional[int] = None
) -> None:
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument(
        "--source", default="live", help="live (default) or a configured backend's name/type"
    )
    p.add_argument(
        "--db", default=None, help="live store file (default $HERMES_HOME/hermes_otel_live.db)"
    )
    if window:
        p.add_argument("--since", default=None, help="30m, 2h, 3d, 1w or an ISO date/time (UTC)")
        p.add_argument("--until", default=None, help="same forms as --since")
    if limit is not None:
        p.add_argument("--limit", type=int, default=limit)


def _add_trace_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--session", default=None, help="exact session id")
    p.add_argument("--status", choices=["ok", "error"], default=None)
    p.add_argument("--model", default=None, help="model name substring (live) / exact (backend)")
    p.add_argument(
        "--tool", default=None, help="tool name, e.g. bash (matches turns that called it)"
    )
    p.add_argument("--name", default=None, help="span name substring, e.g. skill.observability")
    p.add_argument(
        "--text", default=None, help="free text anywhere in the span (live: attributes JSON)"
    )
    p.add_argument(
        "--min-duration", dest="min_duration", type=float, default=None, help="milliseconds"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="otel.py",
        description="Query hermes-otel telemetry from the terminal: the live store or a configured backend.",
    )
    sub = p.add_subparsers(dest="command", metavar="command")
    sub.required = True

    s = sub.add_parser("status", help="live store fill and the configured backends")
    _add_common(s, window=False)

    s = sub.add_parser("traces", help="recent turns (one row per trace)")
    _add_common(s, limit=20)
    _add_trace_filters(s)

    s = sub.add_parser(
        "trace", help="one turn as a span tree: `trace last`, `trace last-2`, `trace <id>`"
    )
    s.add_argument("trace_ref")
    _add_common(s)
    s.add_argument("--session", default=None, help="with `last`: newest trace of this session")
    s.add_argument("--attrs", action="store_true", help="print every attribute under each span")
    s.add_argument(
        "--io",
        action="store_true",
        help="print captured input/output under llm, api and tool spans",
    )
    s.add_argument("--io-chars", dest="io_chars", type=int, default=400)
    s.add_argument("--width", type=int, default=shutil.get_terminal_size((110, 24)).columns)
    s.add_argument(
        "--no-bars", dest="no_bars", action="store_true", help="omit the waterfall column"
    )

    s = sub.add_parser("span", help="every attribute of one span")
    s.add_argument("span_id")
    _add_common(s, window=False)
    s.add_argument("--trace", default=None, help="trace id (required for a backend source)")

    s = sub.add_parser("sessions", help="one row per session")
    _add_common(s, limit=20)

    s = sub.add_parser("stats", help="window totals: turns, tokens, cost, tools, models, slowest")
    _add_common(s, limit=500)
    s.add_argument("--session", default=None)

    s = sub.add_parser("metrics", help="instruments, or one instrument's values")
    s.add_argument("name", nargs="?", default=None)
    _add_common(s)
    s.add_argument(
        "--group-by",
        dest="group_by",
        default=None,
        help="attribute to split by, e.g. gen_ai.request.model",
    )
    s.add_argument("--bucket", default="5m")
    s.add_argument("--agg", choices=["sum", "count", "avg", "max", "last"], default="sum")
    s.add_argument("--buckets", action="store_true", help="also print one row per time bucket")

    s = sub.add_parser("logs", help="captured log records")
    _add_common(s, limit=100)
    s.add_argument("--level", default=None, help="minimum level: debug, info, warning, error")
    s.add_argument("--logger", default=None)
    s.add_argument("--text", default=None)
    s.add_argument("--trace", default=None)
    s.add_argument("--session", default=None)
    s.add_argument(
        "--event", default=None, help="only this structured event (e.g. hermes.tool.call)"
    )
    s.add_argument(
        "--events-only", action="store_true", help="only structured events (logs.events)"
    )
    s.add_argument(
        "--follow",
        action="store_true",
        help="keep printing new records as they arrive (live store only; Ctrl-C to stop)",
    )
    s.add_argument(
        "--interval", type=float, default=2.0, help="poll interval for --follow, seconds"
    )

    s = sub.add_parser(
        "mcp-config",
        help="the opentelemetry-mcp server config (mcp_servers block, hermes mcp add) for a Jaeger or Tempo entry",
    )
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.add_argument(
        "--source",
        default=None,
        help="which backend entry (name or type); default: the query_backend pin, else the first readable one",
    )
    s.add_argument("--name", default=None, help="MCP server name (default otel)")
    s.add_argument(
        "--apply", action="store_true", help="run `hermes mcp add` for the selected entry"
    )
    s.add_argument(
        "--force", action="store_true", help="with --apply: replace an existing server of that name"
    )
    s = sub.add_parser("sql", help="read-only SQL against the live store (table `events`)")
    s.add_argument("query")
    s.add_argument("--json", action="store_true")
    s.add_argument("--db", default=None)
    s.add_argument("--limit", type=int, default=200)
    return p


_COMMANDS = {
    "status": cmd_status,
    "traces": cmd_traces,
    "trace": cmd_trace,
    "span": cmd_span,
    "sessions": cmd_sessions,
    "stats": cmd_stats,
    "metrics": cmd_metrics,
    "logs": cmd_logs,
    "sql": cmd_sql,
    "mcp-config": cmd_mcp_config,
}


def main(
    argv: Optional[Sequence[str]] = None, out: Optional[TextIO] = None, err: Optional[TextIO] = None
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return _COMMANDS[args.command](args, out)
    except CliError as exc:
        err.write(f"otel: {exc}\n")
        return 1
    except BrokenPipeError:  # pragma: no cover
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
