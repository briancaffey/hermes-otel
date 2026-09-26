#!/usr/bin/env python3
"""Check what a run_batch.py batch left in every telemetry source.

    python scripts/turn_batch/check_batch.py --manifest ~/.hermes/turn_batches/<token>/runs.json
    python scripts/turn_batch/check_batch.py --manifest ... --sources live,openobserve --wait 0

For every run in the manifest and every source (the live store plus each
configured backend the plugin's query CLI can read), the checker finds the
turn by its batch marker, then compares the trace, its spans and its
trace-correlated log records with the prompt's ``expect:`` block. It prints a
matrix (prompt × source) and a list of findings, writes ``report.md`` and
``report.json`` next to the manifest, and exits non-zero when a hard
expectation failed anywhere.

Everything goes through the plugin's own query CLI (the observability skill's
``otel.py``) in the profile's home, with the profile's ``.env`` loaded so the
backend adapters have their credentials: what this checks is exactly what the
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

LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40, "CRITICAL": 50}


# ── Profile plumbing ─────────────────────────────────────────────────────


def profile_home(profile: str) -> Path:
    base = (
        Path(os.environ.get("HERMES_HOME", "")).expanduser()
        if os.environ.get("HERMES_HOME")
        else Path.home() / ".hermes"
    )
    return base if profile in ("", "default") else base / "profiles" / profile


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


# ── Sources ──────────────────────────────────────────────────────────────


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


# ── Expectations ─────────────────────────────────────────────────────────


def _attr_text(span: Dict[str, Any]) -> str:
    return json.dumps(span.get("attributes") or {}, default=str)


def evaluate(
    run: Dict[str, Any],
    spans: List[Dict[str, Any]],
    logs: Optional[List[Dict[str, Any]]],
    src: Dict[str, Any],
) -> List[Tuple[str, str]]:
    """``[(level, message)]`` — level is FAIL, WARN or INFO."""
    ex = run.get("expect") or {}
    marker = run["marker"]
    out: List[Tuple[str, str]] = []
    roots = [s for s in spans if not s.get("parent_span_id")]
    tools = [s for s in spans if str(s.get("name", "")).startswith("tool.")]
    apis = [s for s in spans if str(s.get("name", "")).startswith("api.")]
    if ex.get("status"):
        got = {str(r.get("status", "")).lower() for r in roots} or {"?"}
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
        names = [str((s.get("attributes") or {}).get("tool.name") or s.get("name")) for s in tools]
        if not any(any(w in n for w in ex["tool_names_any"]) for n in names):
            out.append(("FAIL", f"tool names {names} contain none of {ex['tool_names_any']}"))
    if ex.get("span_names_any"):
        names = [str(s.get("name")) for s in spans]
        if not any(any(w in n for w in ex["span_names_any"]) for n in names):
            out.append(
                ("FAIL", f"span names {sorted(set(names))} contain none of {ex['span_names_any']}")
            )
    if ex.get("output_contains"):
        want = str(ex["output_contains"]).replace("{marker}", marker)
        if not any(want in _attr_text(s) for s in spans):
            out.append(("FAIL", f"no span attribute contains {want!r}"))
    if logs is not None:
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
        without_session = sum(1 for r in logs if not r.get("session_id"))
        if logs and without_session:
            out.append(("WARN", f"{without_session}/{len(logs)} log record(s) have no session id"))
    return out


# ── Main ─────────────────────────────────────────────────────────────────


def check(
    manifest: Dict[str, Any], cli: Cli, sources: List[Dict[str, Any]], since: str
) -> Dict[str, Any]:
    results: Dict[str, Any] = {
        "token": manifest["token"],
        "profile": manifest["profile"],
        "sources": sources,
        "runs": [],
    }
    for run in manifest["runs"]:
        entry: Dict[str, Any] = {"id": run["id"], "marker": run["marker"], "sources": {}}
        for src in sources:
            name = src["name"]
            cell: Dict[str, Any] = {
                "trace": None,
                "spans": 0,
                "tools": 0,
                "logs": None,
                "findings": [],
            }
            traces, err = cli.run(
                "traces", "--since", since, "--text", run["marker"], "--limit", "10", source=name
            )
            if err:
                cell["findings"].append(("FAIL", f"traces query failed: {err}"))
                entry["sources"][name] = cell
                continue
            traces = traces or []
            if not traces:
                cell["findings"].append(("FAIL", "no trace found for the marker"))
                entry["sources"][name] = cell
                continue
            # A backend that ignores the free-text filter returns its newest
            # traces instead; take the newest whose spans really carry the marker,
            # or whose id the live store already resolved for this turn (a
            # backend may keep the trace but drop its long input attributes).
            known = entry.get("live_trace")
            trace_id, spans, err = None, [], None
            for cand in traces:
                cid = cand.get("traceId") or cand.get("trace_id")
                detail, err = cli.run("trace", cid, source=name)
                cspans = (detail or {}).get("spans") or [] if isinstance(detail, dict) else []
                if cspans and (
                    cid == known or any(run["marker"] in _attr_text(sp) for sp in cspans)
                ):
                    trace_id, spans = cid, cspans
                    break
            if trace_id is None:
                cell["findings"].append(
                    ("FAIL", f"{len(traces)} trace(s) returned, none carries the marker in a span")
                )
                entry["sources"][name] = cell
                continue
            if len(traces) > 1:
                cell["findings"].append(
                    ("WARN", f"free-text search returned {len(traces)} traces; matched by content")
                )
            cell["trace"] = trace_id
            if name == "live":
                entry["live_trace"] = trace_id
            if err:
                cell["findings"].append(("FAIL", f"trace query failed: {err}"))
            cell["spans"] = len(spans)
            cell["tools"] = sum(1 for s in spans if str(s.get("name", "")).startswith("tool."))
            logs: Optional[List[Dict[str, Any]]] = None
            if src.get("logs"):
                logs, err = cli.run(
                    "logs", "--since", since, "--trace", trace_id, "--limit", "500", source=name
                )
                if err:
                    cell["findings"].append(("FAIL", f"logs query failed: {err}"))
                    logs = None
                else:
                    logs = logs or []
                    cell["logs"] = len(logs)
                    cell["log_levels"] = sorted({str(r.get("level", "")).upper() for r in logs})
            cell["findings"].extend(evaluate(run, spans, logs, src))
            entry["sources"][name] = cell
        results["runs"].append(entry)
    return results


def render_report(results: Dict[str, Any], manifest: Dict[str, Any]) -> str:
    names = [s["name"] for s in results["sources"]]
    lines = [f"# Batch {results['token']} · profile {results['profile']}", ""]
    lines.append(
        f"{len(manifest['runs'])} turn(s) started {manifest['started_at']}; sources: {', '.join(names)}."
    )
    lines.append("")
    lines.append(
        "Cell: spans/tools/logs found for the turn in that source; `–` = the source does not serve logs; ✗ = not found or a failed expectation."
    )
    lines.append("")
    lines.append("| prompt | " + " | ".join(names) + " |")
    lines.append("|---|" + "---|" * len(names))
    for r in results["runs"]:
        cells = []
        for n in names:
            c = r["sources"].get(n) or {}
            fails = [f for f in c.get("findings", []) if f[0] == "FAIL"]
            if not c.get("trace"):
                cells.append("✗ no trace")
                continue
            logs = "–" if c.get("logs") is None else str(c["logs"])
            mark = "✗ " if fails else "✓ "
            cells.append(f"{mark}{c['spans']}/{c['tools']}/{logs}")
        lines.append(f"| {r['id']} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    any_finding = False
    for r in results["runs"]:
        for n in names:
            for level, msg in (r["sources"].get(n) or {}).get("findings", []):
                any_finding = True
                lines.append(f"- **{level}** `{r['id']}` @ {n}: {msg}")
    if not any_finding:
        lines.append("- none: every expectation held in every source")
    lines.append("")
    lines.append("## Runs")
    lines.append("")
    for run in manifest["runs"]:
        state = "timeout" if run.get("timed_out") else f"exit {run.get('exit_code')}"
        lines.append(
            f"- `{run['id']}` · {state} · {run.get('duration_s')}s · marker `{run['marker']}`"
        )
    return "\n".join(lines) + "\n"


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--manifest", type=Path, required=True, help="runs.json written by run_batch.py"
    )
    ap.add_argument(
        "--sources",
        default="",
        help="comma-separated source names (default: live + every queryable backend)",
    )
    ap.add_argument(
        "--wait",
        type=float,
        default=45.0,
        help="seconds to wait for backend ingestion before querying",
    )
    ap.add_argument(
        "--since",
        default=None,
        help="window start for the queries (default: 10 minutes before the batch)",
    )
    ap.add_argument(
        "--python", default=None, help="interpreter with the plugin's deps (default: this one)"
    )
    args = ap.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    cli = Cli(manifest["profile"], args.python)
    sources = discover_sources(cli)
    if args.sources:
        wanted = [s.strip() for s in args.sources.split(",") if s.strip()]
        by_name = {s["name"]: s for s in sources}
        unknown = [w for w in wanted if w not in by_name]
        if unknown:
            sys.exit(f"unknown source(s): {', '.join(unknown)}; available: {', '.join(by_name)}")
        sources = [by_name[w] for w in wanted]
    if args.wait > 0:
        elapsed = time.time() - float(manifest.get("ended_unix") or manifest["started_unix"])
        remaining = max(0.0, args.wait - elapsed)
        if remaining:
            print(f"waiting {remaining:.0f}s for ingestion …", flush=True)
            time.sleep(remaining)
    since = args.since or time.strftime(
        "%Y-%m-%dT%H:%M", time.gmtime(float(manifest["started_unix"]) - 600)
    )

    results = check(manifest, cli, sources, since)
    report = render_report(results, manifest)
    out_dir = args.manifest.parent
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "report.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    print(report)
    print(f"report: {out_dir / 'report.md'}")
    failed = any(
        f[0] == "FAIL"
        for r in results["runs"]
        for c in r["sources"].values()
        for f in c.get("findings", [])
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
