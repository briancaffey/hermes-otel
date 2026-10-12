#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""Record one terminal scene with asciinema, with no human at the keyboard (#439).

A detached tmux session of a fixed size runs `asciinema rec` on a clean demo shell.
The script then types each step into the pane one character at a time
(`tmux send-keys -l`) and waits for Hermes to finish a turn before typing the next
message. Turn end = a new `agent` root span in the profile's hermes-otel live store
(the plugin writes it when the turn ends) and then a quiet screen.

    tools/record.py scenes/span-tree.yaml            # -> casts/span-tree.raw.cast + .timeline.json
    tools/record.py scenes/span-tree.yaml --keep     # leave the tmux session open afterwards

`tools/edit_cast.py` then shortens the model waits and writes casts/<name>.cast.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # marketing/terminal
REPO = ROOT.parent.parent

# Lines whose content changes while nothing else does (status bar clock, spinner);
# they are ignored when deciding whether the screen is quiet.
_VOLATILE_MARKERS = ("☤", "⏲")


def tmux(*args: str, check: bool = True) -> str:
    out = subprocess.run(["tmux", *args], capture_output=True, text=True, check=False)
    if check and out.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


class Pane:
    def __init__(self, session: str):
        self.target = session

    def screen(self) -> str:
        return tmux("capture-pane", "-p", "-t", self.target)

    def stable_digest(self) -> str:
        lines = [ln for ln in self.screen().splitlines() if not any(m in ln for m in _VOLATILE_MARKERS)]
        return hashlib.sha1("\n".join(lines).encode()).hexdigest()

    def send_literal(self, text: str) -> None:
        tmux("send-keys", "-t", self.target, "-l", text)

    def send_key(self, key: str) -> None:
        tmux("send-keys", "-t", self.target, key)


class Typist:
    """Human-looking typing: steady inside words, a beat after punctuation, rare hesitations."""

    def __init__(self, cps: float, seed: int):
        self.base = 1.0 / cps
        self.rng = random.Random(seed)

    def delay(self, ch: str, prev: str) -> float:
        d = self.base * self.rng.uniform(0.6, 1.45)
        if ch == " ":
            d *= 1.25
        if prev in ",.?!:":
            d += self.rng.uniform(0.12, 0.28)
        if self.rng.random() < 0.025:
            d += self.rng.uniform(0.25, 0.5)  # a short think
        return d

    def type(self, pane: Pane, text: str, timeline: list, t0: float) -> None:
        timeline.append({"t": time.monotonic() - t0, "event": "type_start", "text": text})
        prev = ""
        for ch in text:
            pane.send_literal(ch)
            time.sleep(self.delay(ch, prev))
            prev = ch
        timeline.append({"t": time.monotonic() - t0, "event": "type_end"})


def agent_roots(db: Path) -> int:
    if not db.exists():
        return 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            return con.execute("select count(*) from events where kind='span' and name='agent'").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def wait_quiet(pane: Pane, quiet_s: float, timeout_s: float, contains: str | None = None) -> None:
    deadline = time.monotonic() + timeout_s
    last, since = None, time.monotonic()
    while time.monotonic() < deadline:
        d = pane.stable_digest()
        if d != last:
            last, since = d, time.monotonic()
        elif time.monotonic() - since >= quiet_s and (contains is None or contains in pane.screen()):
            return
        time.sleep(0.2)
    raise TimeoutError(f"screen did not settle within {timeout_s}s" + (f" (waiting for {contains!r})" if contains else ""))


