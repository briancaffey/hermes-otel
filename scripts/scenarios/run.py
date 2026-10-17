#!/usr/bin/env python3
"""Run scenarios through a real Hermes and record what ran.

    uv run --extra dev python scripts/scenarios/run.py                     # the quick suite
    uv run --extra dev python scripts/scenarios/run.py --suite full        # every mode
    uv run --extra dev python scripts/scenarios/run.py --only shell-echo,resume-three-turns
    uv run --extra dev python scripts/scenarios/run.py --dry-run --suite all

Every scenario is one or more Hermes turns in the harness profile (built by
``setup_profile.py``; ``--profile`` picks another). The ``mode`` says how the
turns are driven:

=========  ===============================================================
cli        ``hermes -p <profile> chat -Q --yolo -q "<prompt>"``, one turn
resume     the first turn as ``cli``, every later turn ``chat -r <session>``
gateway    a ``hermes gateway run`` of the profile with the API server on a
           private port; ``sessions`` × ``turns`` submitted over
           ``POST /v1/runs`` (``concurrency`` at a time); ``approval:``
           answers the run's approval over ``/v1/runs/{id}/approval``
cron       a one-shot job (``cron create``), fired with ``cron run``, then removed
kanban     one card per profile on a scratch board, worked by
           ``hermes kanban dispatch`` passes until every card is terminal
=========  ===============================================================

Each turn's prompt carries a marker (``<token>-<id>`` or
``<token>-<id>-s<session>t<turn>``) so check.py can find its trace by text in
any backend. A scenario's ``config:`` / ``enable_toolsets:`` / ``otel:``
overlays are applied to the profile for that scenario and restored after it.
The manifest (``runs.json``), every turn's stdout/stderr and the gateway log
land in ``<profile home>/scenario_runs/<token>/`` (``--out`` to override).

Nothing needs a human: ``--yolo`` (cli modes) or the scenario's ``approval``
(gateway) answers approvals, stdin is /dev/null, a turn that exceeds
``--timeout`` is killed and recorded as such, and the run carries on.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from _common import profile_home  # noqa: E402
from common import (  # noqa: E402
    DEFAULT_PROFILE,
    DEFAULT_SCENARIOS,
    Overlay,
    default_token,
    load_scenarios,
    render,
    runs_dir,
    select,
    tag,
)

SESSION_RE = re.compile(r"^session_id:\s*(\S+)\s*$", re.M)
CRON_ID_RE = re.compile(r"Created job:\s*([0-9a-zA-Z_-]+)")
KANBAN_TERMINAL = {"done", "blocked", "archived"}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def log(msg: str) -> None:
    print(msg, flush=True)


# ── cli and resume ───────────────────────────────────────────────────────


def run_cli_turn(
    profile: str,
    prompt: str,
    *,
    out_dir: Path,
    label: str,
    timeout: float,
    extra_args: List[str],
    resume: Optional[str] = None,
    yolo: bool = True,
) -> Dict[str, Any]:
    cmd = ["hermes", "-p", profile, "chat", "-Q"]
    if yolo:
        cmd.append("--yolo")
    if resume:
        cmd += ["-r", resume]
    cmd += ["-q", prompt, *extra_args]
    stdout_path = out_dir / f"{label}.stdout.txt"
    stderr_path = out_dir / f"{label}.stderr.txt"
    started = time.time()
    result: Dict[str, Any] = {"command": cmd, "started_at": now_iso(), "started_unix": started}
    with stdout_path.open("wb") as so, stderr_path.open("wb") as se:
        try:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=so,
                stderr=se,
                timeout=timeout,
                cwd=str(out_dir / "workdir"),
                check=False,
            )
            result.update(exit_code=proc.returncode, timed_out=False)
        except subprocess.TimeoutExpired:
            result.update(exit_code=None, timed_out=True)
    result["ended_unix"] = time.time()
    result["duration_s"] = round(result["ended_unix"] - started, 1)
    text = stdout_path.read_text(encoding="utf-8", errors="replace")
    # `chat -Q` prints "session_id: <id>" on stderr (the answer on stdout).
    found = SESSION_RE.findall(
        text + "\n" + stderr_path.read_text(encoding="utf-8", errors="replace")
    )
    result["session_id"] = found[-1] if found else resume
    lines = [ln for ln in text.strip().splitlines() if not ln.startswith("session_id:")]
    result["stdout_tail"] = lines[-3:]
    return result


def turns_of(scenario: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``turns:`` as ``[{prompt, expect?}]``; a lone ``prompt:`` is one turn."""
    raw = scenario.get("turns")
    if raw is None:
        raw = [scenario.get("prompt", "")]
    out = []
    for t in raw:
        out.append(dict(t) if isinstance(t, dict) else {"prompt": str(t)})
    return out


