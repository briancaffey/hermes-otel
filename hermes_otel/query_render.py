"""Terminal rendering for the query CLI: formatters, tables and the span tree (#215).

Pure functions over dicts; output goes to the ``TextIO`` the caller passes in.
:func:`span_summary` is the one-liner to the right of each tree node, driven
by :data:`SUMMARY_FIELDS`: one ordered tuple of :class:`Field` per span kind
(``semconv.span_kind``), each naming the attribute chain it reads and how to
render the value.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, NamedTuple, Optional, TextIO, Tuple

from . import semconv as _sc
from .coerce import optional_number
from .live_summary import trace_totals

TREE_BAR_WIDTH = 16

# The kinds a turn's root span can have; their summary carries the turn fields.
TURN_KINDS: Tuple[str, ...] = ("agent", "cron", "session")


# ── small formatters (pure) ───────────────────────────────────────────────


def fmt_when(ns: Optional[float]) -> str:
    if not ns:
        return "-"
    return datetime.fromtimestamp(int(ns) / 1e9, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fmt_ms(ms: Optional[float]) -> str:
    if ms is None:
        return "-"
    ms = float(ms)
    if ms < 1000:
        return f"{ms:.0f} ms"
    s = ms / 1000
    if s < 60:
        return f"{s:.2f} s"
    m, rem = divmod(s, 60)
    if m < 60:
        return f"{int(m)}m {rem:02.0f}s"
    h, m = divmod(int(m), 60)
    return f"{h}h {m:02d}m"


def fmt_num(n: Optional[float]) -> str:
    if n is None:
        return "-"
    try:
        f = float(n)
    except (TypeError, ValueError):
        return str(n)
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}"


def fmt_cost(c: Optional[float]) -> str:
    if c is None:
        return "-"
    try:
        f = float(c)
    except (TypeError, ValueError):
        return str(c)
    if f == 0:
        return "$0"
    if f < 0.01:
        return f"${f:.4f}"
    return f"${f:.2f}"


def one_line(value: Any, limit: int = 80) -> str:
    """A value as one clipped line for a table cell."""
    if value is None:
        return "-"
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = text.replace("\r", "").replace("\n", "⏎")
    if len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


def _listish(v: Any) -> Any:
    """``["stop"]`` stored as a JSON string comes back as a list."""
    if isinstance(v, str) and v.startswith("[") and v.endswith("]"):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def render_table(rows: List[Dict[str, Any]], columns: List[Tuple[str, str]], out: TextIO) -> None:
    """``columns`` is ``[(key, header)]``; every cell is already a string."""
    if not rows:
        out.write("(no rows)\n")
        return
    widths = {k: len(h) for k, h in columns}
    for r in rows:
        for k, _ in columns:
            widths[k] = max(widths[k], len(str(r.get(k, ""))))
    line = "  ".join(h.ljust(widths[k]) for k, h in columns)
    out.write(line.rstrip() + "\n")
    out.write("  ".join("-" * widths[k] for k, _ in columns) + "\n")
    for r in rows:
        out.write("  ".join(str(r.get(k, "")).ljust(widths[k]) for k, _ in columns).rstrip() + "\n")


def _short_id(value: Any, n: int = 8) -> str:
    s = str(value or "")
    return s[:n] if len(s) > n else s


_TRACE_COLUMNS = [
    ("when", "when (UTC)"),
    ("trace", "trace"),
    ("root", "root"),
    ("session", "session"),
    ("model", "model"),
    ("duration", "duration"),
    ("spans", "spans"),
    ("tokens", "tokens"),
    ("cost", "cost"),
    ("status", "status"),
]


def render_traces(rows: List[Dict[str, Any]], out: TextIO) -> None:
    """The ``traces`` table: one ``summarize_trace`` row per line."""
    table = [
        {
            "when": fmt_when(t.get("startNs")),
            "trace": _short_id(t.get("traceId"), 12),
            "root": one_line(t.get("rootName"), 24),
            "session": _short_id(t.get("session"), 10),
            "model": one_line(t.get("model"), 28),
            "duration": fmt_ms(t.get("durationMs")),
            "spans": fmt_num(t.get("spanCount")),
            "tokens": fmt_num(t.get("tokens")),
            "cost": fmt_cost(t.get("cost")),
            "status": "ERROR" if t.get("error") else "ok",
        }
        for t in rows
    ]
    render_table(table, _TRACE_COLUMNS, out)


_SESSION_COLUMNS = [
    ("session", "session"),
    ("turns", "turns"),
    ("spans", "spans"),
    ("tools", "tools"),
    ("errors", "errors"),
    ("tokens", "tokens"),
    ("cost", "cost"),
    ("model", "model"),
    ("platform", "platform"),
    ("first", "first (UTC)"),
    ("last", "last (UTC)"),
]


def render_sessions(rows: List[Dict[str, Any]], out: TextIO) -> None:
    """The ``sessions`` table: one live-store session row per line."""
    table = [
        {
            "session": str(r.get("session") or ""),
            "turns": fmt_num(r.get("turns")),
            "spans": fmt_num(r.get("spans")),
            "tools": fmt_num(r.get("toolCalls")),
            "errors": fmt_num(r.get("errors")),
            "tokens": fmt_num(r.get("tokens")),
            "cost": fmt_cost(r.get("cost")),
            "model": one_line(r.get("model"), 28),
            "platform": one_line(r.get("platform"), 10),
            "first": fmt_when(r.get("startNs")),
            "last": fmt_when(r.get("endNs")),
        }
        for r in rows
    ]
    render_table(table, _SESSION_COLUMNS, out)


def log_line(r: Dict[str, Any]) -> str:
    """One record as a terminal line: time, level, event or logger, body, ids."""
    name = r.get("event_name") or r.get("logger") or ""
    marker = "⚡" if r.get("event_name") else " "
    tail = ""
    if r.get("session_id"):
        tail += f"  [session {_short_id(r.get('session_id'), 16)}]"
    if r.get("trace_id"):
        tail += f"  [trace {_short_id(r.get('trace_id'), 12)}]"
    return (
        f"{fmt_when(r.get('time_unix_nano'))}  {str(r.get('level') or ''):<5} {marker} {str(name):<34} "
        f"{one_line(r.get('body'), 200)}{tail}\n"
    )


# ── the per-kind summary ──────────────────────────────────────────────────

Render = Callable[[Any], Optional[str]]


class Field(NamedTuple):
    """One summary part: the first non-empty value of ``keys`` rendered by
    ``render``, which returns ``None`` to leave the part out."""

    keys: Tuple[str, ...]
    render: Render


def _text(template: str = "{}") -> Render:
    """``template`` with the value, for a truthy value."""
    return lambda v: template.format(v) if v else None


def _flag(word: str) -> Render:
    """The fixed ``word`` for a truthy value."""
    return lambda v: word if v else None


def _num(template: str, nonzero: bool = False, fmt: Callable[[Any], str] = fmt_num) -> Render:
    """``template`` with ``fmt`` of the numeric value; ``nonzero`` also drops 0."""

    def render(v: Any) -> Optional[str]:
        n = optional_number(v)
        if n is None or (nonzero and not n):
            return None
        return template.format(fmt(n))

    return render


def _clip(limit: int) -> Render:
    return lambda v: one_line(v, limit) if v else None


def _finish(v: Any) -> Optional[str]:
    fin = _listish(v)
    if isinstance(fin, list):
        fin = ",".join(str(x) for x in fin)
    return str(fin) if fin else None


# The trace totals (``trace_totals``) enter the root's summary between the
# turn counters and its final status; they are looked up under these keys.
TRACE_TOKENS = "trace.tokens"
TRACE_COST = "trace.cost"

_TURN_FIELDS: Tuple[Field, ...] = (
    Field(("hermes.turn.number",), _num("turn {}")),
    Field(("hermes.turn.tool_count",), _num("{} tools")),
    Field(("hermes.turn.api_call_count",), _num("{} api calls")),
    Field((TRACE_TOKENS,), _num("{} tok", nonzero=True)),
    Field((TRACE_COST,), _num("{}", nonzero=True, fmt=fmt_cost)),
    Field(("hermes.turn.final_status", "hermes.turn.exit_reason"), _text()),
    Field(("hermes.session.interrupted",), _flag("interrupted")),
    Field(("hermes.platform",), _text()),
)

SUMMARY_FIELDS: Dict[str, Tuple[Field, ...]] = {
    "agent": _TURN_FIELDS,
    "cron": _TURN_FIELDS,
    "session": _TURN_FIELDS,
    "llm": (
        Field(_sc.PROVIDER_KEYS, _text()),
        Field(("llm.request.message_count", "hermes.conversation.message_count"), _num("{} msgs")),
    ),
    "api": (
        Field(_sc.REASONING_TOKEN_KEYS, _num("{} reasoning", nonzero=True)),
        Field(_sc.CACHE_READ_TOKEN_KEYS, _num("{} cached", nonzero=True)),
        Field((_sc.COST_KEY,), _num("{}", fmt=fmt_cost)),
        Field(_sc.FINISH_REASON_KEYS, _finish),
        Field(("llm.response.tool_calls",), _num("{} tool calls", nonzero=True)),
        Field(("hermes.retry.count",), _num("retry {}", nonzero=True)),
    ),
    "tool": (
        Field(("hermes.tool.outcome",), _text()),
        Field(("hermes.tool.command", "hermes.tool.target"), _clip(48)),
        Field(("hermes.tool.blocked_by",), _text("blocked by {}")),
    ),
    "approval": (
        Field(("hermes.approval.choice",), _text()),
        Field(("hermes.approval.decided_by",), _text("by {}")),
        Field(("hermes.approval.timed_out",), _flag("timed out")),
    ),
    "human_input": (
        Field(("hermes.human_input.kind",), _text()),
        Field(("hermes.human_input.outcome",), _text()),
        Field(("hermes.human_input.wait_s",), _num("waited {} s")),
    ),
    "subagent": (
        Field(("hermes.subagent.role",), _text()),
        Field(("hermes.subagent.status",), _text()),
        Field(("hermes.subagent.goal",), _clip(48)),
    ),
    "skill": (
        Field(("hermes.skill.source",), _text()),
        Field(("hermes.skill.result_status",), lambda v: str(v) if v and v != "success" else None),
    ),
}


def span_summary(span: Dict[str, Any], totals: Optional[Dict[str, Optional[float]]] = None) -> str:
    """The per-kind one-liner shown to the right of a tree node."""
    a = span.get("attributes") or {}
    kind = _sc.span_kind(span.get("name") or "", a)
    parts: List[str] = []
    if kind in TURN_KINDS:
        # The session kind only when it says more than the span name and platform do.
        sk = a.get("hermes.session.kind")
        if sk and sk not in (kind, "session", "agent", "cron", a.get("hermes.platform")):
            parts.append(str(sk))
    elif kind == "api":
        i = optional_number(_sc.first(a, _sc.INPUT_TOKEN_KEYS))
        o = optional_number(_sc.first(a, _sc.OUTPUT_TOKEN_KEYS))
        if i is not None or o is not None:
            parts.append(f"{fmt_num(i)} → {fmt_num(o)} tok")
    lookup = a
    if totals:
        lookup = dict(a)
        lookup[TRACE_TOKENS] = totals.get("tokens")
        lookup[TRACE_COST] = totals.get("cost")
    for f in SUMMARY_FIELDS.get(kind, ()):
        v = _sc.first(lookup, f.keys)
        text = f.render(v) if v is not None else None
        if text:
            parts.append(text)
    if span.get("status") == "ERROR":
        msg = span.get("status_message") or a.get("exception.message") or a.get("error.message")
        parts.append("ERROR" + (f": {one_line(msg, 60)}" if msg else ""))
    return " · ".join(parts)


# ── tree rendering ────────────────────────────────────────────────────────


def build_tree(spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Roots (spans whose parent is absent) with ``children`` nested, start-ordered."""
    by_id = {s.get("span_id"): dict(s, children=[]) for s in spans if s.get("span_id")}
    roots: List[Dict[str, Any]] = []
    for node in by_id.values():
        parent = node.get("parent_span_id")
        if parent and parent in by_id and parent != node.get("span_id"):
            by_id[parent]["children"].append(node)
        else:
            roots.append(node)

    def key(n: Dict[str, Any]) -> Tuple[int, str]:
        return (int(n.get("start_time_unix_nano") or 0), str(n.get("name") or ""))

    def sort(nodes: List[Dict[str, Any]]) -> None:
        nodes.sort(key=key)
        for n in nodes:
            sort(n["children"])

    sort(roots)
    return roots


