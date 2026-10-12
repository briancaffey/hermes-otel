#!/usr/bin/env python3
"""Turn a raw take into the cast the video uses (#439).

    tools/edit_cast.py casts/span-tree.raw.cast        # -> casts/span-tree.cast

Uses the driver's `<name>.timeline.json` (aligned to the cast by the first typed key):

- **Model waits are shortened.** From each Enter to the moment Hermes opens its answer box,
  time runs faster so the wait lasts at most `--wait-cap` seconds. Tool-progress lines still
  appear in order, just sooner. The answer itself streams at real speed (`--answer-speed`).
- **Typing keeps its real timing**: it is the "user" on camera.
- **Other pauses** longer than `--idle-cap` are capped (the shell, the launch).
- **Optionally the launch is skipped** (`start_at: ready` in the scene, or `--start-at ready`):
  everything before Hermes's first idle prompt is drawn at t=0.
- **The tail is cut** at the scene's `end` marker, so Hermes's exit summary never shows.
- Keyboard-input events are dropped; only what the screen showed stays.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# The top border of Hermes's answer box, "╭─ ☤ Hermes ──…" (the CLI draws it when the reply starts).
ANSWER_MARKERS = (" Hermes ", "Hermes\x1b")


def load_cast(path: Path) -> tuple[dict, list[list]]:
    lines = path.read_text().splitlines()
    header = json.loads(lines[0])
    events, t = [], 0.0
    for ln in lines[1:]:
        if not ln.strip() or ln.startswith("#"):
            continue
        dt, code, data = json.loads(ln)
        t += dt
        events.append([t, code, data])
    return header, events


def write_cast(path: Path, header: dict, events: list[list]) -> None:
    out, prev = [json.dumps(header, ensure_ascii=False)], 0.0
    for t, code, data in events:
        out.append(json.dumps([round(t - prev, 4), code, data], ensure_ascii=False))
        prev = t
    path.write_text("\n".join(out) + "\n")


def timeline_start_mode(timeline: list[dict]) -> str:
    return next((e.get("start_at", "start") for e in timeline if e["event"] == "start"), "start")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw", type=Path)
    ap.add_argument("--wait-cap", type=float, default=4.0, help="longest on-screen model wait, seconds")
    ap.add_argument("--answer-speed", type=float, default=1.0, help="playback speed of a streaming answer")
    ap.add_argument("--idle-cap", type=float, default=1.6, help="longest pause outside typing and answers")
    ap.add_argument("--tail", type=float, default=0.5, help="seconds the last frame is held")
    ap.add_argument("--start-at", choices=("start", "ready"), default=None,
                    help="'ready' opens on Hermes's first idle screen (the launch is collapsed to t=0)")
    ap.add_argument("-o", "--out", type=Path)
    args = ap.parse_args()

    header, events = load_cast(args.raw)
    name = args.raw.name.removesuffix(".raw.cast")
    timeline = json.loads((args.raw.parent / f"{name}.timeline.json").read_text())

    # Align driver time to cast time with the first keystroke.
    first_key = next((t for t, c, _ in events if c == "i"), None)
    first_type = next((e["t"] for e in timeline if e["event"] == "type_start"), None)
    offset = (first_key - first_type) if first_key is not None and first_type is not None else 0.0
    marks = [{**e, "t": e["t"] + offset} for e in timeline]
    # Cut at the end marker, and before the driver's Ctrl-C at the latest (the cast records that
    # key exactly, while the marker is only aligned to ~0.1 s).
    end_t = next((m["t"] for m in marks if m["event"] == "end"), events[-1][0])
    exit_key = next((t for t, c, _ in events if c == "i" and t >= end_t - 0.5), None)
    if exit_key is not None:
        end_t = min(end_t, exit_key - 0.01)

    # Segments of real time with a playback speed: (start, stop, speed).
    segments: list[tuple[float, float, float]] = []
    submits = [m["t"] for m in marks if m["event"] == "submit"]
    ends = [m["t"] for m in marks if m["event"] == "turn_end"]
    for s, e in zip(submits, ends):
        answer = next(
            (t for t, c, d in events if c == "o" and s < t < e and any(k in d for k in ANSWER_MARKERS)), e
        )
        wait = answer - s
        speed = max(1.0, wait / args.wait_cap)
        segments.append((s, answer, speed))
        segments.append((answer, e, args.answer_speed))

    def speed_at(t: float) -> float:
        for a, b, sp in segments:
            if a <= t < b:
                return sp
        return 1.0

    start_mode = args.start_at or timeline_start_mode(timeline)
    ready_t = next((m["t"] for m in marks if m["event"] == "ready"), None) if start_mode == "ready" else None

    typing = [(m["t"], n["t"]) for m, n in zip(marks, marks[1:]) if m["event"] == "type_start"]

    def in_typing(t: float) -> bool:
        return any(a - 0.05 <= t <= b + 0.6 for a, b in typing)

    out, new_t, prev = [], 0.0, 0.0
    for t, code, data in events:
        if t > end_t:
            break
        if ready_t is not None and t <= ready_t:
            prev = t
            if code == "o":
                out.append([0.0, code, data])
            continue
        gap = t - prev
        sp = speed_at(prev)
        step = gap / sp
        if sp == 1.0 and not in_typing(t) and step > args.idle_cap:
            step = args.idle_cap
        new_t += step
        prev = t
        if code == "o":
            out.append([new_t, code, data])
    # Hold the final frame for the tail.
    out.append([new_t + args.tail, "m", ""])

    header = {**header, "title": header.get("title", name)}
    header.pop("idle_time_limit", None)
    dest = args.out or args.raw.with_name(f"{name}.cast")
    write_cast(dest, header, out)
    print(f"{dest}: {events[-1][0]:.1f} s raw -> {new_t:.1f} s; waits: "
          + ", ".join(f"{b - a:.1f}s→{(b - a) / sp:.1f}s" for a, b, sp in segments[::2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
