"""Stage every binary the hackathon film needs into assets/ (gitignored). Nothing here is committed.

    uv run --with pillow python marketing/hackathon/tools/prep_assets.py [--hermes ~/git/hermes-agent]

- fonts:  the Nous design-system faces Hermes Agent's dashboard ships (web/public/fonts) plus
          JetBrains Mono, copied from a hermes-agent checkout.
- art:    the design-system filler art (web/public/ds-assets), same checkout.
- art+:   the CC0 Goltzius "Mercurius" (1592, NGA) engraving and its two duotone plates.
- clips:  the real span-tree terminal recording, cut (real time) plus two stills.
- shots:  the real captures already committed under marketing/tour/assets/shots/<source>/,
          re-encoded as high-quality JPEGs at full resolution (3200x2000) so punch-ins stay sharp.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parents[1]
TOUR_SHOTS = HERE.parent / "tour" / "assets" / "shots"

FONTS = [
    "public/fonts/Collapse-Bold.woff2",
    "public/fonts/Collapse-Regular.woff2",
    "public/fonts/Collapse-Light.woff2",
    "public/fonts/RulesExpanded-Bold.woff2",
    "public/fonts/RulesExpanded-Regular.woff2",
    "public/fonts/RulesCompressed-Medium.woff2",
    "public/fonts/Mondwest-Regular.woff2",
    "public/fonts/Neuebit-Bold.woff2",
    "public/fonts-terminal/JetBrainsMono-Regular.woff2",
    "public/fonts-terminal/JetBrainsMono-Bold.woff2",
]
ART = ["public/ds-assets/filler-bg0.jpg"]

# Public-domain art, fetched once. CC0: National Gallery of Art open access.
REMOTE_ART = {
    "goltzius-mercury.jpg": "https://upload.wikimedia.org/wikipedia/commons/9/9c/"
    "Hendrik_Goltzius_after_Polidoro_da_Caravaggio%2C_Mercury%2C_probably_1592%2C_NGA_60693.jpg",
}

# Real terminal recordings from marketing/terminal (tools/take_all.sh renders them), cut here:
# (source render, start s, duration s, playback speed) -> assets/clips/<name>.mp4
TERMINAL = HERE.parent / "terminal" / "renders"
CLIPS = {
    "span-tree": ("span-tree.mp4", 39.0, 7.15, 1.0),   # the skill runs, then the tree renders (~43.8 s)
}
# (source render, at s) -> assets/clips/<name>.jpg, a full-resolution still
STILLS = {
    "terminal-answer": ("span-tree.mp4", 25.0),
    "terminal-tree": ("span-tree.mp4", 46.0),
}
# HyperFrames' bundled CC0 effects library (installed with the media-use skill)
SFX_DIR = Path.home() / ".claude" / "skills" / "media-use" / "audio" / "assets" / "sfx"
SFX = ["whoosh.mp3", "whoosh-short.mp3", "whoosh-cinematic.mp3", "impact-bass-1.mp3", "impact-bass-2.mp3",
       "riser.mp3", "key-press.mp3", "click-soft.mp3", "pop.mp3", "typing.mp3", "glitch-1.mp3"]

# (source dir, file stem) -> staged as assets/shots/<source>-<stem>.jpg
SHOTS = {
    "lgtm-local": ["detail-hero", "detail-hero-full", "detail-error", "live", "traces", "metrics", "logs",
                   "logs-trace", "sessions", "settings", "ui-trace", "ui-logs", "ui-metrics"],
    "openobserve-local": ["ui-trace", "ui-metrics", "ui-logs", "ui-traces"],
    "signoz-local": ["ui-trace", "ui-traces", "ui-metrics", "ui-logs"],
    "uptrace-local": ["ui-trace", "ui-traces", "ui-metrics", "ui-logs"],
    "jaeger-local": ["ui-trace", "ui-traces"],
    "phoenix-local": ["ui-trace", "ui-traces"],
    "langfuse-local": ["ui-trace", "ui-traces", "ui-sessions"],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes", default=str(Path.home() / "git" / "hermes-agent"))
    a = ap.parse_args()
    web = Path(a.hermes).expanduser() / "web"

    fonts = HERE / "assets" / "fonts"
    art = HERE / "assets" / "art"
    shots = HERE / "assets" / "shots"
    for d in (fonts, art, shots):
        d.mkdir(parents=True, exist_ok=True)

    for rel in FONTS:
        src = web / rel
        if src.exists():
            shutil.copy2(src, fonts / src.name)
        else:
            print(f"missing font {src}")
    for rel in ART:
        src = web / rel
        if src.exists():
            shutil.copy2(src, art / src.name)
        else:
            print(f"missing art {src}")

    for name, url in REMOTE_ART.items():
        dst = art / name
        if not dst.exists():
            req = urllib.request.Request(url, headers={"User-Agent": "hermes-otel-video/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                dst.write_bytes(r.read())
    # duotone plates of the engraving: ink -> the Hermes site blue on its paper white, and
    # ink -> cream on Hermes Teal. Done here, once, instead of as a CSS filter at render time.
    duo = {"blue": ("#0000f2", "#f2f2f2"), "teal": ("#ffe6cb", "#041c1c")}
    src = Image.open(art / "goltzius-mercury.jpg").convert("L")
    src = ImageOps.autocontrast(src, cutoff=1)
    for name, (ink, paper) in duo.items():
        dst = art / f"mercury-{name}.jpg"
        if not dst.exists():
            ImageOps.colorize(src, black=ink, white=paper).save(dst, "JPEG", quality=92)

    clips = HERE / "assets" / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    for name, (render, start, dur, speed) in CLIPS.items():
        src_v, dst = TERMINAL / render, clips / f"{name}.mp4"
        if not src_v.exists():
            print(f"missing terminal render {src_v} (run marketing/terminal/tools/take_all.sh)")
            continue
        if not dst.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", str(dur), "-i", str(src_v),
                            "-vf", f"setpts=PTS/{speed},fps=30", "-an", "-c:v", "libx264", "-crf", "16",
                            "-pix_fmt", "yuv420p", str(dst)], check=True)

    for name, (render, at) in STILLS.items():
        src_v, dst = TERMINAL / render, clips / f"{name}.jpg"
        if src_v.exists() and not dst.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", str(src_v), "-frames:v", "1",
                            "-q:v", "2", str(dst)], check=True)

    sfx = HERE / "assets" / "sfx"
    sfx.mkdir(parents=True, exist_ok=True)
    for name in SFX:
        if (SFX_DIR / name).exists():
            shutil.copy2(SFX_DIR / name, sfx / name)
        else:
            print(f"missing sfx {SFX_DIR / name}")

    n = 0
    for source, stems in SHOTS.items():
        for stem in stems:
            src = TOUR_SHOTS / source / f"{stem}.png"
            if not src.exists():
                print(f"missing shot {src}")
                continue
            dst = shots / f"{source.removesuffix('-local')}-{stem}.jpg"
            if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
                continue
            Image.open(src).convert("RGB").save(dst, "JPEG", quality=92, optimize=True)
            n += 1
    print(f"fonts={len(list(fonts.iterdir()))} art={len(list(art.iterdir()))} shots staged={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
