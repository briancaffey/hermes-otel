"""Synthesise SCRIPT.md one sentence at a time into per-scene WAVs and write cues.json.

    python3 marketing/hackathon/tools/synth_voice.py                       # kokoro (local)
    python3 marketing/hackathon/tools/synth_voice.py --provider magpie --url https://magpie.lan
    python3 marketing/hackathon/tools/synth_voice.py --provider openai --url https://<host> \
        --model <tts-model> --voice <voice>                                   # any /v1/audio/speech

kokoro needs a python with kokoro-onnx + soundfile: HYPERFRAMES_PYTHON=/path/to/venv/bin/python.
openai reads an optional bearer token from $TTS_API_KEY.

Outputs (gitignored except cues.json, which is source for the composition timing):
  assets/voice/_sentences/<hash>.wav   cache, keyed by provider+voice+spoken text
  assets/voice/<scene>.wav             sentences joined with --gap seconds of silence
  cues.json                            per scene: duration and each sentence's start/end and text
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = ROOT / "assets" / "voice"
CACHE = VOICE_DIR / "_sentences"
SR = 24000


def parse_script(text: str) -> dict[str, dict]:
    """{scene: {"title": str, "kicker": str|None, "lines": [str]}} in file order."""
    out: dict[str, dict] = {}
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^## (S\d+)\s*(?:—\s*(.*))?$", line)
        if m:
            cur = m.group(1)
            out[cur] = {"title": (m.group(2) or "").strip(), "kicker": None, "lines": []}
            continue
        if not cur or not line or line.startswith("#"):
            continue
        if line.startswith(">"):
            out[cur]["kicker"] = line[1:].strip()
        else:
            out[cur]["lines"].append(line)
    return out


def spoken(text: str, table: dict[str, str]) -> str:
    for k in sorted((k for k in table if not k.startswith("_")), key=len, reverse=True):
        text = re.sub(rf"(?<![\w-]){re.escape(k)}(?![\w-])", table[k], text)
    return text


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def synth(a: argparse.Namespace, text: str, dest: Path) -> None:
    tmp = dest.with_name(dest.stem + ".raw.src")
    if a.provider == "kokoro":
        r = subprocess.run(["npx", "hyperframes", "tts", text, "-v", a.voice, "-s", str(a.speed),
                            "-o", str(tmp.with_suffix(".tmp.wav")), "--json"], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"kokoro failed: {r.stdout[-400:]}{r.stderr[-400:]}")
        tmp = tmp.with_suffix(".tmp.wav")
    elif a.provider == "magpie":
        boundary = "----hermesotel"
        body = b""
        for k, v in {"text": text, "language": "en-US", "voice": a.voice}.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(a.url + "/v1/audio/synthesize", data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=120, context=_ctx()) as r:
            tmp.write_bytes(r.read())
    else:  # openai-compatible
        payload = {"model": a.model, "input": text, "voice": a.voice, "response_format": "wav", "speed": a.speed}
        headers = {"Content-Type": "application/json"}
        if os.environ.get("TTS_API_KEY"):
            headers["Authorization"] = f"Bearer {os.environ['TTS_API_KEY']}"
        req = urllib.request.Request(a.url.rstrip("/") + "/v1/audio/speech", data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=180, context=_ctx()) as r:
            tmp.write_bytes(r.read())
    # normalise every provider to mono 24 kHz WAV, trimmed of leading/trailing silence
    trim = "silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-af", trim, "-ac", "1", "-ar", str(SR), str(dest)],
                   check=True)
    tmp.unlink(missing_ok=True)


def duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["kokoro", "magpie", "openai"], default="kokoro")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--url", default="https://magpie.lan")
    ap.add_argument("--model", default="tts-1")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--gap", type=float, default=0.35, help="silence between sentences of one scene")
    a = ap.parse_args()
    a.voice = a.voice or {"kokoro": "bm_george", "magpie": "Magpie-Multilingual.EN-US.Mia", "openai": "alloy"}[a.provider]

    scenes = parse_script((ROOT / "SCRIPT.md").read_text())
    table = json.loads((ROOT / "tools" / "pronounce.json").read_text())
    CACHE.mkdir(parents=True, exist_ok=True)
    gap = VOICE_DIR / "_gap.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono", "-t", str(a.gap),
                    str(gap)], check=True)

    cues: dict = {"provider": a.provider, "voice": a.voice, "speed": a.speed, "gap": a.gap, "scenes": {}}
    for sid, sc in scenes.items():
        parts, sentences, t = [], [], 0.0
        for i, line in enumerate(sc["lines"]):
            say = spoken(line, table)
            key = hashlib.sha1(f"{a.provider}|{a.voice}|{a.speed}|{say}".encode()).hexdigest()[:16]
            wav = CACHE / f"{key}.wav"
            if not wav.exists():
                print(f"  synth {sid}: {say}")
                synth(a, say, wav)
            d = duration(wav)
            if i:
                parts.append(gap)
                t += a.gap
            sentences.append({"text": line, "start": round(t, 3), "end": round(t + d, 3)})
            parts.append(wav)
            t += d
        out = VOICE_DIR / f"{sid}.wav"
        lst = VOICE_DIR / f"{sid}.txt"
        lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                        str(out)], check=True)
        lst.unlink()
        cues["scenes"][sid] = {"title": sc["title"], "kicker": sc["kicker"], "duration": round(duration(out), 3),
                               "sentences": sentences}
        print(f"{sid} {cues['scenes'][sid]['duration']:6.2f}s  {sc['title']}")
    total = sum(s["duration"] for s in cues["scenes"].values())
    words = sum(len(l["text"].split()) for s in cues["scenes"].values() for l in s["sentences"])
    print(f"spoken total {total:.1f}s, {words} words")
    (ROOT / "cues.json").write_text(json.dumps(cues, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