def marker_for(token: str, sid: str, session: int, turn: int, multi: bool) -> str:
    return f"{token}-{sid}" if not multi else f"{token}-{sid}-s{session + 1}t{turn + 1}"


class Runner:
    def __init__(self, args: argparse.Namespace, token: str, out_dir: Path) -> None:
        self.args = args
        self.profile = args.profile
        self.token = token
        self.out_dir = out_dir
        self.workdir = out_dir / "workdir"

    def prompt(self, text: str, marker: str) -> str:
        return tag(render(text, token=self.token, marker=marker, workdir=self.workdir), marker)

    def extra_args(self, s: Dict[str, Any]) -> List[str]:
        return [str(a) for a in (s.get("hermes_args") or [])] + list(self.args.hermes_args)

    # cli: one turn; resume: every turn after the first resumes its session.
    def run_cli(self, s: Dict[str, Any]) -> List[Dict[str, Any]]:
        turns = turns_of(s)
        multi = len(turns) > 1
        out: List[Dict[str, Any]] = []
        session: Optional[str] = None
        for j, t in enumerate(turns):
            marker = marker_for(self.token, s["id"], 0, j, multi)
            entry: Dict[str, Any] = {"marker": marker, "session": 0, "turn": j + 1}
            if t.get("expect") is not None:
                entry["expect"] = t["expect"]
            if s["mode"] == "resume" and j > 0 and not session:
                entry.update(exit_code=None, timed_out=False, skipped="no session id to resume")
                out.append(entry)
                continue
            res = run_cli_turn(
                self.profile,
                self.prompt(t["prompt"], marker),
                out_dir=self.out_dir,
                label=marker.removeprefix(f"{self.token}-"),
                timeout=self.args.timeout,
                extra_args=self.extra_args(s),
                resume=session if (s["mode"] == "resume" and j > 0) else None,
            )
            entry.update(res)
            session = session or res.get("session_id")
            out.append(entry)
        return out

    # gateway: own gateway process per scenario, so overlays and shutdown are scoped to it.
    def run_gateway(self, s: Dict[str, Any]) -> List[Dict[str, Any]]:
        gw = Gateway(
            self.profile,
            self.out_dir / f"{s['id']}.gateway.log",
            self.args.gateway_port,
            self.workdir,
        )
        if not gw.start(timeout=self.args.gateway_start_timeout):
            return [
                {
                    "marker": f"{self.token}-{s['id']}",
                    "session": 0,
                    "turn": 1,
                    "exit_code": None,
                    "timed_out": False,
                    "skipped": f"gateway did not answer on :{gw.port} (see {gw.log_path.name})",
                }
            ]
        try:
            return self._gateway_turns(s, gw)
        finally:
            gw.stop()

    def _gateway_turns(self, s: Dict[str, Any], gw: "Gateway") -> List[Dict[str, Any]]:
        turns = turns_of(s)
        sessions = int(s.get("sessions") or 1)
        multi = sessions > 1 or len(turns) > 1
        approval = s.get("approval")

        def one_session(i: int) -> List[Dict[str, Any]]:
            sid = f"{self.token}-{s['id']}-s{i + 1}"
            rows = []
            for j, t in enumerate(turns):
                marker = marker_for(self.token, s["id"], i, j, multi)
                entry: Dict[str, Any] = {
                    "marker": marker,
                    "session": i,
                    "turn": j + 1,
                    "session_id": sid,
                }
                if t.get("expect") is not None:
                    entry["expect"] = t["expect"]
                entry.update(
                    gw.run(
                        self.prompt(t["prompt"], marker),
                        sid,
                        approval=approval,
                        timeout=self.args.timeout,
                    )
                )
                rows.append(entry)
            return rows

        workers = max(1, int(s.get("concurrency") or 1))
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one_session, range(sessions)))
        return [row for rows in results for row in rows]

    # cron: create a job whose prompt carries the marker, fire it once, remove it.
    def run_cron(self, s: Dict[str, Any]) -> List[Dict[str, Any]]:
        marker = f"{self.token}-{s['id']}"
        entry: Dict[str, Any] = {"marker": marker, "session": 0, "turn": 1}
        cron = s.get("cron") or {}
        create = [
            "hermes",
            "-p",
            self.profile,
            "cron",
            "create",
            "1h",
            self.prompt(s["prompt"], marker),
            "--name",
            marker,
            "--deliver",
            "local",
            "--repeat",
            "1",
        ]
        if cron.get("model"):
            create += ["--model", str(cron["model"])]
        if cron.get("provider"):
            create += ["--provider", str(cron["provider"])]
        started = time.time()
        entry.update(started_at=now_iso(), started_unix=started)
        proc = subprocess.run(
            create, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120
        )
        m = CRON_ID_RE.search(proc.stdout)
        if not m:
            entry.update(
                exit_code=proc.returncode,
                timed_out=False,
                skipped=f"cron create printed no job id: {(proc.stderr or proc.stdout)[-300:]}",
            )
            return [entry]
        job = m.group(1)
        entry["cron_job_id"] = job
        log_path = self.out_dir / f"{s['id']}.cron.txt"
        try:
            fire = subprocess.run(
                ["hermes", "-p", self.profile, "cron", "run", job],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.args.timeout,
            )
            log_path.write_text(fire.stdout + "\n--- stderr ---\n" + fire.stderr, encoding="utf-8")
            entry.update(exit_code=fire.returncode, timed_out=False)
            entry["stdout_tail"] = fire.stdout.strip().splitlines()[-2:]
        except subprocess.TimeoutExpired:
            entry.update(exit_code=None, timed_out=True)
        finally:
            subprocess.run(
                ["hermes", "-p", self.profile, "cron", "rm", job],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=60,
            )
        entry["ended_unix"] = time.time()
        entry["duration_s"] = round(entry["ended_unix"] - started, 1)
        return [entry]

    # kanban: one card per profile on a scratch board; dispatch until terminal.
    def run_kanban(self, s: Dict[str, Any]) -> List[Dict[str, Any]]:
        kb = s.get("kanban") or {}
        profiles = list(kb.get("profiles") or [self.profile])
        missing = [p for p in profiles if not (profile_home(p) / "config.yaml").exists()]
        if missing:
            return [
                {
                    "marker": marker_for(self.token, s["id"], i, 0, True),
                    "session": i,
                    "turn": 1,
                    "profile": p,
                    "exit_code": None,
                    "timed_out": False,
                    "skipped": f"profile {', '.join(missing)} missing; run setup_profile.py --second-profile",
                }
                for i, p in enumerate(profiles)
            ]
        board = self.out_dir / f"{s['id']}.board"
        (board / "ws").mkdir(parents=True, exist_ok=True)
        db = board / "kanban.db"
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_RUN_ID")
        }
        env.update(HERMES_KANBAN_DB=str(db), HERMES_KANBAN_WORKSPACES_ROOT=str(board / "ws"))

        def hermes(args: List[str], timeout: float = 120) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["hermes", *args],
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        hermes(["-p", self.profile, "kanban", "init"])
        started = time.time()
        cards: List[Dict[str, Any]] = []
        for i, prof in enumerate(profiles):
            marker = marker_for(self.token, s["id"], i, 0, True)
            body = self.prompt(s["prompt"], marker)
            proc = hermes(
                [
                    "-p",
                    self.profile,
                    "kanban",
                    "create",
                    "--assignee",
                    prof,
                    "--max-retries",
                    "1",
                    "--max-runtime",
                    f"{int(self.args.timeout)}s",
                    "--json",
                    "--body",
                    body,
                    f"scenario {marker}",
                ]
            )
            card: Dict[str, Any] = {}
            try:
                card = json.loads(proc.stdout[proc.stdout.find("{") :])
            except ValueError:
                pass
            cards.append(
                {
                    "marker": marker,
                    "session": i,
                    "turn": 1,
                    "profile": prof,
                    "kanban_task": card.get("id"),
                    "started_at": now_iso(),
                    "started_unix": started,
                }
            )
        ids = {c["kanban_task"] for c in cards if c.get("kanban_task")}
        deadline = time.time() + self.args.timeout * max(1, len(cards))
        status: Dict[str, str] = {}
        passes = 0
        while ids and time.time() < deadline:
            passes += 1
            hermes(["-p", self.profile, "kanban", "dispatch", "--json"], timeout=self.args.timeout)
            status = kanban_status(db, ids)
            if status and all(v in KANBAN_TERMINAL for v in status.values()):
                break
            time.sleep(float(kb.get("tick_seconds", 6)))
        ended = time.time()
        for c in cards:
            st = status.get(c.get("kanban_task") or "", "missing")
            c.update(
                kanban_status=st,
                exit_code=0 if st == "done" else 1,
                timed_out=st not in KANBAN_TERMINAL,
                ended_unix=ended,
                duration_s=round(ended - started, 1),
                dispatch_passes=passes,
            )
        return cards


