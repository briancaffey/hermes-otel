"""Synthesize SCRIPT.md with NVIDIA Magpie (one sentence per request) into per-scene WAVs.

    python marketing/tour/tools/synth_voice.py [--url https://magpie.lan] [--only S06]

Writes assets/voice/<scene>.mp3 (22.05 kHz mono, 160 kbps) and assets/voice/durations.json. Sentences are
joined with a 320 ms pause; scenes are separate files so the composition can place each one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE = "Magpie-Multilingual.EN-US.Mia"


def scenes(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^## (S\d+)", line)
        if m:
            cur = m.group(1)
            out[cur] = []
            continue
        if cur and line.strip() and not line.startswith("#"):
            out[cur].append(line.strip())
    return out


def synth(url: str, sentence: str, dest: Path) -> None:
    boundary = "----hermesotel"
    fields = {"text": sentence, "language": "en-US", "voice": VOICE}
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url + "/v1/audio/synthesize", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
        dest.write_bytes(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://magpie.lan")
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    voice = ROOT / "assets" / "voice"
    cache = voice / "_sentences"
    cache.mkdir(parents=True, exist_ok=True)
    sc = scenes((ROOT / "SCRIPT.md").read_text())
    durations = {}
    for name, lines in sc.items():
        if a.only and name != a.only:
            continue
        parts = []
        for s in lines:
            h = hashlib.sha1((VOICE + s).encode()).hexdigest()[:12]
            f = cache / f"{h}.wav"
            if not f.exists() or f.stat().st_size < 1000:
                synth(a.url, s, f)
            parts.append(f)
        # concat with a 320 ms pause between sentences, normalise lightly
        lst = voice / f"_{name}.txt"
        sil = cache / "sil320.wav"
        if not sil.exists():
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "0.32", str(sil)], check=True)
        lines_txt = []
        for i, p in enumerate(parts):
            if i:
                lines_txt.append(f"file '{sil}'")
            lines_txt.append(f"file '{p}'")
        lst.write_text("\n".join(lines_txt) + "\n")
        out = voice / f"{name}.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "22050", "-ac", "1",
                        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-codec:a", "libmp3lame", "-b:a", "160k", str(out)], check=True)
        lst.unlink()
        d = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)]).strip())
        durations[name] = round(d, 2)
        print(f"{name}: {len(parts)} sentences, {d:.1f}s")
    # per-sentence cue offsets for the composition (the same 320 ms pause the concat used)
    cues = {}
    for name, lines in sc.items():
        t, rows = 0.0, []
        for s in lines:
            f = cache / f"{hashlib.sha1((VOICE + s).encode()).hexdigest()[:12]}.wav"
            if not f.exists():
                break
            d = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)]).strip())
            rows.append({"start": round(t, 2), "dur": round(d, 2), "text": s})
            t += d + 0.32
        cues[name] = {"sentences": rows, "total": round(max(t - 0.32, 0), 2)}
    (voice / "cues.json").write_text(json.dumps(cues, indent=1))
    prev = {}
    dj = voice / "durations.json"
    if dj.exists():
        prev = json.loads(dj.read_text())
    prev.update(durations)
    dj.write_text(json.dumps(prev, indent=1))
    print("total", round(sum(prev.values()), 1), "s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
