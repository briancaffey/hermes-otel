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
terminal; output goes to a ``TextIO`` the caller passes in.
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

from .live_store import (
    LiveStore,
    _default_db_path,
    span_kind,
    summarize_trace,
    trace_totals,
)

_PKG_DIR = Path(__file__).resolve().parent

# ── attribute keys, both conventions ─────────────────────────────────────
_SESSION_KEYS = ("hermes.session_id", "session.id", "session_id")
_MODEL_KEYS = ("gen_ai.request.model", "llm.model_name", "gen_ai.response.model")
_PROVIDER_KEYS = ("gen_ai.provider.name", "llm.provider", "gen_ai.system")
_IN_TOK_KEYS = ("gen_ai.usage.input_tokens", "llm.token_count.prompt")
_OUT_TOK_KEYS = ("gen_ai.usage.output_tokens", "llm.token_count.completion")
_TOTAL_TOK_KEYS = ("gen_ai.usage.total_tokens", "llm.token_count.total")
_REASON_TOK_KEYS = (
    "gen_ai.usage.reasoning.output_tokens",
    "llm.token_count.completion_details.reasoning",
)
_CACHE_READ_KEYS = (
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_read_input_tokens",
    "llm.token_count.prompt_details.cache_read",
)
_FINISH_KEYS = ("gen_ai.response.finish_reasons", "llm.response.finish_reason")
_INPUT_KEYS = ("input.value", "gen_ai.input.messages", "gen_ai.tool.call.arguments")
_OUTPUT_KEYS = ("output.value", "gen_ai.output.messages", "gen_ai.tool.call.result")
_COST_KEY = "hermes.cost.usage"

_READ_ONLY_SQL = re.compile(r"^\s*(select|with|explain|pragma)\b", re.IGNORECASE)
_SINCE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhdw])$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

TREE_BAR_WIDTH = 16


class CliError(Exception):
    """A user-facing failure; :func:`main` prints it and returns 1."""


# ── small formatters (pure) ───────────────────────────────────────────────


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


def _first(attrs: Dict[str, Any], keys: Sequence[str]) -> Any:
    for k in keys:
        v = attrs.get(k)
        if v is not None and v != "":
            return v
    return None


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _listish(v: Any) -> Any:
    """``["stop"]`` stored as a JSON string comes back as a list."""
    if isinstance(v, str) and v.startswith("[") and v.endswith("]"):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _short_id(value: Any, n: int = 8) -> str:
    s = str(value or "")
    return s[:n] if len(s) > n else s


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


# ── span normalisation ────────────────────────────────────────────────────


def _otlp_value(v: Dict[str, Any]) -> Any:
    if not isinstance(v, dict) or not v:
        return None
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return v["intValue"]
    if "doubleValue" in v:
        return v["doubleValue"]
    if "boolValue" in v:
        return v["boolValue"]
    if "arrayValue" in v:
        return [_otlp_value(x) for x in (v["arrayValue"].get("values") or [])]
    return None


def otlp_attrs_to_dict(attrs: Any) -> Dict[str, Any]:
    if isinstance(attrs, dict):
        return dict(attrs)
    out: Dict[str, Any] = {}
    for a in attrs or []:
        if isinstance(a, dict) and a.get("key"):
            out[a["key"]] = _otlp_value(a.get("value") or {})
    return out


