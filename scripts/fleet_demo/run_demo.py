#!/usr/bin/env python3
"""Observe a small Hermes fleet end to end and read it back from one backend (#398).

    uv run --extra dev python scripts/fleet_demo/run_demo.py --jaeger-url http://localhost:16686

Three profiles work one goal on a **scratch kanban board** (its own SQLite
file under the output directory; the real board is never touched):

* ``minimal``         dispatches, and synthesizes the result (its own model)
* ``fleet-worker``    two parallel worker cards on a cheaper model
* ``fleet-reviewer``  the verifier card on a third model

``fleet-worker`` and ``fleet-reviewer`` are created for the run by cloning
``minimal`` (``hermes profile create --clone-from minimal``), given their own
model, this checkout's plugin and minimal's ``hermes_otel.yaml`` (so every
profile exports to the same backend), and deleted afterwards unless
``--keep-profiles``. The swarm is ``hermes kanban swarm`` (workers →
verifier → synthesizer); ``hermes -p minimal kanban dispatch`` runs every few
seconds until every card is terminal.

The checks read Jaeger: every card's run is one trace (#388) rooted at
``kanban.claim``; each worker's turn root carries its own ``hermes.profile``
and ``gen_ai.request.model`` (#391); token totals per profile come from those
roots. Numbers in the report come from the backend; nothing is filled in.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path[:0] = [str(REPO), str(REPO / "scripts")]
from _common import profile_home  # noqa: E402

from hermes_otel.dashboard.backends.base import StructuredFilter  # noqa: E402
from hermes_otel.dashboard.backends.jaeger import JaegerAdapter  # noqa: E402
from hermes_otel.query_sources import spans_from_otlp  # noqa: E402

TERMINAL = {"done", "blocked", "archived"}
DEFAULT_MODELS = {
    "fleet-worker": "nvidia/nemotron-3.5-lightning-30b-a3b",
    "fleet-reviewer": "openai/gpt-oss-20b",
}


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def hermes(
    args: List[str], env: Dict[str, str], timeout: float = 180
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["hermes", *args],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# --- profiles ---------------------------------------------------------------


def make_profile(name: str, model: str, source: str, env: Dict[str, str]) -> bool:
    """Create ``name`` from ``source`` with its own model; True when created here."""
    import yaml  # type: ignore

    home = profile_home(name)
    created = False
    if not home.exists():
        proc = hermes(
            [
                "profile",
                "create",
                name,
                "--clone-from",
                source,
                "--no-alias",
                "--description",
                f"fleet demo {name.split('-')[-1]}",
            ],
            env,
        )
        if proc.returncode != 0:
            sys.exit(f"profile create {name} failed:\n{proc.stderr[-1500:]}")
        created = True
    cfg_path = home / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    m = cfg.get("model")
    if isinstance(m, dict):
        m["default"] = model
    else:
        cfg["model"] = {"default": model, "provider": "nvidia"}
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    src = profile_home(source)
    shutil.copy2(src / "hermes_otel.yaml", home / "hermes_otel.yaml")
    plugin = home / "plugins" / "hermes_otel"
    if plugin.exists():
        shutil.rmtree(plugin)
    shutil.copytree(
        REPO / "hermes_otel",
        plugin,
        ignore=shutil.ignore_patterns("__pycache__", "*.db", "debug.log"),
    )
    log(f"profile {name}: model {model}{' (created)' if created else ''}")
    return created


# --- board ------------------------------------------------------------------


def crash_note(db: Path, task_id: str) -> str:
    """The tail of a crashed worker's last output, from the board's events."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select payload from task_events where task_id=? and kind='crashed' order by rowid desc limit 1",
            (task_id,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return ""
    try:
        return str(json.loads(row[0] or "{}").get("worker_output") or "").strip()[-120:]
    except json.JSONDecodeError:
        return ""


def tasks(db: Path) -> List[Dict[str, Any]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("select id, title, assignee, status from tasks")]
    finally:
        con.close()


# --- jaeger -----------------------------------------------------------------


def runs_from_jaeger(base: str, task_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Per task id: each trace that holds its kanban.claim, summarised.

    Read through the dashboard's Jaeger adapter and decoded by the package
    (``spans_from_otlp``), so this report sees the traces exactly as the
    dashboard and ``hermes-otel query`` do.
    """
    jaeger = JaegerAdapter({"endpoint": base, "service_name": "hermes-agent"})
    now = int(time.time())
    out: Dict[str, List[Dict[str, Any]]] = {}
    for tid in task_ids:
        f = StructuredFilter(attr_equals={"hermes.kanban.task_id": tid}, roots_only=False)
        cards = jaeger.search(f, now - 2 * 3600, now, 20).get("traces") or []
        rows = []
        for card in cards:
            trace_id = card.get("traceID")
            spans = spans_from_otlp(jaeger.get_trace(trace_id))
            if "kanban.claim" not in {s["name"] for s in spans}:
                continue
            ids = {s["span_id"] for s in spans}
            worker = next(
                (
                    s
                    for s in spans
                    if s["name"] == "agent" and s["attributes"].get("hermes.kanban.task_id") == tid
                ),
                None,
            )
            wt = worker["attributes"] if worker else {}
            rows.append(
                {
                    "trace": trace_id,
                    "spans": len(spans),
                    "has_worker": worker is not None,
                    "worker_parented": bool(worker and worker["parent_span_id"] in ids),
                    "profile": wt.get("hermes.profile"),
                    "model": wt.get("gen_ai.request.model"),
                    "trigger": wt.get("hermes.turn.trigger"),
                    "input_tokens": wt.get("gen_ai.usage.input_tokens"),
                    "output_tokens": wt.get("gen_ai.usage.output_tokens"),
                    "outcome": wt.get("hermes.kanban.outcome"),
                }
            )
        out[tid] = rows
    return out


# --- main -------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--source-profile", default="minimal", help="profile that dispatches and synthesizes"
    )
    ap.add_argument("--worker-model", default=DEFAULT_MODELS["fleet-worker"])
    ap.add_argument("--reviewer-model", default=DEFAULT_MODELS["fleet-reviewer"])
    ap.add_argument("--jaeger-url", default="http://localhost:16686")
    ap.add_argument("--tick-seconds", type=float, default=8.0)
    ap.add_argument("--max-wait", type=float, default=900.0)
    ap.add_argument(
        "--keep-profiles", action="store_true", help="do not delete the profiles created here"
    )
    args = ap.parse_args()

    token = "F" + dt.datetime.now().strftime("%y%m%d-%H%M%S")
    out = HERE / "out" / token
    board = out / "board"
    (board / "ws").mkdir(parents=True, exist_ok=True)
    db = board / "kanban.db"
    env = dict(os.environ)
    env["HERMES_KANBAN_DB"] = str(db)
    env["HERMES_KANBAN_WORKSPACES_ROOT"] = str(board / "ws")
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_RUN_ID"):
        env.pop(k, None)
    log(f"token {token}; board {db}")

    created: List[str] = []
    try:
        for name, model in (
            ("fleet-worker", args.worker_model),
            ("fleet-reviewer", args.reviewer_model),
        ):
            if make_profile(name, model, args.source_profile, env):
                created.append(name)

        hermes(["-p", args.source_profile, "kanban", "init"], env)
        tail = "Use no tools other than the kanban ones. Finish with kanban_complete and a one-sentence summary."
        proc = hermes(
            [
                "-p",
                args.source_profile,
                "kanban",
                "swarm",
                f"Fleet demo {token}: two one-sentence facts about prime numbers, checked and combined",
                "--worker",
                f"fleet-worker:Fleet {token} A. State one fact about prime numbers. {tail}",
                "--worker",
                f"fleet-worker:Fleet {token} B. State a different fact about prime numbers. {tail}",
                "--verifier",
                "fleet-reviewer",
                "--synthesizer",
                args.source_profile,
                "--json",
            ],
            env,
        )
        if proc.returncode != 0:
            sys.exit(f"kanban swarm failed:\n{proc.stderr[-2000:]}")
        log("swarm created: " + " ".join(f"{t['id']}→{t['assignee']}" for t in tasks(db)))

        deadline = time.time() + args.max_wait
        passes = 0
        while time.time() < deadline:
            passes += 1
            hermes(["-p", args.source_profile, "kanban", "dispatch", "--json"], env, timeout=120)
            rows = tasks(db)
            log(f"pass {passes}: " + ", ".join(f"{r['assignee']}:{r['status']}" for r in rows))
            if rows and all(r["status"] in TERMINAL for r in rows):
                break
            time.sleep(args.tick_seconds)
        else:
            log("gave up waiting; the report covers what landed")
        time.sleep(8)  # let the last exports land

        rows = tasks(db)
        worked = [r for r in rows if r["assignee"]]
        runs = runs_from_jaeger(args.jaeger_url, [r["id"] for r in worked])
    finally:
        if not args.keep_profiles:
            for name in created:
                hermes(["profile", "delete", name, "-y"], env)
                log(f"deleted profile {name}")

    checks = []
    L = [
        f"# Fleet demo {token}",
        "",
        f"Board `{db}`; Jaeger {args.jaeger_url}.",
        "",
        "| task | assignee | status | trace | profile | model | trigger | tokens in/out | outcome |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    per_profile: Dict[str, List[int]] = {}
    for r in worked:
        tr = runs.get(r["id"]) or []
        for x in tr or [{}]:
            L.append(
                f"| {r['id']} | {r['assignee']} | {r['status']} | {str(x.get('trace', '-'))[:8]} | "
                f"{x.get('profile') or '-'} | {x.get('model') or '-'} | {x.get('trigger') or '-'} | "
                f"{x.get('input_tokens', '-')}/{x.get('output_tokens', '-')} | {x.get('outcome') or '-'} |"
            )
            if x.get("profile") and x.get("input_tokens") is not None:
                acc = per_profile.setdefault(x["profile"], [0, 0])
                acc[0] += int(x["input_tokens"])
                acc[1] += int(x.get("output_tokens") or 0)
        if not tr:
            # Never claimed: the swarm's anchor card, or a step whose
            # dependency was blocked. Nothing ran, so nothing to check.
            L[-1] = L[-1].replace("| - | - | - | - |", "| - | - | - | not run |", 1)
            continue
        if not any(x["has_worker"] for x in tr):
            # The worker process exited before its first turn (e.g. a skill
            # the card asks for is not installed in that profile): claim and
            # spawn are still one trace, there is just no turn to join it.
            note = crash_note(db, r["id"])
            checks.append(
                (
                    f"{r['assignee']} {r['id']}: claim and spawn share a trace (worker never reached a turn)",
                    all(x["spans"] >= 2 for x in tr),
                    note,
                )
            )
            continue
        checks.append(
            (
                f"{r['assignee']} {r['id']}: card ran to done or blocked",
                r["status"] in ("done", "blocked"),
                r["status"],
            )
        )
        joined = [x for x in tr if x["has_worker"] and x["worker_parented"]]
        checks.append(
            (
                f"{r['assignee']} {r['id']}: worker turn in the claim's trace (#388)",
                bool(joined),
                "",
            )
        )
        checks.append(
            (
                f"{r['assignee']} {r['id']}: worker root says its profile and model (#391)",
                any(x["profile"] == r["assignee"] and x["model"] for x in joined),
                ", ".join(f"{x['profile']}/{x['model']}" for x in joined),
            )
        )
    models = {x["model"] for tr in runs.values() for x in tr if x.get("model")}
    checks.append(
        ("the fleet ran on more than one model", len(models) > 1, ", ".join(sorted(models)))
    )
    L += [
        "",
        "## Tokens per profile (from the worker roots in Jaeger)",
        "",
        "| profile | input | output |",
        "|---|---|---|",
    ]
    for p, (i, o) in sorted(per_profile.items()):
        L.append(f"| {p} | {i} | {o} |")
    L += ["", "## Checks", "", "| check | result | detail |", "|---|---|---|"]
    for cid, ok, detail in checks:
        L.append(f"| {cid} | {'PASS' if ok else 'FAIL'} | {detail} |")
    text = "\n".join(L) + "\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    failed = [c for c in checks if not c[1]]
    log(f"report: {out / 'report.md'}  ({len(checks) - len(failed)} PASS, {len(failed)} FAIL)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
