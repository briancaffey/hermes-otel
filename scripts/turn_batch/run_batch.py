#!/usr/bin/env python3
"""Run the prompt library through Hermes, one turn after another.

    python scripts/turn_batch/run_batch.py                 # every prompt, profile from prompts.yaml
    python scripts/turn_batch/run_batch.py --only shell-echo,no-tools --profile minimal

Each prompt becomes one ``hermes -p <profile> chat -Q --yolo -q "<prompt>"`` run
(not ``-z``: a one-shot run swallows Hermes's stderr, which the batch keeps).
The prompts run in series so their telemetry does not interleave. Every prompt
gets a marker, ``<token>-<id>``, substituted into its text and appended as
``(batch marker: ...)`` so check_batch.py can find each turn by text in any
backend. The manifest (``runs.json``) records what ran, when, and how it ended;
stdout and stderr of every turn are kept next to it.

No human is needed: ``--yolo`` answers approval prompts, stdin is /dev/null, a
turn that exceeds ``--timeout`` is killed and recorded as such, and the batch
carries on to the next prompt.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent


def load_prompts(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        sys.exit("pyyaml is required: uv run --extra dev python scripts/turn_batch/run_batch.py")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or not isinstance(data.get("prompts"), list):
        sys.exit(f"{path}: expected a mapping with a `prompts:` list")
    return data


def default_token() -> str:
    return "B" + dt.datetime.now().strftime("%y%m%d-%H%M%S")


def render(text: str, token: str, marker: str, workdir: Path) -> str:
    return (
        text.replace("{marker}", marker)
        .replace("{token}", token)
        .replace("{workdir}", str(workdir))
    )


def _profile_config(profile: str) -> Path:
    base = (
        Path(os.environ.get("HERMES_HOME", "")).expanduser()
        if os.environ.get("HERMES_HOME")
        else Path.home() / ".hermes"
    )
    home = base if profile in ("", "default") else base / "profiles" / profile
    return home / "config.yaml"


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def overlay_config(profile: str, prompt: Dict[str, Any]) -> Optional[str]:
    """Apply a prompt's ``config:`` overlay and ``enable_toolsets:`` to the
    profile's config.yaml for one turn; returns the original text to restore
    (None when the prompt asks for nothing). The overlay is deep-merged; an
    enabled toolset is added to ``toolsets`` and removed from
    ``agent.disabled_toolsets``."""
    overlay = prompt.get("config") or {}
    enable = list(prompt.get("enable_toolsets") or [])
    if not overlay and not enable:
        return None
    import yaml  # type: ignore

    path = _profile_config(profile)
    original = path.read_text(encoding="utf-8")
    cfg = yaml.safe_load(original) or {}
    cfg = _merge(cfg, overlay)
    if enable:
        toolsets = list(cfg.get("toolsets") or [])
        cfg["toolsets"] = toolsets + [t for t in enable if t not in toolsets]
        agent = cfg.setdefault("agent", {})
        agent["disabled_toolsets"] = [
            t for t in (agent.get("disabled_toolsets") or []) if t not in enable
        ]
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return original


def restore_config(profile: str, original: Optional[str]) -> None:
    """Put the profile's config.yaml back exactly as it was (a turn's agent
    can edit its own config; the restore covers that too)."""
    if original is not None:
        _profile_config(profile).write_text(original, encoding="utf-8")


def run_one(
    profile: str,
    prompt: str,
    timeout: float,
    out_dir: Path,
    prompt_id: str,
    extra_args: List[str],
) -> Dict[str, Any]:
    cmd = ["hermes", "-p", profile, "chat", "-Q", "--yolo", "-q", prompt, *extra_args]
    started = time.time()
    stdout_path = out_dir / f"{prompt_id}.stdout.txt"
    stderr_path = out_dir / f"{prompt_id}.stderr.txt"
    result: Dict[str, Any] = {
        "id": prompt_id,
        "prompt": prompt,
        "command": cmd,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "started_unix": started,
    }
    with stdout_path.open("wb") as so, stderr_path.open("wb") as se:
        try:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=so,
                stderr=se,
                timeout=timeout,
                env={**os.environ, "HERMES_BATCH_MARKER": prompt_id},
                check=False,
            )
            result["exit_code"] = proc.returncode
            result["timed_out"] = False
        except subprocess.TimeoutExpired:
            result["exit_code"] = None
            result["timed_out"] = True
    result["ended_unix"] = time.time()
    result["duration_s"] = round(result["ended_unix"] - started, 1)
    try:
        tail = stdout_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        result["stdout_tail"] = tail[-3:]
    except OSError:
        result["stdout_tail"] = []
    return result


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--prompts", type=Path, default=HERE / "prompts.yaml")
    ap.add_argument(
        "--profile", default=None, help="Hermes profile (default: prompts.yaml `profile`)"
    )
    ap.add_argument("--only", default="", help="comma-separated prompt ids to run")
    ap.add_argument("--token", default=None, help="run token (default: B<yymmdd-HHMMSS>)")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output dir (default: $HERMES_HOME/turn_batches/<token>, else ~/.hermes/turn_batches/<token>)",
    )
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per turn")
    ap.add_argument("--sleep", type=float, default=3.0, help="seconds between turns")
    ap.add_argument("--dry-run", action="store_true", help="print the rendered prompts and exit")
    ap.add_argument(
        "hermes_args",
        nargs="*",
        help="extra arguments after `--`, passed to hermes chat (e.g. -m MODEL)",
    )
    args = ap.parse_args(argv)

    data = load_prompts(args.prompts)
    profile = args.profile or data.get("profile") or "default"
    token = args.token or default_token()
    # Same rule as scripts/dashboard_verify: a scratch HERMES_HOME keeps its
    # batches, so the verifier finds the manifest where the turns ran (#364).
    home = (
        Path(os.environ["HERMES_HOME"]).expanduser()
        if os.environ.get("HERMES_HOME")
        else Path.home() / ".hermes"
    )
    out_dir = args.out or (home / "turn_batches" / token)
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = out_dir / "workdir"
    workdir.mkdir(exist_ok=True)

    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    prompts = [p for p in data["prompts"] if not wanted or p.get("id") in wanted]
    missing = wanted - {p.get("id") for p in prompts}
    if missing:
        sys.exit(f"unknown prompt id(s): {', '.join(sorted(missing))}")
    if not prompts:
        sys.exit("nothing to run")
    if shutil.which("hermes") is None and not args.dry_run:
        sys.exit("`hermes` is not on PATH")

    manifest: Dict[str, Any] = {
        "token": token,
        "profile": profile,
        "prompts_file": str(args.prompts),
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "started_unix": time.time(),
        "workdir": str(workdir),
        "runs": [],
    }
    manifest_path = out_dir / "runs.json"

    print(f"batch {token} · profile {profile} · {len(prompts)} prompt(s) · {out_dir}")
    for i, p in enumerate(prompts, 1):
        pid = str(p["id"])
        marker = f"{token}-{pid}"
        text = render(str(p["prompt"]), token, marker, workdir)
        text = f"{text}\n(batch marker: {marker})"
        entry = {"id": pid, "marker": marker, "why": p.get("why"), "expect": p.get("expect") or {}}
        if args.dry_run:
            print(f"\n[{i}/{len(prompts)}] {pid}\n{text}")
            continue
        print(f"[{i}/{len(prompts)}] {pid} … ", end="", flush=True)
        original = overlay_config(profile, p)
        try:
            extra = [str(a) for a in (p.get("hermes_args") or [])] + list(args.hermes_args)
            run = run_one(profile, text, args.timeout, out_dir, pid, extra)
        finally:
            restore_config(profile, original)
        entry.update(run)
        manifest["runs"].append(entry)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        state = "timeout" if run["timed_out"] else f"exit {run['exit_code']}"
        print(f"{state} in {run['duration_s']}s")
        if i < len(prompts) and args.sleep:
            time.sleep(args.sleep)

    if args.dry_run:
        return 0
    manifest["ended_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest["ended_unix"] = time.time()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    failed = [r["id"] for r in manifest["runs"] if r["timed_out"] or r["exit_code"] != 0]
    print(
        f"\ndone: {len(manifest['runs'])} run(s), {len(failed)} failed{': ' + ', '.join(failed) if failed else ''}"
    )
    print(f"manifest: {manifest_path}")
    print(f"next: python scripts/turn_batch/check_batch.py --manifest {manifest_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