def spans_from_otlp(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The adapters' ``{"batches": [...]}`` trace → live-store-shaped span dicts."""
    spans: List[Dict[str, Any]] = []
    for batch in payload.get("batches") or payload.get("resourceSpans") or []:
        resource = otlp_attrs_to_dict((batch.get("resource") or {}).get("attributes"))
        for scope in batch.get("scopeSpans") or []:
            for sp in scope.get("spans") or []:
                start = int(sp.get("startTimeUnixNano") or 0)
                end = int(sp.get("endTimeUnixNano") or 0)
                status = sp.get("status") or {}
                code = status.get("code") if isinstance(status, dict) else None
                attrs = otlp_attrs_to_dict(sp.get("attributes"))
                if resource.get("service.name") and "service.name" not in attrs:
                    attrs["service.name"] = resource["service.name"]
                spans.append(
                    {
                        "trace_id": sp.get("traceId"),
                        "span_id": sp.get("spanId"),
                        "parent_span_id": sp.get("parentSpanId") or None,
                        "name": sp.get("name") or "",
                        "start_time_unix_nano": start,
                        "end_time_unix_nano": end,
                        "duration_ms": (end - start) / 1e6 if start and end else None,
                        "status": {2: "ERROR", 1: "OK"}.get(code, "UNSET"),
                        "status_message": (
                            status.get("message") if isinstance(status, dict) else None
                        ),
                        "attributes": attrs,
                    }
                )
    return spans


def trace_row_from_card(card: Dict[str, Any]) -> Dict[str, Any]:
    """A search card (``{"traces": [...]}`` item) → the ``summarize_trace`` shape."""
    attrs: Dict[str, Any] = {}
    for ss in card.get("spanSets") or []:
        for sp in ss.get("spans") or []:
            attrs.update(otlp_attrs_to_dict(sp.get("attributes")))
    start = int(card.get("startTimeUnixNano") or 0)
    dur = _num(card.get("durationMs")) or 0.0
    return {
        "traceId": card.get("traceID") or card.get("traceId"),
        "rootName": card.get("rootTraceName") or "",
        "rootKind": span_kind(card.get("rootTraceName") or "", attrs),
        "service": card.get("rootServiceName") or attrs.get("service.name") or "hermes",
        "startNs": start,
        "endNs": start + int(dur * 1e6),
        "durationMs": dur,
        "spanCount": card.get("spanCount"),
        "model": _first(attrs, _MODEL_KEYS),
        "tokens": _num(_first(attrs, _TOTAL_TOK_KEYS)),
        "cost": _num(attrs.get(_COST_KEY)),
        "error": str(attrs.get("status") or "").lower() == "error" or bool(attrs.get("error")),
        "session": _first(attrs, _SESSION_KEYS),
        "turn": _num(attrs.get("hermes.turn.number")),
        "platform": attrs.get("hermes.platform"),
    }


# ── sources ───────────────────────────────────────────────────────────────


class LiveSource:
    """Queries over the SQLite live store."""

    label = "live"

    def __init__(self, store: LiveStore):
        self.store = store

    def traces(
        self, f: argparse.Namespace, start_ns: Optional[int], end_ns: Optional[int]
    ) -> List[Dict[str, Any]]:
        res = self.store.query_traces(
            start_ns=start_ns,
            end_ns=end_ns,
            session=f.session or None,
            status=f.status or None,
            name=f.name or None,
            text=f.text or None,
            limit=f.limit,
            min_duration_ms=f.min_duration or None,
            model=f.model or None,
            tool=f.tool or None,
        )
        return res.get("traces") or []

    def trace_spans(self, trace_id: str) -> List[Dict[str, Any]]:
        return self.store.trace(trace_id).get("spans") or []

    def spans_for_traces(self, ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        return self.store.spans_for_traces(list(ids))

    def sessions(
        self, start_ns: Optional[int], end_ns: Optional[int], limit: int
    ) -> List[Dict[str, Any]]:
        return (
            self.store.sessions(start_ns=start_ns, end_ns=end_ns, limit=limit).get("sessions") or []
        )

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        return self.store.metric_names()

    def metric_buckets(
        self, name: str, start_s: int, end_s: int, bucket_s: int, group_by: Optional[str], agg: str
    ) -> Dict[str, Any]:
        return self.store.metric_buckets(
            name, int(start_s * 1e9), int(end_s * 1e9), bucket_s, group_by, agg
        )

    def logs(
        self, f: argparse.Namespace, start_ns: Optional[int], end_ns: Optional[int]
    ) -> List[Dict[str, Any]]:
        return self.store.query_logs(
            trace_id=f.trace or None,
            session=f.session or None,
            level_min=_LEVELS.get((f.level or "").lower()) or None,
            logger=f.logger or None,
            text=f.text or None,
            start_ns=start_ns,
            end_ns=end_ns,
            limit=f.limit,
        )


_LEVELS = {"debug": 10, "info": 20, "warning": 30, "warn": 30, "error": 40, "critical": 50}


def _import_backends():
    """The dashboard's ``backends`` package, imported the way ``plugin_api.py`` does."""
    try:
        import yaml  # noqa: F401  (the adapters read the config file with it)
    except ImportError:
        raise CliError(
            "backend queries read the config file with pyyaml, which this interpreter lacks. "
            "Run the script with the interpreter Hermes uses (`hermes --version` prints the "
            "install directory; use its venv/bin/python) or `pip install pyyaml`."
        )
    dash = _PKG_DIR / "dashboard"
    if str(dash) not in sys.path:
        sys.path.insert(0, str(dash))
    import backends  # noqa: E402  (path shim above)

    return backends


class BackendSource:
    """Queries through a dashboard adapter (Phoenix, Langfuse, Jaeger, …)."""

    def __init__(self, adapter: Any, label: str):
        self.adapter = adapter
        self.label = label

    def traces(
        self, f: argparse.Namespace, start_ns: Optional[int], end_ns: Optional[int]
    ) -> List[Dict[str, Any]]:
        backends = _import_backends()
        attr_equals: Dict[str, str] = {}
        if f.model:
            attr_equals["llm.model_name"] = f.model
        if f.session:
            attr_equals["hermes.session_id"] = f.session
        if f.tool:
            attr_equals["tool.name"] = f.tool
        sf = backends.base.StructuredFilter(
            name_regex=f.name or None,
            attr_equals=attr_equals,
            min_duration_ms=int(f.min_duration) if f.min_duration else None,
            status=f.status or None,
            free_text=f.text or None,
            roots_only=not f.tool,
        )
        start_s = int((start_ns or 0) / 1e9) or int(time.time() - 3600)
        end_s = int((end_ns or 0) / 1e9) or int(time.time())
        res = self.adapter.search(sf, start_s, end_s, f.limit)
        return [trace_row_from_card(c) for c in (res.get("traces") or [])]

    def trace_spans(self, trace_id: str) -> List[Dict[str, Any]]:
        return spans_from_otlp(self.adapter.get_trace(trace_id) or {})

    def spans_for_traces(self, ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        return {t: self.trace_spans(t) for t in ids}

    def sessions(
        self, start_ns: Optional[int], end_ns: Optional[int], limit: int
    ) -> List[Dict[str, Any]]:
        raise CliError(
            f"sessions is a live-store view; {self.label} has no session index (use traces --session)"
        )

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        if not getattr(self.adapter, "supports_metrics", False):
            raise CliError(f"backend {self.label!r} does not serve metrics; use the live store")
        return self.adapter.metric_names(start_s, end_s)

    def metric_buckets(
        self, name: str, start_s: int, end_s: int, bucket_s: int, group_by: Optional[str], agg: str
    ) -> Dict[str, Any]:
        if not getattr(self.adapter, "supports_metrics", False):
            raise CliError(f"backend {self.label!r} does not serve metrics; use the live store")
        return self.adapter.metrics_query(name, start_s, end_s, bucket_s, group_by, agg)

    def logs(
        self, f: argparse.Namespace, start_ns: Optional[int], end_ns: Optional[int]
    ) -> List[Dict[str, Any]]:
        if not getattr(self.adapter, "supports_logs", False):
            raise CliError(f"backend {self.label!r} does not serve logs; use the live store")
        backends = _import_backends()
        lf = backends.base.LogFilter(
            trace_id=f.trace or None,
            session=f.session or None,
            min_level=_LEVELS.get((f.level or "").lower(), 0),
            logger=f.logger or None,
            text=f.text or None,
        )
        start_s = int((start_ns or 0) / 1e9) or int(time.time() - 3600)
        end_s = int((end_ns or 0) / 1e9) or int(time.time())
        return self.adapter.logs_search(lf, start_s, end_s, f.limit)


def open_live_store(db_path: Optional[str]) -> LiveStore:
    path = db_path or _default_db_path()
    if not Path(path).exists():
        raise CliError(
            f"no live store at {path}. Run one Hermes turn with the plugin enabled, or set "
            "HERMES_OTEL_LIVE_DB / --db to the file (dashboard_live must be true)."
        )
    return LiveStore(db_path=path)


def resolve_source(args: argparse.Namespace) -> Any:
    """``--source live`` (default) → :class:`LiveSource`, else a backend adapter."""
    wanted = (getattr(args, "source", None) or "live").strip()
    if wanted.lower() == "live":
        return LiveSource(open_live_store(getattr(args, "db", None)))
    backends = _import_backends()
    try:
        adapter, configured, cfg_path, _pin = backends.resolve_adapter(wanted)
    except KeyError as e:
        raise CliError(f"no configured backend named {wanted!r}; configured: {e.args[0]}")
    if adapter is None:
        raise CliError(
            f"backend {wanted!r} has no query adapter; the live store or the backend UI are the options"
        )
    return BackendSource(adapter, wanted)


# ── tree rendering ────────────────────────────────────────────────────────


def _kind_of(name: str) -> str:
    return name.split(".", 1)[0] if name else ""


def span_summary(span: Dict[str, Any], totals: Optional[Dict[str, Optional[float]]] = None) -> str:
    """The per-kind one-liner shown to the right of a tree node."""
    a = span.get("attributes") or {}
    kind = _kind_of(span.get("name") or "")
    parts: List[str] = []
    if kind in ("agent", "cron", "session"):
        sk = a.get("hermes.session.kind")
        if sk and sk not in (kind, "session", "agent", "cron", a.get("hermes.platform")):
            parts.append(str(sk))
        t = a.get("hermes.turn.number")
        if t is not None:
            parts.append(f"turn {fmt_num(t)}")
        tc = a.get("hermes.turn.tool_count")
        if tc is not None:
            parts.append(f"{fmt_num(tc)} tools")
        ac = a.get("hermes.turn.api_call_count")
        if ac is not None:
            parts.append(f"{fmt_num(ac)} api calls")
        if totals:
            if totals.get("tokens"):
                parts.append(f"{fmt_num(totals['tokens'])} tok")
            if totals.get("cost"):
                parts.append(fmt_cost(totals["cost"]))
        fs = a.get("hermes.turn.final_status") or a.get("hermes.turn.exit_reason")
        if fs:
            parts.append(str(fs))
        if a.get("hermes.session.interrupted"):
            parts.append("interrupted")
        p = a.get("hermes.platform")
        if p:
            parts.append(str(p))
    elif kind == "llm":
        prov = _first(a, _PROVIDER_KEYS)
        if prov:
            parts.append(str(prov))
        mc = a.get("llm.request.message_count") or a.get("hermes.conversation.message_count")
        if mc is not None:
            parts.append(f"{fmt_num(mc)} msgs")
    elif kind == "api":
        i, o = _num(_first(a, _IN_TOK_KEYS)), _num(_first(a, _OUT_TOK_KEYS))
        if i is not None or o is not None:
            parts.append(f"{fmt_num(i)} → {fmt_num(o)} tok")
        r = _num(_first(a, _REASON_TOK_KEYS))
        if r:
            parts.append(f"{fmt_num(r)} reasoning")
        cr = _num(_first(a, _CACHE_READ_KEYS))
        if cr:
            parts.append(f"{fmt_num(cr)} cached")
        c = _num(a.get(_COST_KEY))
        if c is not None:
            parts.append(fmt_cost(c))
        fin = _listish(_first(a, _FINISH_KEYS))
        if isinstance(fin, list):
            fin = ",".join(str(x) for x in fin)
        if fin:
            parts.append(str(fin))
        tc = _num(a.get("llm.response.tool_calls"))
        if tc:
            parts.append(f"{fmt_num(tc)} tool calls")
        rc = _num(a.get("hermes.retry.count"))
        if rc:
            parts.append(f"retry {fmt_num(rc)}")
    elif kind == "tool":
        oc = a.get("hermes.tool.outcome")
        if oc:
            parts.append(str(oc))
        tgt = a.get("hermes.tool.command") or a.get("hermes.tool.target")
        if tgt:
            parts.append(one_line(tgt, 48))
        bb = a.get("hermes.tool.blocked_by")
        if bb:
            parts.append(f"blocked by {bb}")
    elif kind == "approval":
        ch = a.get("hermes.approval.choice")
        if ch:
            parts.append(str(ch))
        by = a.get("hermes.approval.decided_by")
        if by:
            parts.append(f"by {by}")
        if a.get("hermes.approval.timed_out"):
            parts.append("timed out")
    elif kind == "subagent":
        role = a.get("hermes.subagent.role")
        if role:
            parts.append(str(role))
        st = a.get("hermes.subagent.status")
        if st:
            parts.append(str(st))
        goal = a.get("hermes.subagent.goal")
        if goal:
            parts.append(one_line(goal, 48))
    elif kind == "skill":
        src = a.get("hermes.skill.source")
        if src:
            parts.append(str(src))
        rs = a.get("hermes.skill.result_status")
        if rs and rs != "success":
            parts.append(str(rs))
    if span.get("status") == "ERROR":
        msg = span.get("status_message") or a.get("exception.message") or a.get("error.message")
        parts.append("ERROR" + (f": {one_line(msg, 60)}" if msg else ""))
    return " · ".join(parts)


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
            for key_set, tag in ((_INPUT_KEYS, "in "), (_OUTPUT_KEYS, "out")):
                v = _first(a, key_set)
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
        backends = _import_backends()
        cfg_path, configured, pin = backends.load_config()
        info["config_file"] = str(cfg_path) if cfg_path else None
        info["query_backend_pin"] = pin
        for b in configured:
            cls = backends.find_adapter_class(str(b.get("type") or ""))
            info["backends"].append(
                {
                    "name": backends.backend_label(b),
                    "type": b.get("type"),
                    "endpoint": b.get("endpoint") or b.get("host") or b.get("base_url"),
                    "queryable": cls is not None,
                    "metrics": bool(getattr(cls, "supports_metrics", False)) if cls else False,
                    "logs": bool(getattr(cls, "supports_logs", False)) if cls else False,
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
            out.write(f"  {b['name']:<14} {b['type']:<12} {b['endpoint'] or '-':<48} {q}\n")
        if info.get("query_backend_pin"):
            out.write(f"  default --source: {info['query_backend_pin']} (query_backend)\n")
    return 0


def _trace_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out = []
    for t in rows:
        out.append(
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
        )
    return out


def cmd_traces(args: argparse.Namespace, out: TextIO) -> int:
    src = resolve_source(args)
    start_ns, end_ns = _window(args)
    rows = src.traces(args, start_ns, end_ns)
    if args.json:
        _emit_json(rows, out)
        return 0
    render_table(
        _trace_rows(rows),
        [
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
        ],
        out,
    )
    if rows:
        out.write(
            f"{len(rows)} trace(s), newest first; full ids with --json; `trace <id>` for the tree\n"
        )
    return 0


def _pick_trace_id(src: Any, ref: str, args: argparse.Namespace) -> str:
    """``last`` / ``last-2`` → the n-th newest trace in the window; else the id as given."""
    m = re.match(r"^last(?:-(\d+))?$", ref.strip())
    if not m:
        return ref.strip()
    n = int(m.group(1) or 1)
    ns = argparse.Namespace(
        session=getattr(args, "session", None),
        status=None,
        name=None,
        text=None,
        limit=n,
        min_duration=None,
        model=None,
        tool=None,
    )
    start_ns, end_ns = _window(args)
    rows = src.traces(ns, start_ns, end_ns)
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
    render_table(
        table,
        [
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
        ],
        out,
    )
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
        if s.get("rootKind") in ("agent", "cron", "session") or str(s.get("rootName", "")).split(
            "."
        )[0] in ("agent", "cron"):
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
            kind = _kind_of(name)
            if sp.get("status") == "ERROR":
                errors += 1
            if kind == "tool":
                row = tools.setdefault(
                    name[5:] or name, {"calls": 0, "errors": 0, "completed": 0, "ms": 0.0}
                )
                row["calls"] += 1
                oc = str(a.get("hermes.tool.outcome") or "")
                if oc == "completed":
                    row["completed"] += 1
                if sp.get("status") == "ERROR" or oc in ("error", "timeout"):
                    row["errors"] += 1
                row["ms"] += _num(sp.get("duration_ms")) or 0.0
            elif kind == "api":
                model = str(_first(a, _MODEL_KEYS) or name[4:] or "?")
                row = models.setdefault(
                    model, {"calls": 0, "in": 0.0, "out": 0.0, "cost": 0.0, "errors": 0, "ms": 0.0}
                )
                row["calls"] += 1
                row["in"] += _num(_first(a, _IN_TOK_KEYS)) or 0.0
                row["out"] += _num(_first(a, _OUT_TOK_KEYS)) or 0.0
                row["cost"] += _num(a.get(_COST_KEY)) or 0.0
                row["ms"] += _num(sp.get("duration_ms")) or 0.0
                if sp.get("status") == "ERROR":
                    row["errors"] += 1
            elif kind == "skill":
                skills[name[6:] or name] = skills.get(name[6:] or name, 0) + 1
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
    ns = argparse.Namespace(
        session=args.session,
        status=None,
        name=None,
        text=None,
        limit=args.limit,
        min_duration=None,
        model=None,
        tool=None,
    )
    rows = src.traces(ns, start_ns, end_ns)
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
    start_ns, end_ns = _window(args)
    rows = src.logs(args, start_ns, end_ns)
    if args.json:
        _emit_json(rows, out)
        return 0
    if not rows:
        out.write("(no log records; capture_logs must be true for the plugin to record any)\n")
        return 0
    for r in rows:
        out.write(
            f"{fmt_when(r.get('time_unix_nano'))}  {str(r.get('level') or ''):<8} {str(r.get('logger') or ''):<28} "
            f"{one_line(r.get('body'), 200)}"
            + (f"  [trace {_short_id(r.get('trace_id'), 12)}]" if r.get("trace_id") else "")
            + "\n"
        )
    out.write(f"{len(rows)} record(s), newest first\n")
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