def wait_turn(pane: Pane, db: Path, before: int, quiet_s: float, timeout_s: float, idle_prompt: str) -> None:
    deadline = time.monotonic() + timeout_s
    while agent_roots(db) <= before:
        if time.monotonic() > deadline:
            raise TimeoutError(f"no new agent span in {db} within {timeout_s}s")
        time.sleep(0.5)
    # The prompt line reads `<profile> ❯` again only once Hermes is idle (it shows a busy hint before).
    wait_quiet(pane, quiet_s, max(10.0, deadline - time.monotonic()), contains=idle_prompt)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene", type=Path)
    ap.add_argument("--keep", action="store_true", help="leave the tmux session running")
    ap.add_argument("--out", type=Path, default=ROOT / "casts")
    args = ap.parse_args()

    scene = yaml.safe_load(args.scene.read_text())
    name = scene.get("name") or args.scene.stem
    cols, rows = int(scene.get("cols", 120)), int(scene.get("rows", 30))
    profile = scene.get("profile", "promo")
    hermes_home = Path(os.path.expanduser(f"~/.hermes/profiles/{profile}"))
    db = hermes_home / "hermes_otel_live.db"
    workdir = Path(os.path.expanduser(scene.get("workdir", str(REPO))))
    typist = Typist(float(scene.get("cps", 16)), int(scene.get("seed", 7)))

    args.out.mkdir(parents=True, exist_ok=True)
    raw = args.out / f"{name}.raw.cast"
    session = f"rec-{name}"
    tmux("kill-session", "-t", session, check=False)

    env = {
        "DEMO_PROFILE": profile,
        "DEMO_WORKDIR": str(workdir),
        "DEMO_PS1_DIR": scene.get("ps1_dir", "~/" + workdir.name),
        "PYTHON": str(Path.home() / "git/hermes-agent/venv/bin/python"),
        "OTEL_PY": str(REPO / "hermes_otel/skills/observability/scripts/otel.py"),
    }
    rec = (
        f"asciinema rec --overwrite --quiet --capture-input --title {shlex.quote(scene.get('title', name))} "
        f"--command {shlex.quote(f'bash --noprofile --rcfile {HERE / 'demo_bashrc'} -i')} {shlex.quote(str(raw))}"
    )
    tmux(
        "new-session", "-d", "-s", session, "-x", str(cols), "-y", str(rows),
        *[a for k, v in env.items() for a in ("-e", f"{k}={v}")],
        f"cd {shlex.quote(str(workdir))} && TERM=xterm-256color COLORTERM=truecolor {rec}",
    )
    # Keep tmux itself out of the picture: no status line, true colour passed through.
    tmux("set-option", "-t", session, "status", "off")
    tmux("set-option", "-t", session, "-g", "terminal-features", "*:RGB")

    pane = Pane(session)
    timeline: list[dict] = []
    t0 = time.monotonic()
    timeline.append({"t": 0.0, "event": "start", "start_at": scene.get("start_at", "start")})
    try:
        wait_quiet(pane, 0.8, 20)
        for i, step in enumerate(scene["steps"]):
            kind, value = next(iter(step.items()))
            if kind == "pause":
                time.sleep(float(value))
            elif kind == "type":
                typist.type(pane, str(value), timeline, t0)
            elif kind == "key":
                pane.send_key(str(value))
                timeline.append({"t": time.monotonic() - t0, "event": "key", "key": value})
            elif kind == "say":  # type a message and press Enter, then wait for the turn to end
                before = agent_roots(db)
                typist.type(pane, str(value), timeline, t0)
                time.sleep(float(scene.get("enter_pause", 0.45)))
                pane.send_key("Enter")
                timeline.append({"t": time.monotonic() - t0, "event": "submit"})
                wait_turn(pane, db, before, float(scene.get("quiet_s", 2.0)), float(scene.get("turn_timeout", 240)),
                          f"{profile} ❯")
                timeline.append({"t": time.monotonic() - t0, "event": "turn_end"})
            elif kind == "run":  # a shell command: type, Enter, wait for the screen to settle
                typist.type(pane, str(value), timeline, t0)
                time.sleep(0.35)
                pane.send_key("Enter")
                timeline.append({"t": time.monotonic() - t0, "event": "submit"})
                wait_quiet(pane, float(step.get("quiet_s", scene.get("run_quiet_s", 1.5))), 120)
                timeline.append({"t": time.monotonic() - t0, "event": "turn_end"})
            elif kind == "wait_for":  # text on screen, e.g. the Hermes prompt after launch
                wait_quiet(pane, 1.0, float(scene.get("launch_timeout", 60)), contains=str(value))
                timeline.append({"t": time.monotonic() - t0, "event": "ready"})
            else:
                raise ValueError(f"step {i}: unknown step {kind!r}")
        timeline.append({"t": time.monotonic() - t0, "event": "end"})
    finally:
        (args.out / f"{name}.timeline.json").write_text(json.dumps(timeline, indent=1))
        if not args.keep:
            # Leave the demo shell (and Hermes, if still open) so asciinema closes the file.
            for key in ("C-c", "C-d"):
                tmux("send-keys", "-t", session, key, check=False)
                time.sleep(0.6)
            tmux("send-keys", "-t", session, "-l", "exit", check=False)
            tmux("send-keys", "-t", session, "Enter", check=False)
            for _ in range(20):
                if session not in tmux("ls", check=False):
                    break
                time.sleep(0.5)
            tmux("kill-session", "-t", session, check=False)
    print(f"{raw} ({time.monotonic() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
