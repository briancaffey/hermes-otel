#!/usr/bin/env python3
"""Check what a scenario run left in every telemetry source.

    uv run --extra dev python scripts/scenarios/check.py --manifest <profile home>/scenario_runs/<token>/runs.json
    uv run --extra dev python scripts/scenarios/check.py --manifest ... --sources live --wait 0

For every turn in the manifest and every source (the live store plus each
configured backend the plugin's query CLI can read), the checker finds the
turn's trace by its marker, then compares the trace, its spans and its
trace-correlated log records with the scenario's ``expect:`` block (a turn's
own ``expect:`` overrides keys for that turn). Then it checks the scenario's
``expect_run:`` block across all of its turns (turn numbering on a resumed
session, distinct sessions in the gateway, no log record attributed to the
wrong session, two profiles in a kanban run). It prints a matrix (turn ×
source) and the findings, writes ``report.md`` and ``report.json`` next to the
manifest, and exits non-zero when a hard expectation failed anywhere.

A source that is down is skipped and named in the report, never failed: the
harness reports what it could see. Everything goes through the plugin's own
query CLI (the observability skill's ``otel.py``) in the profile's home, with
the profile's ``.env`` loaded, so what this checks is exactly what the
dashboard and the skill can see.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from _common import profile_home  # noqa: E402
from common import render_expect  # noqa: E402

Finding = Tuple[str, str]  # (FAIL | WARN | INFO, message)
TURN_ROOT_NAMES = ("agent", "cron")
TRUE = (True, "true", "True", 1)


# ── Profile plumbing ─────────────────────────────────────────────────────


def load_env_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class Cli:
    """The plugin's query CLI, run in the profile's home."""

    def __init__(self, profile: str, python: Optional[str] = None) -> None:
        self.profile = profile
        self.home = profile_home(profile)
        self.script = (
            self.home
            / "plugins"
            / "hermes_otel"
            / "skills"
            / "observability"
            / "scripts"
            / "otel.py"
        )
        if not self.script.exists():
            sys.exit(
                f"plugin query CLI not found at {self.script}; is hermes_otel installed in profile {profile!r}?"
            )
        self.python = python or sys.executable
        self.env = {
            **os.environ,
            **load_env_file(self.home / ".env"),
            "HERMES_HOME": str(self.home),
        }
        self.env.pop("HERMES_OTEL_DEBUG", None)

    def run(
        self, *args: str, source: Optional[str] = None, timeout: float = 90.0
    ) -> Tuple[Any, Optional[str]]:
        cmd = [self.python, str(self.script), *args, "--json"]
        if source and source != "live":
            cmd += ["--source", source]
        try:
            p = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, env=self.env, check=False
            )
        except subprocess.TimeoutExpired:
            return None, "timeout"
        if p.returncode != 0:
            err = (p.stderr.strip().splitlines() or p.stdout.strip().splitlines() or ["?"])[-1]
            return None, err[:240]
        try:
            return json.loads(p.stdout), None
        except json.JSONDecodeError:
            return None, "non-JSON output"


def discover_sources(cli: Cli) -> List[Dict[str, Any]]:
    """``[{name, traces, metrics, logs}]`` from `status`: the live store plus every queryable backend."""
    sources: List[Dict[str, Any]] = [
        {"name": "live", "traces": True, "metrics": True, "logs": True}
    ]
    st, err = cli.run("status")
    if err or not isinstance(st, dict):
        return sources
    for b in st.get("backends") or st.get("available") or []:
        if not isinstance(b, dict) or not b.get("supported", b.get("queryable", True)):
            continue
        name = b.get("name") or b.get("type")
        sources.append(
            {
                "name": name,
                "traces": True,
                "metrics": bool(b.get("metrics")),
                "logs": bool(b.get("logs")),
            }
        )
    return sources


# ── Span helpers ─────────────────────────────────────────────────────────


def attrs(span: Dict[str, Any]) -> Dict[str, Any]:
    a = span.get("attributes")
    return a if isinstance(a, dict) else {}


def attr_text(span: Dict[str, Any]) -> str:
    return json.dumps(attrs(span), default=str)


def name(span: Dict[str, Any]) -> str:
    return str(span.get("name", ""))


def turn_root(spans: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The turn's own root: the outermost ``agent`` / ``cron`` span (a kanban
    run hangs the worker's ``agent`` under ``kanban.claim``, a delegation hangs
    child ``agent`` spans under the parent's), else any parentless span."""
    by_id = {s.get("span_id"): s for s in spans}

    def depth(s: Dict[str, Any]) -> int:
        d, seen = 0, set()
        while s is not None and s.get("span_id") not in seen:
            seen.add(s.get("span_id"))
            s = by_id.get(s.get("parent_span_id"))
            d += 1
        return d

    roots = [s for s in spans if name(s) in TURN_ROOT_NAMES]
    if roots:
        return min(roots, key=depth)
    parentless = [s for s in spans if not s.get("parent_span_id")]
    return parentless[0] if parentless else None


