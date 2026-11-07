"""Placeholder music bed with local MusicGen (facebook/musicgen-medium on Apple MPS / CPU).

    uv run --with torch --with transformers --with soundfile --with numpy \
        python marketing/hackathon/tools/gen_music.py [--seconds 88] [--model facebook/musicgen-small]

Writes assets/bgm/bed.wav (gitignored). MusicGen's decoder tops out near 30 s per pass, so this
generates three 30 s passes, each continuing from the tail of the last (audio prompt), and joins
them with short crossfades. Replace with a produced track (inference.club, or your own) by dropping
a file at assets/bgm/bed.wav; the composition only reads that path.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from transformers import AutoProcessor, MusicgenForConditionalGeneration

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ("cinematic minimal electronic score, 100 bpm, pulsing analog synth bass and a ticking arpeggio, "
          "dark and focused at first, building steadily with tight drums, then opening into a bright, wide, "
          "confident synth anthem, instrumental, no vocals, clean mix")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=88.0)
    ap.add_argument("--model", default="facebook/musicgen-medium")
    ap.add_argument("--prompt", default=PROMPT)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    torch.manual_seed(a.seed)
    proc = AutoProcessor.from_pretrained(a.model)
    model = MusicgenForConditionalGeneration.from_pretrained(a.model).to(dev)
    sr = model.config.audio_encoder.sampling_rate
    tps = model.config.audio_encoder.frame_rate  # tokens per second

    chunk, ctx = 30.0, 8.0
    out = np.zeros(0, dtype=np.float32)
    while len(out) / sr < a.seconds:
        if len(out) == 0:
            inputs = proc(text=[a.prompt], padding=True, return_tensors="pt").to(dev)
            gen_s = chunk
        else:
            tail = out[-int(ctx * sr):]
            inputs = proc(text=[a.prompt], audio=[tail], sampling_rate=sr, padding=True,
                          return_tensors="pt").to(dev)
            gen_s = chunk
        with torch.no_grad():
            audio = model.generate(**inputs, do_sample=True, guidance_scale=3.0,
                                   max_new_tokens=int(gen_s * tps))
        wav = audio[0, 0].float().cpu().numpy()
        if len(out) == 0:
            out = wav
        else:
            new = wav[int(ctx * sr):]  # the continuation repeats the prompt audio first
            out = np.concatenate([out, new])
        print(f"bed {len(out) / sr:.1f}s", flush=True)
    out = out[: int(a.seconds * sr)]
    fade = int(2.0 * sr)
    out[-fade:] *= np.linspace(1, 0, fade)
    peak = np.abs(out).max() or 1.0
    out = out / peak * 0.89  # about -1 dBFS
    dst = ROOT / "assets" / "bgm" / "bed.wav"
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(dst, out, sr, subtype="PCM_16")
    print(f"wrote {dst} ({len(out) / sr:.1f}s @ {sr} Hz)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
