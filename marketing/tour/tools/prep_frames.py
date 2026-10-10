"""Downscale every captured screenshot to a 1920-wide JPEG under assets/frames/<source>-<page>.jpg."""
import pathlib

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
out = ROOT / "assets" / "frames"
out.mkdir(parents=True, exist_ok=True)
n = 0
for png in sorted((ROOT / "assets" / "shots").glob("*/*.png")):
    dest = out / f"{png.parent.name}-{png.stem}.jpg"
    if dest.exists() and dest.stat().st_mtime >= png.stat().st_mtime:
        continue
    im = Image.open(png).convert("RGB")
    w, h = im.size
    if w > 1920:
        im = im.resize((1920, round(h * 1920 / w)), Image.LANCZOS)
    im.save(dest, "JPEG", quality=88, optimize=True)
    n += 1
print(f"{n} frames written to {out}")