def _bar(span: Dict[str, Any], t0: int, t1: int, width: int) -> str:
    if t1 <= t0:
        return "▇" * width
    s = int(span.get("start_time_unix_nano") or t0)
    e = int(span.get("end_time_unix_nano") or s)
    span_w = max(t1 - t0, 1)
    a = int((s - t0) / span_w * width)
    b = int((e - t0) / span_w * width)
    a = min(max(a, 0), width - 1)
    b = min(max(b, a + 1), width)
    return " " * a + "▇" * (b - a) + " " * (width - b)


def render_tree(
    spans: List[Dict[str, Any]],
    out: TextIO,
    width: int = 110,
    show_attrs: bool = False,
    show_io: bool = False,
    io_chars: int = 400,
    bars: bool = True,
) -> None:
    """The README-style tree: ``├──``/``└──`` guides, duration, waterfall bar, summary."""
    if not spans:
        out.write("(trace has no spans)\n")
        return
    roots = build_tree(spans)
    totals = trace_totals(spans)
    t0 = min(int(s.get("start_time_unix_nano") or 0) for s in spans)
    t1 = max(int(s.get("end_time_unix_nano") or s.get("start_time_unix_nano") or 0) for s in spans)

    rows: List[Tuple[str, Dict[str, Any]]] = []

    def walk(node: Dict[str, Any], prefix: str, is_last: bool, is_root: bool) -> None:
        if is_root:
            label = str(node.get("name") or "")
            child_prefix = ""
        else:
            label = prefix + ("└── " if is_last else "├── ") + str(node.get("name") or "")
            child_prefix = prefix + ("    " if is_last else "│   ")
        rows.append((label, node))
        kids = node["children"]
        for i, k in enumerate(kids):
            walk(k, child_prefix, i == len(kids) - 1, False)

    for i, r in enumerate(roots):
        walk(r, "", i == len(roots) - 1, True)

    label_w = min(max(len(lbl) for lbl, _ in rows), max(24, width - 48))
    dur_w = 9
    for label, node in rows:
        dur = node.get("duration_ms")
        if dur is None:
            s, e = node.get("start_time_unix_nano"), node.get("end_time_unix_nano")
            dur = (int(e) - int(s)) / 1e6 if s and e else None
        lbl = label if len(label) <= label_w else label[: label_w - 1] + "…"
        summary = span_summary(node, totals if node is roots[0] else None)
        parts = [lbl.ljust(label_w), fmt_ms(dur).rjust(dur_w)]
        if bars:
            parts.append(_bar(node, t0, t1, TREE_BAR_WIDTH))
        line = "  ".join(parts)
        # The guide prefix of this node's children, so continuation lines
        # (summary overflow, --io, --attrs) sit inside the tree.
        guide_len = len(label) - len(label.lstrip("│ ├└─"))
        indent = " " * (guide_len + 4)
        if summary and len(line) + 2 + len(summary) <= width:
            out.write((line + "  " + summary).rstrip() + "\n")
        else:
            out.write(line.rstrip() + "\n")
            if summary:
                out.write(f"{indent}↳ {summary}\n")
        a = node.get("attributes") or {}
        if show_io:
            for key_set, tag in ((_sc.INPUT_CONTENT_KEYS, "in "), (_sc.OUTPUT_CONTENT_KEYS, "out")):
                v = _sc.first(a, key_set)
                if v is not None:
                    text = v if isinstance(v, str) else json.dumps(v, default=str)
                    clipped = (
                        text
                        if len(text) <= io_chars
                        else text[:io_chars] + f"… (+{len(text) - io_chars} chars)"
                    )
                    for j, ln in enumerate(clipped.splitlines() or [""]):
                        out.write(f"{indent}{tag if j == 0 else '   '} │ {ln}\n")
        if show_attrs:
            for k in sorted(a):
                out.write(f"{indent}{k} = {one_line(a[k], 160)}\n")

    tok, cost = totals.get("tokens"), totals.get("cost")
    tail = [f"{len(spans)} spans", fmt_ms((t1 - t0) / 1e6 if t1 > t0 else None)]
    if tok:
        tail.append(f"{fmt_num(tok)} tokens")
    if cost:
        tail.append(fmt_cost(cost))
    errors = sum(1 for s in spans if s.get("status") == "ERROR")
    if errors:
        tail.append(f"{errors} error{'s' if errors != 1 else ''}")
    out.write("── " + " · ".join(tail) + "\n")
