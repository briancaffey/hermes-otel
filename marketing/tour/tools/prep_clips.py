"""Cut the terminal recordings into the clips the tour plays (S08, S09).

    python3 marketing/tour/tools/prep_clips.py [--src marketing/terminal/renders]

Reads the 1920x1080 MP4s that marketing/terminal/tools/take.sh renders (not in git) and writes
assets/clips/<name>.mp4 (not in git): silent, 30 fps, sped up so the typing and the model's
answer fit the narration. Speeds only change pacing; every frame shown is from the recording.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]

# name -> (speed, seconds trimmed off the start, seconds trimmed off the end), on the source clip
CLIPS = {
    "span-tree": (2.0, 0.0, 0.0),  # S08: the question, the answer, then the span tree
    "cli": (1.75, 0.0, 0.0),       # S09 part 1: otel traces, then otel trace last
    "mcp": (1.6, 0.0, 0.0),        # S09 part 2: the otel MCP tools against Jaeger
}


def duration(path: Path) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(REPO / "marketing" / "terminal" / "renders"))
    a = ap.parse_args()
    src, out = Path(a.src), ROOT / "assets" / "clips"
    out.mkdir(parents=True, exist_ok=True)
    missing = [n for n in CLIPS if not (src / f"{n}.mp4").exists()]
    if missing:
        print(f"missing renders in {src}: {', '.join(missing)} (record them with marketing/terminal/tools/take.sh)")
        return 1
    for name, (speed, head, tail) in CLIPS.items():
        f = src / f"{name}.mp4"
        length = duration(f) - head - tail
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{head:.2f}", "-t", f"{length:.2f}", "-i", str(f),
                        "-an", "-filter:v", f"setpts=PTS/{speed},fps=30", "-c:v", "libx264", "-crf", "16",
                        "-preset", "slow", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out / f"{name}.mp4")],
                       check=True)
        print(f"{name}: {length:.1f}s at {speed}x -> {duration(out / f'{name}.mp4'):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