def same(got: Any, want: Any) -> bool:
    if isinstance(want, bool):
        return (got in TRUE) == want
    return str(got) == str(want)


def overlap_max(spans: List[Dict[str, Any]]) -> int:
    """Most spans running at the same instant."""
    edges: List[Tuple[int, int]] = []
    for s in spans:
        a, b = s.get("start_time_unix_nano"), s.get("end_time_unix_nano")
        if a and b:
            edges += [(int(a), 1), (int(b), -1)]
    best = cur = 0
    for _, step in sorted(edges, key=lambda e: (e[0], e[1])):
        cur += step
        best = max(best, cur)
    return best


def tools_per_response(apis: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> int:
    """Most tool spans that started after one api.* span ended and before the
    next began: the tool calls one model response asked for (Hermes may run
    them one after another, so they need not overlap in time)."""
    ends = sorted(int(a.get("end_time_unix_nano") or 0) for a in apis)
    counts: Dict[int, int] = {}
    for t in tools:
        start = int(t.get("start_time_unix_nano") or 0)
        before = [e for e in ends if e <= start]
        if before:
            counts[before[-1]] = counts.get(before[-1], 0) + 1
    return max(counts.values(), default=0)


# ── Per-turn expectations ────────────────────────────────────────────────


def evaluate(
    ex: Dict[str, Any],
    marker: str,
    spans: List[Dict[str, Any]],
    logs: Optional[List[Dict[str, Any]]],
) -> List[Finding]:
    """Compare one turn's trace (and its correlated logs, when the source
    serves logs) with an ``expect:`` block."""
    ex = render_expect(ex or {}, marker)
    out: List[Finding] = []
    parentless = [s for s in spans if not s.get("parent_span_id")]
    root = turn_root(spans)
    tools = [s for s in spans if name(s).startswith("tool.")]
    apis = [s for s in spans if name(s).startswith("api.")]

    if ex.get("status"):
        got = {str(r.get("status", "")).lower() for r in parentless} or {"?"}
        want = "error" if ex["status"] == "error" else "ok"
        if not any(g.startswith(want) for g in got):
            out.append(("FAIL", f"root status {sorted(got)}, expected {want}"))
    if ex.get("llm_calls_min") is not None and len(apis) < int(ex["llm_calls_min"]):
        out.append(("FAIL", f"{len(apis)} api.* span(s), expected ≥ {ex['llm_calls_min']}"))
    if ex.get("tools_min") is not None and len(tools) < int(ex["tools_min"]):
        out.append(("FAIL", f"{len(tools)} tool span(s), expected ≥ {ex['tools_min']}"))
    if ex.get("tools_max") is not None and len(tools) > int(ex["tools_max"]):
        out.append(("FAIL", f"{len(tools)} tool span(s), expected ≤ {ex['tools_max']}"))
    if ex.get("tool_names_any"):
        names = [str(attrs(s).get("tool.name") or name(s)) for s in tools]
        if not any(any(w in n for w in ex["tool_names_any"]) for n in names):
            out.append(("FAIL", f"tool names {names} contain none of {ex['tool_names_any']}"))
    if ex.get("tools_concurrent_min") is not None:
        got = overlap_max(tools)
        if got < int(ex["tools_concurrent_min"]):
            out.append(
                (
                    "FAIL",
                    f"at most {got} tool span(s) ran at once, expected ≥ {ex['tools_concurrent_min']}",
                )
            )
    if ex.get("tools_per_response_min") is not None:
        got = tools_per_response(apis, tools)
        if got < int(ex["tools_per_response_min"]):
            out.append(
                (
                    "FAIL",
                    f"at most {got} tool call(s) followed one api.* response, expected ≥ {ex['tools_per_response_min']}",
                )
            )
    for prefix, want in (ex.get("span_prefix_count_min") or {}).items():
        n = sum(1 for s in spans if name(s).startswith(prefix))
        if n < int(want):
            out.append(("FAIL", f"{n} span(s) named {prefix}*, expected ≥ {want}"))
    if ex.get("subagent_roots_min") is not None:
        n = sum(
            1
            for s in spans
            if name(s) == "agent" and attrs(s).get("hermes.session.is_subagent") in TRUE
        )
        if n < int(ex["subagent_roots_min"]):
            out.append(
                ("FAIL", f"{n} rejoined child root(s), expected ≥ {ex['subagent_roots_min']}")
            )
    if ex.get("agent_depth_min") is not None:
        by_id = {s.get("span_id"): s for s in spans}

        def depth(s: Optional[Dict[str, Any]]) -> int:
            d, seen = 0, set()
            while s is not None and s.get("span_id") not in seen:
                seen.add(s.get("span_id"))
                d += name(s) == "agent"
                s = by_id.get(s.get("parent_span_id"))
            return d

        got = max((depth(s) for s in spans), default=0)
        if got < int(ex["agent_depth_min"]):
            out.append(("FAIL", f"agent nesting depth {got}, expected ≥ {ex['agent_depth_min']}"))
    for key, wants in (ex.get("attr_values_min") or {}).items():
        for value, want in wants.items():
            n = sum(1 for s in spans if str(attrs(s).get(key)) == str(value))
            if n < int(want):
                out.append(("FAIL", f"{n} span(s) with {key}={value}, expected ≥ {want}"))
    if ex.get("span_names_any"):
        names = [name(s) for s in spans]
        if not any(any(w in n for w in ex["span_names_any"]) for n in names):
            out.append(
                ("FAIL", f"span names {sorted(set(names))} contain none of {ex['span_names_any']}")
            )
    if ex.get("output_contains"):
        want = str(ex["output_contains"])
        if not any(want in attr_text(s) for s in spans):
            out.append(("FAIL", f"no span attribute contains {want!r}"))
    for key, want in (ex.get("root_attrs") or {}).items():
        got = attrs(root).get(key) if root else None
        if not same(got, want):
            out.append(
                (
                    "FAIL",
                    f"turn root {name(root) if root else '?'}: {key}={got!r}, expected {want!r}",
                )
            )
    for key, want in (ex.get("span_attrs_any") or {}).items():
        hits = [s for s in spans if key in attrs(s) and str(want) in str(attrs(s)[key])]
        if not hits:
            seen = sorted({str(attrs(s)[key]) for s in spans if key in attrs(s)})[:5]
            want_txt = f"containing {want!r}" if want != "" else "present"
            out.append(("FAIL", f"no span has {key} {want_txt} (values seen: {seen or 'none'})"))
    for key in ex.get("attrs_absent") or []:
        hits = [name(s) for s in spans if attrs(s).get(key) not in (None, "", [])]
        if hits:
            out.append(("FAIL", f"{key} is set on {len(hits)} span(s) ({', '.join(hits[:3])})"))
    for key, limit in (ex.get("attr_max_len") or {}).items():
        longest = max((len(str(attrs(s)[key])) for s in spans if key in attrs(s)), default=0)
        if longest > int(limit):
            out.append(("FAIL", f"{key} is {longest} chars on some span, expected ≤ {limit}"))
    for text in ex.get("text_absent") or []:
        where = [name(s) for s in spans if text in attr_text(s)]
        if logs:
            where += [
                f"log:{r.get('logger')}"
                for r in logs
                if text in str(r.get("body", "")) or text in json.dumps(r.get("attributes") or {})
            ]
        if where:
            out.append(("FAIL", f"{text!r} found in {len(where)} place(s): {', '.join(where[:4])}"))

    if logs is not None:
        for text in ex.get("logs_text_absent") or []:
            hits = [
                str(r.get("event_name") or r.get("logger"))
                for r in logs
                if text in str(r.get("body", "")) or text in json.dumps(r.get("attributes") or {})
            ]
            if hits:
                out.append(
                    ("FAIL", f"{text!r} in {len(hits)} log record(s): {', '.join(hits[:4])}")
                )
        if ex.get("logs_min") is not None and len(logs) < int(ex["logs_min"]):
            out.append(
                ("FAIL", f"{len(logs)} trace-correlated log record(s), expected ≥ {ex['logs_min']}")
            )
        if ex.get("log_levels_any"):
            levels = {str(r.get("level", "")).upper() for r in logs}
            if not levels & {str(x).upper() for x in ex["log_levels_any"]}:
                out.append(
                    ("FAIL", f"log levels {sorted(levels)} contain none of {ex['log_levels_any']}")
                )
        events = [r for r in logs if r.get("event_name")]
        if ex.get("events_min") is not None and len(events) < int(ex["events_min"]):
            out.append(("FAIL", f"{len(events)} event record(s), expected ≥ {ex['events_min']}"))
        if ex.get("event_names_any"):
            got = sorted({str(r["event_name"]) for r in events})
            if not set(got) & set(ex["event_names_any"]):
                out.append(("FAIL", f"event names {got} contain none of {ex['event_names_any']}"))
        if ex.get("logs_only_events"):
            plain = [r for r in logs if not r.get("event_name")]
            if plain:
                loggers = sorted({str(r.get("logger")) for r in plain})[:4]
                out.append(
                    ("FAIL", f"{len(plain)} plain log record(s) exported ({', '.join(loggers)})")
                )
        if ex.get("events_max") is not None and len(events) > int(ex["events_max"]):
            out.append(("FAIL", f"{len(events)} event record(s), expected ≤ {ex['events_max']}"))
        without_session = sum(1 for r in logs if not r.get("session_id"))
        if logs and without_session:
            out.append(("WARN", f"{without_session}/{len(logs)} log record(s) have no session id"))
    return out


# ── Scenario-level expectations ──────────────────────────────────────────


def evaluate_run(ex: Dict[str, Any], turns: List[Dict[str, Any]]) -> List[Finding]:
    """``expect_run:`` across a scenario's turns, as found in one source.
    Each turn is ``{session, turn, root, spans, logs}`` (``root`` None when its
    trace was not found; those turns are reported per turn already)."""
    out: List[Finding] = []
    found = [t for t in turns if t.get("root") is not None]
    if not ex:
        return out
    if len(found) < len(turns):
        out.append(("INFO", f"{len(turns) - len(found)} turn(s) without a trace left out"))

    def root_attr(t: Dict[str, Any], key: str) -> Any:
        return attrs(t["root"]).get(key)

    sessions: Dict[int, List[Dict[str, Any]]] = {}
    for t in found:
        sessions.setdefault(int(t.get("session") or 0), []).append(t)
    if ex.get("turn_numbers"):
        want = [int(x) for x in ex["turn_numbers"]]
        for sess, ts in sorted(sessions.items()):
            got = []
            for t in sorted(ts, key=lambda t: t.get("turn") or 0):
                v = root_attr(t, "hermes.turn.number")
                got.append(int(float(v)) if v not in (None, "") else None)
            if len(ts) == len(want) and got != want:
                out.append(
                    ("FAIL", f"session {sess + 1}: hermes.turn.number {got}, expected {want}")
                )
    session_ids = [root_attr(t, "hermes.session_id") for t in found]
    if ex.get("same_session") and len(set(session_ids)) > 1:
        out.append(
            (
                "FAIL",
                f"turns ran in {len(set(session_ids))} sessions: {sorted(set(map(str, session_ids)))}",
            )
        )
    if ex.get("sessions_distinct") is not None:
        n = len({s for s in session_ids if s})
        if n < int(ex["sessions_distinct"]):
            out.append(
                ("FAIL", f"{n} distinct session id(s), expected ≥ {ex['sessions_distinct']}")
            )
    if ex.get("traces_distinct"):
        ids = [t.get("trace_id") for t in found]
        if len(set(ids)) != len(ids):
            out.append(("FAIL", f"{len(ids)} turns share {len(set(ids))} trace(s)"))
    for key, want in (ex.get("distinct_root_attr_min") or {}).items():
        vals = {str(root_attr(t, key)) for t in found if root_attr(t, key) not in (None, "")}
        if len(vals) < int(want):
            out.append(
                (
                    "FAIL",
                    f"{len(vals)} distinct {key} on the turn roots {sorted(vals)}, expected ≥ {want}",
                )
            )
    if ex.get("no_cross_session_logs"):
        bad = 0
        for t in found:
            own = root_attr(t, "hermes.session_id")
            bad += sum(
                1
                for r in t.get("logs") or []
                if r.get("session_id") and own and r["session_id"] != own
            )
        if bad:
            out.append(("FAIL", f"{bad} log record(s) carry another turn's session id"))
    if ex.get("log_attribution_in"):
        allowed = set(ex["log_attribution_in"])
        seen: Dict[str, int] = {}
        for t in found:
            for r in t.get("logs") or []:
                a = str((r.get("attributes") or {}).get("hermes.log.attribution") or "none")
                seen[a] = seen.get(a, 0) + 1
        bad = {k: v for k, v in seen.items() if k not in allowed}
        if bad:
            out.append(("FAIL", f"log attribution {bad} outside {sorted(allowed)}"))
    return out


# ── Main ─────────────────────────────────────────────────────────────────


def turns_of_run(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A manifest entry's turns; a pre-harness (turn_batch) entry is one turn."""
    turns = run.get("turns")
    if turns:
        return turns
    return [{k: run.get(k) for k in ("marker", "exit_code", "timed_out", "duration_s")}]


def find_trace(
    cli: Cli, source: str, marker: str, since: str, known: Optional[str]
) -> Tuple[Optional[str], List[Dict[str, Any]], List[Finding]]:
    findings: List[Finding] = []
    # The live store resolved the turn already: ask the backend for that trace
    # id directly. Several backends ignore the free-text filter (Jaeger,
    # Zipkin, Victoria), and a search would only see their newest traces.
    if known and source != "live":
        detail, err = cli.run("trace", known, source=source)
        spans = (detail or {}).get("spans") or [] if isinstance(detail, dict) else []
        if spans:
            return known, spans, findings
    traces, err = cli.run(
        "traces", "--since", since, "--text", marker, "--limit", "10", source=source
    )
    if err:
        return None, [], [("FAIL", f"traces query failed: {err}")]
    traces = traces or []
    if not traces:
        return None, [], [("FAIL", "no trace found for the marker")]
    # A turn's auxiliary calls (title generation) repeat the prompt in their
    # own one-span traces, and every later turn of a session repeats it in its
    # conversation history; the turn's own trace is the earliest turn root
    # that carries the marker.
    traces.sort(
        key=lambda t: (
            str(t.get("rootKind") or "") in ("api", "llm"),
            t.get("startNs") or 0,
        )
    )
    for cand in traces:
        cid = cand.get("traceId") or cand.get("trace_id")
        detail, err = cli.run("trace", cid, source=source)
        spans = (detail or {}).get("spans") or [] if isinstance(detail, dict) else []
        # A backend that ignores the free-text filter returns its newest traces
        # instead; take one whose spans really carry the marker, or whose id
        # the live store already resolved (a backend may drop long attributes).
        if spans and (cid == known or any(marker in attr_text(sp) for sp in spans)):
            if err:
                findings.append(("FAIL", f"trace query failed: {err}"))
            return cid, spans, findings
    return (
        None,
        [],
        [("FAIL", f"{len(traces)} trace(s) returned, none carries the marker in a span")],
    )


def metric_total(
    cli: Cli, source: str, name: str, since: str
) -> Tuple[Optional[float], Optional[str]]:
    """The instrument's window total from the query CLI's ``metrics`` command
    (the sum of every series' buckets), or ``(None, error)``."""
    res, err = cli.run("metrics", name, "--since", since, "--agg", "sum", source=source)
    if err:
        return None, err
    series = (res or {}).get("series") or {} if isinstance(res, dict) else {}
    return float(sum(v for vals in series.values() for v in vals if v is not None)), None


def evaluate_metrics(ex: Dict[str, Any], cli: Cli, source: str, since: str) -> List[Finding]:
    """``metrics_min``: the instrument's total since the run started, in a
    source that serves metrics. Metrics carry no trace id, so this is per run
    window, not per turn; the live store counts points, a backend reads its
    own samples."""
    out: List[Finding] = []
    for name, want in (ex.get("metrics_min") or {}).items():
        total, err = metric_total(cli, source, str(name), since)
        if err:
            out.append(("FAIL", f"metrics query for {name} failed: {err}"))
        elif total < float(want):
            out.append(("FAIL", f"{name} totals {total:g} in the run window, expected ≥ {want}"))
    return out


def check(
    manifest: Dict[str, Any],
    clis: Dict[str, Cli],
    sources: List[Dict[str, Any]],
    since: str,
) -> Dict[str, Any]:
    results: Dict[str, Any] = {
        "token": manifest["token"],
        "profile": manifest["profile"],
        "sources": sources,
        "runs": [],
    }
    base_profile = manifest["profile"]
    for run in manifest["runs"]:
        entry: Dict[str, Any] = {
            "id": run["id"],
            "mode": run.get("mode", "cli"),
            "turns": [],
            "run": {},
        }
        per_source_turns: Dict[str, List[Dict[str, Any]]] = {s["name"]: [] for s in sources}
        for turn in turns_of_run(run):
            marker = turn["marker"]
            ex = {**(run.get("expect") or {}), **(turn.get("expect") or {})}
            tentry: Dict[str, Any] = {"marker": marker, "sources": {}}
            cli = clis.get(turn.get("profile") or base_profile) or clis[base_profile]
            known: Optional[str] = None
            for src in sources:
                sname = src["name"]
                cell: Dict[str, Any] = {
                    "trace": None,
                    "spans": 0,
                    "tools": 0,
                    "logs": None,
                    "findings": [],
                }
                tentry["sources"][sname] = cell
                if src.get("down"):
                    cell["skipped"] = "source down"
                    continue
                if turn.get("skipped"):
                    cell["findings"].append(("FAIL", f"turn did not run: {turn['skipped']}"))
                    per_source_turns[sname].append({**turn, "root": None})
                    continue
                trace_id, spans, finds = find_trace(cli, sname, marker, since, known)
                cell["findings"].extend(finds)
                if trace_id is None:
                    per_source_turns[sname].append({**turn, "root": None})
                    continue
                if sname == "live":
                    known = trace_id
                cell["trace"] = trace_id
                cell["spans"] = len(spans)
                cell["tools"] = sum(1 for s in spans if name(s).startswith("tool."))
                logs: Optional[List[Dict[str, Any]]] = None
                if src.get("logs"):
                    logs, err = cli.run(
                        "logs",
                        "--since",
                        since,
                        "--trace",
                        trace_id,
                        "--limit",
                        "500",
                        source=sname,
                    )
                    if err:
                        cell["findings"].append(("FAIL", f"logs query failed: {err}"))
                        logs = None
                    else:
                        logs = logs or []
                        cell["logs"] = len(logs)
                cell["findings"].extend(evaluate(ex, marker, spans, logs))
                if src.get("metrics"):
                    cell["findings"].extend(evaluate_metrics(ex, cli, sname, since))
                per_source_turns[sname].append(
                    {
                        **turn,
                        "trace_id": trace_id,
                        "root": turn_root(spans),
                        "spans": spans,
                        "logs": logs,
                    }
                )
            entry["turns"].append(tentry)
        for src in sources:
            if src.get("down"):
                continue
            entry["run"][src["name"]] = evaluate_run(
                run.get("expect_run") or {}, per_source_turns[src["name"]]
            )
        if run.get("known_issue"):
            mark_known(entry, str(run["known_issue"]))
        results["runs"].append(entry)
    return results


def mark_known(entry: Dict[str, Any], issue: str) -> None:
    """A scenario tagged ``known_issue`` keeps its findings visible but does not
    fail the run: each FAIL becomes KNOWN. When nothing failed, say the issue
    no longer reproduces so the tag can go."""
    hit = False
    cells = [c for t in entry["turns"] for c in t["sources"].values()]
    lists = [c.get("findings", []) for c in cells] + list(entry["run"].values())
    for findings in lists:
        for i, (level, msg) in enumerate(findings):
            if level == "FAIL":
                findings[i] = ("KNOWN", f"{msg} (known issue {issue})")
                hit = True
    if not hit and lists:
        lists[0].append(
            ("INFO", f"known issue {issue} no longer reproduces; drop the known_issue tag")
        )


def cell_text(c: Dict[str, Any]) -> str:
    if c.get("skipped"):
        return "– down"
    fails = [f for f in c.get("findings", []) if f[0] == "FAIL"]
    known = [f for f in c.get("findings", []) if f[0] == "KNOWN"]
    if not c.get("trace"):
        return "≈ no trace" if known else "✗ no trace"
    if known and not fails:
        logs = "–" if c.get("logs") is None else str(c["logs"])
        return f"≈ {c['spans']}/{c['tools']}/{logs}"
    logs = "–" if c.get("logs") is None else str(c["logs"])
    return f"{'✗ ' if fails else '✓ '}{c['spans']}/{c['tools']}/{logs}"


def render_report(results: Dict[str, Any], manifest: Dict[str, Any]) -> str:
    names = [s["name"] for s in results["sources"]]
    down = [s["name"] for s in results["sources"] if s.get("down")]
    lines = [f"# Scenarios {results['token']} · profile {results['profile']}", ""]
    lines.append(
        f"{len(manifest['runs'])} scenario(s) started {manifest['started_at']}; sources: {', '.join(names)}."
    )
    if down:
        lines.append(f"Skipped (not answering): {', '.join(down)}.")
    lines += [
        "",
        "Cell: spans/tools/logs found for the turn in that source; `–` = the source does not serve logs; "
        "✗ = not found or a failed expectation; ≈ = fails only on a tracked known issue. "
        "A `run` row is the scenario-level check across its turns.",
        "",
        "| scenario | turn | " + " | ".join(names) + " |",
        "|---|---|" + "---|" * len(names),
    ]
    for r in results["runs"]:
        multi = len(r["turns"]) > 1
        for t in r["turns"]:
            label = t["marker"].removeprefix(f"{results['token']}-{r['id']}").lstrip("-") or "1"
            cells = [cell_text(t["sources"].get(n) or {}) for n in names]
            lines.append(f"| {r['id']} | {label} | " + " | ".join(cells) + " |")
        if r["run"] and (multi or any(r["run"].values())):
            cells = []
            for n in names:
                fr = r["run"].get(n)
                if fr is None:
                    cells.append("– down")
                else:
                    cells.append(
                        "✗"
                        if any(f[0] == "FAIL" for f in fr)
                        else "≈" if any(f[0] == "KNOWN" for f in fr) else "✓"
                    )
            lines.append(f"| {r['id']} | run | " + " | ".join(cells) + " |")
    lines += ["", "## Findings", ""]
    any_finding = False
    for r in results["runs"]:
        for t in r["turns"]:
            for n in names:
                for level, msg in (t["sources"].get(n) or {}).get("findings", []):
                    any_finding = True
                    lines.append(
                        f"- **{level}** `{t['marker'].removeprefix(results['token'] + '-')}` @ {n}: {msg}"
                    )
        for n, fr in r["run"].items():
            for level, msg in fr:
                any_finding = True
                lines.append(f"- **{level}** `{r['id']}` (run) @ {n}: {msg}")
    if not any_finding:
        lines.append("- none: every expectation held in every source")
    lines += ["", "## Turns", ""]
    for run in manifest["runs"]:
        for t in turns_of_run(run):
            state = (
                f"skipped: {t['skipped']}"
                if t.get("skipped")
                else "timeout" if t.get("timed_out") else f"exit {t.get('exit_code')}"
            )
            lines.append(
                f"- `{t['marker']}` · {run.get('mode', 'cli')} · {state} · {t.get('duration_s')}s"
            )
    return "\n".join(lines) + "\n"


def has_failure(results: Dict[str, Any]) -> bool:
    for r in results["runs"]:
        for t in r["turns"]:
            for c in t["sources"].values():
                if any(f[0] == "FAIL" for f in c.get("findings", [])):
                    return True
        for fr in r["run"].values():
            if any(f[0] == "FAIL" for f in fr):
                return True
    return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--manifest", type=Path, required=True, help="runs.json written by run.py")
    ap.add_argument(
        "--sources",
        default="",
        help="comma-separated source names (default: live + every queryable backend)",
    )
    ap.add_argument(
        "--wait",
        type=float,
        default=30.0,
        help="seconds to wait for backend ingestion before querying",
    )
    ap.add_argument(
        "--since",
        default=None,
        help="window start for the queries (default: 10 minutes before the run)",
    )
    ap.add_argument(
        "--python", default=None, help="interpreter with the plugin's deps (default: this one)"
    )
    args = ap.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    profiles = {manifest["profile"]} | {
        t.get("profile") for r in manifest["runs"] for t in turns_of_run(r) if t.get("profile")
    }
    clis = {p: Cli(p, args.python) for p in sorted(profiles)}
    base = clis[manifest["profile"]]
    sources = discover_sources(base)
    if args.sources:
        wanted = [s.strip() for s in args.sources.split(",") if s.strip()]
        by_name = {s["name"]: s for s in sources}
        unknown = [w for w in wanted if w not in by_name]
        if unknown:
            sys.exit(f"unknown source(s): {', '.join(unknown)}; available: {', '.join(by_name)}")
        sources = [by_name[w] for w in wanted]
    if args.wait > 0 and any(s["name"] != "live" for s in sources):
        elapsed = time.time() - float(manifest.get("ended_unix") or manifest["started_unix"])
        remaining = max(0.0, args.wait - elapsed)
        if remaining:
            print(f"waiting {remaining:.0f}s for ingestion …", flush=True)
            time.sleep(remaining)
    since = args.since or time.strftime(
        "%Y-%m-%dT%H:%M", time.gmtime(float(manifest["started_unix"]) - 600)
    )
    # A backend that does not answer is skipped, not failed.
    for s in sources:
        if s["name"] == "live":
            continue
        _, err = base.run("traces", "--since", since, "--limit", "1", source=s["name"], timeout=30)
        if err:
            s["down"] = True
            s["down_reason"] = err
            print(f"source {s['name']} is not answering ({err}); skipping it", flush=True)

    results = check(manifest, clis, sources, since)
    report = render_report(results, manifest)
    out_dir = args.manifest.parent
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "report.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    print(report)
    print(f"report: {out_dir / 'report.md'}")
    return 1 if has_failure(results) else 0


if __name__ == "__main__":
    sys.exit(main())
