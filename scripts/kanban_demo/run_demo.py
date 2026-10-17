#!/usr/bin/env python3
"""Exercise Hermes' kanban board end to end and read the telemetry back.

    uv run --extra dev python scripts/kanban_demo/run_demo.py --profile minimal
    uv run --extra dev python scripts/kanban_demo/run_demo.py --profile minimal \
        --jaeger-url http://localhost:16686 --runaway-seconds 45

Three cards go onto a **scratch board** (its own SQLite file under the output
directory, so the real board under ``~/.hermes`` is never touched):

* ``complete``  the worker reads the card and calls ``kanban_complete``
* ``block``     the worker calls ``kanban_block`` with a reason
* ``runaway``   the worker starts a long sleep and never finishes; the card's
                runtime cap makes the dispatcher kill it and auto-block the card

``hermes -p <profile> kanban dispatch`` is run every few seconds, exactly as
the gateway's embedded dispatcher would tick, until every card is terminal.
The dispatcher-side hooks fire in those ``dispatch`` processes and the
worker-side hooks fire in each spawned worker; all of them run under the same
profile, so one live store (``<profile home>/hermes_otel_live.db``) and the
profile's configured backends receive everything.

The report (``report.md`` in the output directory) lists the board's event
timeline, every ``kanban.*`` span with its parent and status, each worker's
trace, the ``hermes.kanban.task.count`` points and the
``hermes.kanban.transition`` events, and checks a fixed set of expectations.
With ``--jaeger-url`` it also resolves each card's traces in Jaeger and prints
links. Exit code 1 when an expectation failed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from _common import profile_home  # noqa: E402

TERMINAL = {"done", "blocked", "archived"}


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# --- hermes ---------------------------------------------------------------


def hermes(
    args: List[str], env: Dict[str, str], *, timeout: float = 180
) -> subprocess.CompletedProcess:
    cmd = ["hermes", *args]
    return subprocess.run(
        cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout
    )


def hermes_json(args: List[str], env: Dict[str, str]) -> Any:
    proc = hermes(args, env)
    if proc.returncode != 0:
        sys.exit(f"hermes {' '.join(args)} failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    text = proc.stdout
    start = text.find("{")
    if start < 0:
        start = text.find("[")
    if start < 0:
        sys.exit(f"hermes {' '.join(args)}: no JSON in output:\n{text[-2000:]}")
    return json.loads(text[start:])


# --- the board ------------------------------------------------------------


def create_cards(
    profile: str, env: Dict[str, str], token: str, runaway_seconds: int
) -> Dict[str, str]:
    """Three cards; returns {role: task_id}."""
    common = [
        "-p",
        profile,
        "kanban",
        "create",
        "--assignee",
        profile,
        "--max-retries",
        "1",
        "--json",
    ]
    bodies = {
        "complete": (
            f"Telemetry demo {token}. Call kanban_show once, then immediately call kanban_complete "
            f"with summary 'demo {token} complete'. Use no other tools and do not run any command."
        ),
        "block": (
            f"Telemetry demo {token}. Call kanban_show once, then immediately call kanban_block "
            f"with reason 'demo {token} needs a human decision'. Use no other tools."
        ),
        "crash": (
            f"Telemetry demo {token}. Run exactly this shell command and nothing else: "
            "kill -9 $PPID"
        ),
        "runaway": (
            f"Telemetry demo {token}. Run the shell command `sleep 900` and wait for it to finish. "
            "Do not call kanban_complete or kanban_block; just wait."
        ),
    }
    ids: Dict[str, str] = {}
    for role, body in bodies.items():
        args = [*common, "--body", body]
        if role == "runaway":
            args += ["--max-runtime", f"{runaway_seconds}s"]
        args.append(f"otel demo {token}: {role}")
        card = hermes_json(args, env)
        ids[role] = card["id"]
        log(f"created {role:9s} {card['id']} (status {card['status']})")
    return ids


def task_rows(db: Path, ids: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = {
            r["id"]: dict(r) for r in con.execute("select id, status, current_run_id from tasks")
        }
    finally:
        con.close()
    return {role: rows.get(tid, {}) for role, tid in ids.items()}


def board_events(db: Path, ids: Dict[str, str]) -> List[Dict[str, Any]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "select kind, task_id, run_id, created_at, payload from task_events order by created_at, rowid"
        ).fetchall()
    finally:
        con.close()
    by_id = {tid: role for role, tid in ids.items()}
    out = []
    for r in rows:
        if r["task_id"] in by_id:
            out.append(
                {
                    "role": by_id[r["task_id"]],
                    "kind": r["kind"],
                    "run_id": r["run_id"],
                    "at": dt.datetime.fromtimestamp(r["created_at"]).strftime("%H:%M:%S"),
                    "payload": (r["payload"] or "")[:120],
                }
            )
    return out


def spawned_pids(db: Path) -> List[int]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("select payload from task_events where kind='spawned'").fetchall()
    finally:
        con.close()
    pids = []
    for (payload,) in rows:
        try:
            pid = json.loads(payload or "{}").get("pid")
            if isinstance(pid, int):
                pids.append(pid)
        except json.JSONDecodeError:
            pass
    return pids


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --- the telemetry --------------------------------------------------------


def live_rows(store: Path, kind: str, since_ns: int) -> List[Dict[str, Any]]:
    """Rows of one kind written since ``since_ns``, through the plugin's own
    ``LiveStore`` reader (the schema is its business, #404)."""
    if not store.exists():
        return []
    from hermes_otel import semconv
    from hermes_otel.live_store import LiveStore

    ls = LiveStore(db_path=str(store))
    try:
        rows = {"span": ls.spans, "metric": ls.metrics, "log": ls.logs}[kind]()
    finally:
        ls.close()
    out = []
    for d in rows:
        ts = int(
            d.get("end_time_unix_nano")
            or d.get("start_time_unix_nano")
            or d.get("time_unix_nano")
            or 0
        )
        if ts < since_ns:
            continue
        attrs = d.get("attributes") or {}
        d.update(
            {
                "_session_id": d.get("session_id") or semconv.first(attrs, semconv.SESSION_KEYS),
                "_trace_id": d.get("trace_id"),
                "_span_id": d.get("span_id"),
                "_parent": d.get("parent_span_id"),
                "_status": d.get("status"),
                "_name": d.get("name") or d.get("event_name") or d.get("logger"),
            }
        )
        out.append(d)
    return out


def collect(store: Path, since_ns: int, ids: Dict[str, str]) -> Dict[str, Any]:
    by_id = {tid: role for role, tid in ids.items()}
    spans = live_rows(store, "span", since_ns)
    kanban = []
    for s in spans:
        a = s.get("attributes", {})
        if s["_name"].startswith("kanban.") and a.get("hermes.kanban.task_id") in by_id:
            kanban.append(
                {
                    "role": by_id[a["hermes.kanban.task_id"]],
                    "name": s["_name"],
                    "event": a.get("hermes.kanban.event"),
                    "run_id": a.get("hermes.kanban.run_id"),
                    "status": s["_status"],
                    "root": s["_parent"] is None,
                    "session": s["_session_id"],
                    "trace": s["_trace_id"],
                    "pid": a.get("hermes.kanban.worker_pid"),
                    "exit_kind": a.get("hermes.kanban.exit_kind"),
                    "outcome": a.get("hermes.kanban.outcome"),
                    "summary": a.get("hermes.kanban.summary") or a.get("hermes.kanban.reason"),
                }
            )
    # Worker turns: every turn root stamped with one of our cards. Since #388
    # it nests under the dispatcher's kanban.claim (one trace per run), so it
    # is a root only when the worker could not join the run's trace.
    roots = [
        s
        for s in spans
        if s["_name"] in ("agent", "cron")
        and s.get("attributes", {}).get("hermes.kanban.task_id") in by_id
    ]
    traces = []
    for r in roots:
        members = [s for s in spans if s["_trace_id"] == r["_trace_id"]]
        traces.append(
            {
                "role": by_id[r["attributes"]["hermes.kanban.task_id"]],
                "trace": r["_trace_id"],
                "session": r["_session_id"],
                "run_id": r["attributes"].get("hermes.kanban.run_id"),
                "outcome": r["attributes"].get("hermes.kanban.outcome"),
                "spans": [
                    m["_name"]
                    for m in sorted(members, key=lambda m: m.get("start_time_unix_nano", 0))
                ],
            }
        )
    metrics: Dict[tuple, float] = {}
    # Board health (#397): dispatcher passes by outcome, what they did by kind.
    dispatch: Dict[str, float] = {}
    for m in live_rows(store, "metric", since_ns):
        a = m.get("attributes", {})
        if m.get("name") == "hermes.kanban.task.count":
            k = (a.get("outcome"), a.get("board"))
            metrics[k] = metrics.get(k, 0) + float(m.get("value") or 0)
        elif m.get("name") == "hermes.kanban.dispatch.count":
            k2 = f"tick:{a.get('outcome')}"
            dispatch[k2] = dispatch.get(k2, 0) + float(m.get("value") or 0)
        elif m.get("name") == "hermes.kanban.dispatch.result":
            k2 = str(a.get("kind"))
            dispatch[k2] = dispatch.get(k2, 0) + float(m.get("value") or 0)
    events = []
    events_on = False
    for e in live_rows(store, "log", since_ns):
        name = e.get("event_name") or ""
        if name.startswith("hermes."):
            events_on = True  # logs.events is on for this profile
        if name == "hermes.kanban.transition":
            a = e.get("attributes", {})
            events.append(
                {
                    "body": e.get("body"),
                    "severity": e.get("severity_text") or e.get("severity"),
                    "task": a.get("hermes.kanban.task_id"),
                }
            )
    return {
        "kanban_spans": kanban,
        "worker_traces": traces,
        "metrics": metrics,
        "dispatch": dispatch,
        "events": events,
        "events_on": events_on,
    }


def jaeger_traces(url: str, ids: Dict[str, str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for role, tid in ids.items():
        q = urllib.parse.urlencode(
            {
                "service": "hermes-agent",
                "limit": 20,
                "lookback": "2h",
                "tags": json.dumps({"hermes.kanban.task_id": tid}),
            }
        )
        try:
            with urllib.request.urlopen(f"{url.rstrip('/')}/api/traces?{q}", timeout=10) as resp:
                data = json.load(resp)["data"]
        except Exception as exc:  # noqa: BLE001 - the backend is optional
            out[role] = [f"query failed: {exc}"]
            continue
        out[role] = [t["traceID"] for t in data]
    return out


# --- expectations -----------------------------------------------------------


def check(result: Dict[str, Any], tasks: Dict[str, Dict[str, Any]]) -> List[tuple]:
    """``(id, ok, detail)`` rows."""
    ks = result["kanban_spans"]

    def have(role, name, **attrs):
        for s in ks:
            if (
                s["role"] == role
                and s["name"] == name
                and all(s.get(k) == v for k, v in attrs.items())
            ):
                return s
        return None

    rows = []
    for role in ("complete", "block", "crash", "runaway"):
        rows.append(
            (
                f"{role}: card terminal",
                tasks[role].get("status") in TERMINAL,
                tasks[role].get("status"),
            )
        )
        rows.append(
            (f"{role}: kanban.claim root span", bool(have(role, "kanban.claim", root=True)), "")
        )
        claim, spawn = have(role, "kanban.claim"), have(role, "kanban.spawn")
        rows.append(
            (
                f"{role}: kanban.spawn under the run's claim, with pid",
                bool(claim and spawn and not spawn["root"] and spawn["trace"] == claim["trace"])
                and spawn.get("pid") is not None,
                "",
            )
        )
        if role in ("complete", "block"):
            rows.append(
                (
                    f"{role}: worker turn in the claim's trace (one trace per run, #388)",
                    bool(claim)
                    and any(
                        t["role"] == role and t["trace"] == claim["trace"]
                        for t in result["worker_traces"]
                    ),
                    claim["trace"][:8] if claim else "",
                )
            )
    done = have("complete", "kanban.complete")
    rows.append(
        (
            "complete: kanban.complete in the worker's trace",
            bool(
                done
                and not done["root"]
                and any(t["trace"] == done["trace"] for t in result["worker_traces"])
            ),
            done["summary"] if done else "",
        )
    )
    rows.append(
        (
            "complete: worker root stamped (trace listed)",
            any(
                t["role"] == "complete" and t["outcome"] == "completed"
                for t in result["worker_traces"]
            ),
            "",
        )
    )
    blocked = have("block", "kanban.block")
    rows.append(
        (
            "block: kanban.block in the worker's trace, status ok",
            bool(
                blocked
                and not blocked["root"]
                and blocked["status"] != "error"
                and any(t["trace"] == blocked["trace"] for t in result["worker_traces"])
            ),
            blocked["summary"] if blocked else "",
        )
    )
    rows.append(
        (
            "block: worker root stamped blocked",
            any(
                t["role"] == "block" and t["outcome"] == "blocked" for t in result["worker_traces"]
            ),
            "",
        )
    )
    exited = have("crash", "kanban.worker_exit")
    rows.append(
        (
            "crash: kanban.worker_exit fired when the dispatcher found the worker dead",
            bool(exited),
            (
                f"exit_kind={exited['exit_kind']} outcome={exited['outcome']} status={exited['status']}"
                if exited
                else "not fired"
            ),
        )
    )
    if exited and exited["exit_kind"] in ("signaled", "nonzero_exit"):
        rows.append(
            (
                "crash: worker_exit is an ERROR span for a kill or crash",
                exited["status"] == "error",
                "",
            )
        )
    else:
        rows.append(
            (
                "crash: exit kind known",
                True,
                f"{exited['exit_kind'] if exited else '-'}: a CLI dispatch pass does not reap the worker, "
                "so the kind can be unknown (the gateway's dispatcher knows); the span then stays OK",
            )
        )
    rows.append(
        (
            "runaway: no kanban.* span after the runtime cap (Hermes fires no hook for it)",
            not have("runaway", "kanban.worker_exit") and not have("runaway", "kanban.block"),
            "the card is auto-blocked by the breaker; see the board timeline",
        )
    )
    m = result["metrics"]
    rows.append(
        (
            "metric hermes.kanban.task.count has claimed/completed/blocked points",
            all(any(k[0] == o for k in m) for o in ("claimed", "completed", "blocked")),
            str({f"{k[0]}/{k[1]}": v for k, v in m.items()}),
        )
    )
    d = result.get("dispatch", {})
    rows.append(
        (
            "metric hermes.kanban.dispatch.count/result: passes counted, 4 spawns (#397)",
            sum(v for k, v in d.items() if k.startswith("tick:")) > 0 and d.get("spawned", 0) >= 4,
            str({k: v for k, v in sorted(d.items())}),
        )
    )
    if result["events_on"]:
        rows.append(
            (
                "events hermes.kanban.transition recorded",
                len(result["events"]) > 0,
                f"{len(result['events'])} events",
            )
        )
    else:
        rows.append(
            (
                "events hermes.kanban.transition",
                True,
                "skipped: logs.events is off for this profile (no hermes.* event in the window)",
            )
        )
    return rows


# --- report -----------------------------------------------------------------


def report(
    out: Path, token: str, ids, tasks, timeline, result, checks, jaeger, jaeger_url, store, db
) -> str:
    L = [f"# Kanban telemetry demo `{token}`", ""]
    L += [f"- board: `{db}`", f"- live store: `{store}`", ""]
    L += ["## Cards", "", "| role | task | status | run |", "|---|---|---|---|"]
    for role, tid in ids.items():
        L.append(
            f"| {role} | `{tid}` | {tasks[role].get('status')} | {tasks[role].get('current_run_id')} |"
        )
    L += [
        "",
        "## Board timeline",
        "",
        "| time | card | event | run | payload |",
        "|---|---|---|---|---|",
    ]
    for e in timeline:
        L.append(
            f"| {e['at']} | {e['role']} | {e['kind']} | {e['run_id'] or ''} | `{e['payload'].replace('|', '/')}` |"
        )
    L += [
        "",
        "## `kanban.*` spans",
        "",
        "| card | span | event | run | status | root | session | pid | exit | outcome | text |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in result["kanban_spans"]:
        L.append(
            f"| {s['role']} | `{s['name']}` | {s['event']} | {s['run_id'] or ''} | {s['status']} | "
            f"{'yes' if s['root'] else 'no'} | {s['session'] or ''} | {s['pid'] or ''} | {s['exit_kind'] or ''} | "
            f"{s['outcome'] or ''} | {(s['summary'] or '')[:60]} |"
        )
    L += ["", "## Worker traces", ""]
    for t in result["worker_traces"]:
        link = f" ([Jaeger]({jaeger_url.rstrip('/')}/trace/{t['trace']}))" if jaeger_url else ""
        L.append(
            f"- **{t['role']}** run {t['run_id']} outcome `{t['outcome']}` session `{t['session']}` trace `{t['trace']}`{link}"
        )
        L.append("  - " + " → ".join(f"`{n}`" for n in t["spans"]))
    L += [
        "",
        "## Metric `hermes.kanban.task.count`",
        "",
        "| outcome | board | count |",
        "|---|---|---|",
    ]
    for (o, b), v in sorted(result["metrics"].items(), key=lambda kv: str(kv[0])):
        L.append(f"| {o} | {b} | {v:g} |")
    L += ["", "## Events `hermes.kanban.transition`", ""]
    for e in result["events"]:
        L.append(f"- {e['severity']} `{e['body']}`")
    if not result["events"]:
        L.append(
            "- none"
            + (
                ""
                if result["events_on"]
                else " (logs.events is off for this profile; spans and metrics still prove the hooks)"
            )
        )
    if jaeger:
        L += ["", "## Jaeger traces by card", ""]
        for role, tids in jaeger.items():
            L.append(
                f"- **{role}**: "
                + (
                    ", ".join(
                        f"[{t[:8]}]({jaeger_url.rstrip('/')}/trace/{t})" if len(t) > 16 else t
                        for t in tids
                    )
                    or "none"
                )
            )
    L += ["", "## Checks", "", "| check | result | detail |", "|---|---|---|"]
    for cid, ok, detail in checks:
        L.append(f"| {cid} | {'PASS' if ok else 'FAIL'} | {detail} |")
    text = "\n".join(L) + "\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    return text


# --- main -----------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--profile", default="minimal", help="Hermes profile that dispatches and works the cards"
    )
    ap.add_argument(
        "--hermes-home",
        default=None,
        help="the profile's home (default: ~/.hermes/profiles/<profile>)",
    )
    ap.add_argument(
        "--out", default=None, help="output directory (default: scripts/kanban_demo/out/<token>)"
    )
    ap.add_argument(
        "--runaway-seconds", type=int, default=45, help="runtime cap of the runaway card"
    )
    ap.add_argument(
        "--tick-seconds", type=float, default=8.0, help="seconds between dispatcher passes"
    )
    ap.add_argument("--max-wait", type=float, default=600.0, help="give up after this many seconds")
    ap.add_argument("--jaeger-url", default=None, help="also resolve each card's traces in Jaeger")
    ap.add_argument("--token", default=None)
    args = ap.parse_args()

    token = args.token or "K" + dt.datetime.now().strftime("%y%m%d-%H%M%S")
    out = Path(args.out) if args.out else HERE / "out" / token
    board_dir = out / "board"
    (board_dir / "ws").mkdir(parents=True, exist_ok=True)
    db = board_dir / "kanban.db"
    env = dict(os.environ)
    env["HERMES_KANBAN_DB"] = str(db)
    env["HERMES_KANBAN_WORKSPACES_ROOT"] = str(board_dir / "ws")
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_RUN_ID"):
        env.pop(k, None)
    home = Path(args.hermes_home).expanduser() if args.hermes_home else profile_home(args.profile)
    store = home / "hermes_otel_live.db"
    log(f"token {token}; board {db}; live store {store}")
    started_ns = time.time_ns()

    hermes(["-p", args.profile, "kanban", "init"], env)
    ids = create_cards(args.profile, env, token, args.runaway_seconds)

    deadline = time.time() + args.max_wait
    passes = 0
    while time.time() < deadline:
        passes += 1
        proc = hermes(["-p", args.profile, "kanban", "dispatch", "--json"], env, timeout=120)
        if proc.returncode != 0:
            log(f"dispatch pass {passes} failed: {proc.stderr[-400:]}")
        tasks = task_rows(db, ids)
        status = {r: t.get("status") for r, t in tasks.items()}
        log(f"dispatch pass {passes}: {status}")
        if all(s in TERMINAL for s in status.values()):
            break
        time.sleep(args.tick_seconds)
    else:
        log("gave up waiting; the report covers what landed")
        tasks = task_rows(db, ids)

    # The dispatcher kills the runaway worker itself; this is only a safety net.
    for pid in spawned_pids(db):
        if alive(pid):
            log(f"worker {pid} still alive after the board settled; terminating it")
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    time.sleep(3)  # let the last flushes land

    timeline = board_events(db, ids)
    result = collect(store, started_ns, ids)
    checks = check(result, tasks)
    jaeger = jaeger_traces(args.jaeger_url, ids) if args.jaeger_url else None
    text = report(
        out, token, ids, tasks, timeline, result, checks, jaeger, args.jaeger_url, store, db
    )
    print(text)
    failed = [c for c in checks if not c[1]]
    log(f"report: {out / 'report.md'}  ({len(checks) - len(failed)} PASS, {len(failed)} FAIL)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
