#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.47"]
# ///
"""Render a cast to a 1080p MP4 for the promo video (#441).

    tools/render.py casts/span-tree.cast                     # player path (default)
    tools/render.py casts/span-tree.cast --engine agg        # agg -> GIF -> MP4 baseline
    tools/render.py casts/span-tree.cast --fps 60 --still 12.5 # also write one PNG at 12.5 s

`player`: asciinema-player 3.17.0 in headless Chromium, stepped frame by frame
(`seek()` on a paused player, then a screenshot), piped into ffmpeg. True colour,
JetBrains Mono, the marketing palette, and a flat window frame on a 1920x1080 canvas.
`agg`: asciinema's own renderer (GIF, 256 colours per frame), scaled and padded by ffmpeg.

Needs `.cache/` (tools/fetch_assets.sh): the player bundle and JetBrains Mono.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / ".cache"

# marketing/DESIGN.md: background #0d1117, surface #161b22, text #c9d1d9, muted #8b949e,
# blue #58a6ff, green #3fb950. The 16 ANSI colours are GitHub dark's.
PALETTE = {
    "canvas": "#0d1117",
    "window": "#161b22",
    "border": "#30363d",
    "fg": "#c9d1d9",
    "bg": "#0d1117",
    "muted": "#8b949e",
    "ansi": [
        "#484f58", "#ff7b72", "#3fb950", "#d29922", "#58a6ff", "#bc8cff", "#39c5cf", "#b1bac4",
        "#6e7681", "#ffa198", "#56d364", "#e3b341", "#79c0ff", "#d2a8ff", "#56d4dd", "#f0f6fc",
    ],
}

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="asciinema-player.css">
<style>
@font-face { font-family: "JetBrains Mono"; src: url("fonts/JetBrainsMono-Regular.ttf"); font-weight: 400; }
@font-face { font-family: "JetBrains Mono"; src: url("fonts/JetBrainsMono-Bold.ttf"); font-weight: 700; }
@font-face { font-family: "JetBrains Mono"; src: url("fonts/JetBrainsMono-Italic.ttf"); font-style: italic; }
html, body { margin: 0; width: %(W)dpx; height: %(H)dpx; background: %(canvas)s; overflow: hidden; }
body { display: flex; align-items: center; justify-content: center; }
.window { background: %(window)s; border: 1px solid %(border)s; border-radius: 8px; overflow: hidden; }
.bar { height: 44px; display: flex; align-items: center; gap: 9px; padding: 0 18px;
       border-bottom: 1px solid %(border)s; font: 500 18px/1 Inter, system-ui, sans-serif; color: %(muted)s; }
.dot { width: 13px; height: 13px; border-radius: 50%%; background: %(border)s; }
.bar .title { margin-left: 14px; letter-spacing: .01em; }
.bar .title b { color: %(fg)s; font-weight: 600; }
.term { padding: %(pad)dpx; background: %(bg)s; }
.ap-player { border-radius: 0 !important; }
.asciinema-player-theme-hermes {
  --term-color-foreground: %(fg)s; --term-color-background: %(bg)s;
  %(ansi_vars)s
}
.ap-control-bar, .ap-overlay, .ap-overlay-start { display: none !important; }
/* A steady cursor: a blink animation would run on wall-clock time, not cast time. */
.ap-cursor, .ap-terminal .ap-cursor-a, .ap-terminal .ap-cursor-b { animation: none !important; }
</style></head>
<body>
<div class="window" id="win">
  <div class="bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span>
    <span class="title">%(title_html)s</span></div>
  <div class="term"><div id="player"></div></div>
</div>
<script src="asciinema-player.min.js"></script>
<script>
window.ready = (async () => {
  await document.fonts.load('400 20px "JetBrains Mono"');
  await document.fonts.load('700 20px "JetBrains Mono"');
  window.player = AsciinemaPlayer.create("cast.cast", document.getElementById("player"), {
    cols: %(cols)d, rows: %(rows)d, autoPlay: false, preload: true, controls: false, fit: false,
    theme: "hermes", terminalFontFamily: '"JetBrains Mono", "Apple Symbols", Menlo, monospace',
    terminalFontSize: "%(font)spx", terminalLineHeight: %(lh)s, idleTimeLimit: 1e9,
  });
  await new Promise(r => setTimeout(r, 300));
  await window.player.seek(0);
  return true;
})();
</script></body></html>
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def cast_info(path: Path) -> tuple[dict, list[float]]:
    lines = path.read_text().splitlines()
    header = json.loads(lines[0])
    t, times = 0.0, []
    for ln in lines[1:]:
        if ln.strip():
            dt, code, _ = json.loads(ln)
            t += dt
            if code == "o":
                times.append(t)
    header["_duration"] = t
    return header, times


def ffmpeg_writer(out: Path, fps: int, crf: int) -> subprocess.Popen:
    return subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "png", "-i", "-",
         "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-tune", "animation",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE,
    )


def render_player(cast: Path, out: Path, args) -> None:
    from playwright.sync_api import sync_playwright

    header, out_times = cast_info(cast)
    cols, rows = header["term"]["cols"], header["term"]["rows"]
    W, H = args.width, args.height
    lh, pad, bar, margin = 1.28, 22, 44, args.margin
    # Largest font for which cols x rows (plus frame) fits the canvas; JetBrains Mono advance = 0.6 em.
    font = min((W - 2 * margin - 2 * pad) / (cols * 0.6), (H - 2 * margin - bar - 2 * pad) / (rows * lh))
    font = round(font * 2) / 2
    title = header.get("title") or cast.stem
    work = Path(tempfile.mkdtemp(prefix="render-", dir=args.tmp))
    try:
        for f in ("asciinema-player.min.js", "asciinema-player.css"):
            shutil.copy(CACHE / f, work / f)
        shutil.copytree(CACHE / "fonts", work / "fonts")
        shutil.copy(cast, work / "cast.cast")
        ansi_vars = " ".join(f"--term-color-{i}: {c};" for i, c in enumerate(PALETTE["ansi"]))
        title_html = title.replace("&", "&amp;").replace("<", "&lt;")
        if ":" in title_html:
            a, b = title_html.split(":", 1)
            title_html = f"<b>{a}</b> · {b.strip()}"
        (work / "index.html").write_text(PAGE % {
            **PALETTE, "W": W, "H": H, "cols": cols, "rows": rows, "font": font, "lh": lh, "pad": pad,
            "ansi_vars": ansi_vars, "title_html": title_html,
        })
        duration = header["_duration"] + args.hold
        n = int(duration * args.fps) + 1
        writer = ffmpeg_writer(out, args.fps, args.crf)
        t_start = time.monotonic()
        # The player fetches the cast, which Chromium refuses from file://; serve the work dir.
        handler = functools.partial(_QuietHandler, directory=str(work))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/index.html")
            page.evaluate("window.ready")
            last_png, last_i, ev = None, 0, 0
            for i in range(n):
                t = min(i / args.fps, header["_duration"])
                # Only re-render when the screen changed since the last frame.
                changed = False
                while ev < len(out_times) and out_times[ev] <= t:
                    ev, changed = ev + 1, True
                if last_png is None or changed:
                    page.evaluate("t => window.player.seek(t)", t)
                    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                    last_png = page.screenshot(type="png")
                for s in args.still:
                    if abs(s - i / args.fps) < 0.5 / args.fps:
                        still = out.with_name(f"{out.stem}@{s:g}s.png")
                        still.write_bytes(last_png)
                writer.stdin.write(last_png)
                if i - last_i >= args.fps * 10:
                    last_i = i
                    print(f"  {i}/{n} frames, {time.monotonic() - t_start:.0f} s", file=sys.stderr)
            browser.close()
        server.shutdown()
        writer.stdin.close()
        if writer.wait() != 0:
            raise RuntimeError("ffmpeg failed")
    finally:
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)
    print(f"{out} ({duration:.1f} s, {args.width}x{args.height}@{args.fps}, font {font}px, "
          f"{time.monotonic() - t_start:.0f} s to render)")


def render_agg(cast: Path, out: Path, args) -> None:
    gif = out.with_suffix(".gif")
    theme = ",".join(c.lstrip("#") for c in [PALETTE["bg"], PALETTE["fg"], *PALETTE["ansi"]])
    t0 = time.monotonic()
    subprocess.run(
        ["agg", "--quiet", "--font-dir", str(CACHE / "fonts"),
         "--font-family", "JetBrains Mono,Apple Symbols,Menlo", "--font-size", str(args.agg_font_size),
         "--line-height", "1.28", "--theme", theme, "--fps-cap", str(min(args.fps, 30)),
         "--idle-time-limit", "1000", "--last-frame-duration", str(args.hold), str(cast), str(gif)],
        check=True,
    )
    W, H = args.width, args.height
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(gif), "-vf",
         f"scale=w={W - 2 * args.margin}:h={H - 2 * args.margin}:force_original_aspect_ratio=decrease:flags=lanczos,"
         f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={PALETTE['canvas']},fps={args.fps}",
         "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf), "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(out)],
        check=True,
    )
    if not args.keep_work:
        gif.unlink()
    print(f"{out} (agg, {time.monotonic() - t0:.0f} s to render)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cast", type=Path)
    ap.add_argument("--engine", choices=("player", "agg"), default="player")
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--margin", type=int, default=40)
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--hold", type=float, default=2.0, help="seconds to hold the last frame")
    ap.add_argument("--still", type=float, action="append", default=[], help="also save the frame at this time")
    ap.add_argument("--agg-font-size", type=int, default=28)
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--tmp", default=None)
    args = ap.parse_args()
    if not (CACHE / "asciinema-player.min.js").exists():
        sys.exit(f"missing {CACHE}; run tools/fetch_assets.sh")
    out = args.out or ROOT / "renders" / f"{args.cast.name.removesuffix('.cast')}{'' if args.engine == 'player' else '.agg'}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    (render_player if args.engine == "player" else render_agg)(args.cast, out, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