def kanban_status(db: Path, ids: set) -> Dict[str, str]:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute("select id, status from tasks").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return {r[0]: r[1] for r in rows if r[0] in ids}


# ── The gateway ──────────────────────────────────────────────────────────


def free_port(preferred: int) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 50)]:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port for the gateway's API server")


class Gateway:
    """``hermes -p <profile> gateway run`` with the API server platform on a
    private loopback port and a throwaway key. The profile is standalone
    (setup_profile.py), so a multiplexed host gateway never serves it."""

    def __init__(self, profile: str, log_path: Path, port: int, cwd: Path) -> None:
        self.profile = profile
        self.log_path = log_path
        self.cwd = cwd
        self.port = free_port(port)
        self.key = secrets.token_hex(24)
        self.proc: Optional[subprocess.Popen] = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float) -> bool:
        env = dict(os.environ)
        env.update(
            API_SERVER_ENABLED="true",
            API_SERVER_KEY=self.key,
            API_SERVER_PORT=str(self.port),
            API_SERVER_HOST="127.0.0.1",
        )
        self._log = self.log_path.open("wb")
        self.proc = subprocess.Popen(
            ["hermes", "-p", self.profile, "gateway", "run"],
            cwd=str(self.cwd),
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                self.request("GET", "/health", timeout=2)
                return True
            except (OSError, urllib.error.URLError):
                time.sleep(1)
        return False

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if getattr(self, "_log", None):
            self._log.close()

    def request(
        self, method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 30
    ) -> Dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")

    def run(
        self, prompt: str, session_id: str, *, approval: Optional[str], timeout: float
    ) -> Dict[str, Any]:
        """Submit one turn and wait for it; answer its approval if asked to.
        ``approval``: ``once`` / ``session`` / ``always`` / ``deny`` answer the
        first approval request, ``timeout`` leaves it unanswered."""
        started = time.time()
        out: Dict[str, Any] = {"started_at": now_iso(), "started_unix": started}
        try:
            r = self.request("POST", "/v1/runs", {"input": prompt, "session_id": session_id})
        except (OSError, urllib.error.URLError, ValueError) as exc:
            out.update(exit_code=None, timed_out=False, skipped=f"POST /v1/runs failed: {exc}")
            return out
        run_id = r.get("run_id")
        out["run_id"] = run_id
        status: Dict[str, Any] = {}
        answered = False
        while time.time() - started < timeout:
            try:
                status = self.request("GET", f"/v1/runs/{run_id}")
            except (OSError, urllib.error.URLError, ValueError):
                time.sleep(1)
                continue
            st = status.get("status")
            if st == "waiting_for_approval" and not answered:
                out["approval_requested"] = (status.get("approval") or {}).get("command")
                if approval and approval != "timeout":
                    try:
                        self.request("POST", f"/v1/runs/{run_id}/approval", {"choice": approval})
                        out["approval_answered"] = approval
                    except (OSError, urllib.error.URLError) as exc:
                        out["approval_error"] = str(exc)
                answered = True
            if st in ("completed", "failed", "cancelled", "stopped", "interrupted"):
                break
            time.sleep(1)
        out["ended_unix"] = time.time()
        out["duration_s"] = round(out["ended_unix"] - started, 1)
        st = status.get("status")
        out["run_status"] = st
        out["timed_out"] = st not in ("completed", "failed", "cancelled", "stopped", "interrupted")
        out["exit_code"] = 0 if st == "completed" else (None if out["timed_out"] else 1)
        output = str(status.get("output") or "")
        out["stdout_tail"] = output.strip().splitlines()[-3:]
        return out


# ── Main ─────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--scenarios", "--prompts", type=Path, default=DEFAULT_SCENARIOS)
    ap.add_argument(
        "--profile",
        default=None,
        help=f"Hermes profile (default: the file's, else {DEFAULT_PROFILE})",
    )
    ap.add_argument("--only", default="", help="comma-separated scenario ids")
    ap.add_argument(
        "--suite", default="quick", help="quick (default), full, extra, all, or a suite in the file"
    )
    ap.add_argument("--token", default=None, help="run token (default: S<yymmdd-HHMMSS>)")
    ap.add_argument("--out", type=Path, default=None, help="output directory")
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per turn")
    ap.add_argument("--sleep", type=float, default=2.0, help="seconds between scenarios")
    ap.add_argument("--gateway-port", type=int, default=8651)
    ap.add_argument("--gateway-start-timeout", type=float, default=90.0)
    ap.add_argument("--dry-run", action="store_true", help="print the rendered turns and exit")
    ap.add_argument("hermes_args", nargs="*", help="extra arguments after `--` for hermes chat")
    args = ap.parse_args(argv)

    data = load_scenarios(args.scenarios)
    args.profile = args.profile or data.get("profile") or DEFAULT_PROFILE
    scenarios = select(data, args.only, args.suite)
    if not scenarios:
        sys.exit("nothing to run")
    if shutil.which("hermes") is None and not args.dry_run:
        sys.exit("`hermes` is not on PATH")
    if not args.dry_run and not (profile_home(args.profile) / "config.yaml").exists():
        sys.exit(
            f"profile {args.profile!r} does not exist; run scripts/scenarios/setup_profile.py first"
        )
    token = args.token or default_token()
    out_dir = args.out or runs_dir(args.profile, token)
    if not args.dry_run:
        (out_dir / "workdir").mkdir(parents=True, exist_ok=True)
    runner = Runner(args, token, out_dir)

    manifest: Dict[str, Any] = {
        "token": token,
        "profile": args.profile,
        "scenarios_file": str(args.scenarios),
        "started_at": now_iso(),
        "started_unix": time.time(),
        "workdir": str(out_dir / "workdir"),
        "runs": [],
    }
    manifest_path = out_dir / "runs.json"
    log(f"scenarios {token} · profile {args.profile} · {len(scenarios)} scenario(s) · {out_dir}")
    for i, s in enumerate(scenarios, 1):
        if args.dry_run:
            print(f"\n[{i}/{len(scenarios)}] {s['id']} ({s['mode']})")
            for j, t in enumerate(turns_of(s)):
                marker = marker_for(token, s["id"], 0, j, len(turns_of(s)) > 1)
                print(f"  turn {j + 1}: {runner.prompt(t['prompt'], marker)}")
            continue
        log(f"[{i}/{len(scenarios)}] {s['id']} ({s['mode']}) …")
        with Overlay(args.profile, s):
            run_mode = {
                "cli": runner.run_cli,
                "resume": runner.run_cli,
                "gateway": runner.run_gateway,
                "cron": runner.run_cron,
                "kanban": runner.run_kanban,
            }[s["mode"]]
            turns = run_mode(s)
        entry = {
            "id": s["id"],
            "mode": s["mode"],
            "why": s.get("why"),
            "expect": s.get("expect") or {},
            "expect_run": s.get("expect_run") or {},
            "known_issue": s.get("known_issue"),
            "turns": turns,
            # single-turn shape kept for scripts that read runs[].marker
            # (dashboard_verify, refactor_check)
            "marker": turns[0]["marker"] if turns else f"{token}-{s['id']}",
            "started_unix": min((t.get("started_unix") or time.time()) for t in turns),
            "ended_unix": max((t.get("ended_unix") or time.time()) for t in turns),
        }
        entry["exit_code"] = 0 if all(t.get("exit_code") == 0 for t in turns) else 1
        # A scenario that drives a failure (expect_exit: nonzero) is fine when it fails.
        if s.get("expect_exit") == "nonzero" and turns and all(t.get("exit_code") for t in turns):
            entry["exit_code"] = 0
        entry["timed_out"] = any(t.get("timed_out") for t in turns)
        entry["duration_s"] = round(entry["ended_unix"] - entry["started_unix"], 1)
        manifest["runs"].append(entry)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for t in turns:
            state = (
                f"skipped ({t['skipped']})"
                if t.get("skipped")
                else "timeout" if t.get("timed_out") else f"exit {t.get('exit_code')}"
            )
            log(f"    {t['marker'].removeprefix(token + '-')}: {state} in {t.get('duration_s')}s")
        if i < len(scenarios) and args.sleep:
            time.sleep(args.sleep)
    if args.dry_run:
        return 0
    manifest["ended_at"] = now_iso()
    manifest["ended_unix"] = time.time()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    failed = [r["id"] for r in manifest["runs"] if r["exit_code"] != 0 or r["timed_out"]]
    log(
        f"\ndone: {len(manifest['runs'])} scenario(s), {len(failed)} with a failed turn"
        f"{': ' + ', '.join(failed) if failed else ''}"
    )
    log(f"manifest: {manifest_path}")
    log(f"next: uv run --extra dev python scripts/scenarios/check.py --manifest {manifest_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
