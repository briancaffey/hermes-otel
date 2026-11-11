"""Music for the film, made on club.lan with ACE-Step (async jobs you can audition on the site).

    python3 marketing/hackathon/tools/music.py queue            # queue every option in OPTIONS
    python3 marketing/hackathon/tools/music.py queue --only 4 --n 10   # 10 more takes of option 4
    python3 marketing/hackathon/tools/music.py status           # where the queued jobs are
    python3 marketing/hackathon/tools/music.py fetch <id>       # download a pick -> assets/bgm/bed.wav
    python3 marketing/hackathon/tools/music.py fetch 284 --repeat 72.075 80.075   # the film's bed

Every queued job is an inference request on club.lan (Dashboard -> Queue / Media -> Music), so you
can listen there and hand back the id of the one you like. Queued ids are kept in music_jobs.json.
`fetch` stores the original next to the bed and loudness-normalises the bed to -16 LUFS.
`--repeat A B` plays the span A..B (seconds, both on a downbeat) twice, to lengthen a track that ends
before the film does; the join is a 30 ms crossfade just ahead of the beat.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gen_media import request  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
JOBS = Path(__file__).parent / "music_jobs.json"
MODEL = "acestep-v15-turbo"
DURATION = 95
XF = 0.030                    # crossfade at a --repeat join, seconds
TAIL = "instrumental, no vocals, clean mix, steady tempo, builds over the track, clear ending"
OPTIONS = {
    1: ("hybrid orchestral", 100, "cinematic hybrid orchestral score, pulsing string ostinato, deep synth bass, soft taiko, restrained and confident"),
    2: ("warm analog electronic", 100, "warm analog synth arpeggios, soft kick and brushed hats, airy pads, optimistic ambient electronica"),
    3: ("downtempo keys", 90, "downtempo chillhop, jazzy Rhodes electric piano, dusty drums, round bass, focused late-night coding mood"),
    4: ("mythic lyre", 95, "ancient Greek mood, plucked lyre and harp, frame drums, low choir drone, modern sub bass underneath"),
    5: ("synthwave", 105, "retro 80s synthwave, driving bassline, gated snare, bright lead synth, neon night drive"),
    6: ("minimalist piano", 100, "neo-classical minimalism, repeating piano patterns, cello and violin swells, uplifting and precise"),
    7: ("bright product launch", 110, "upbeat indie electronic, plucky marimba, hand claps, bright synth chords, product launch energy"),
    8: ("dark techno", 120, "dark cinematic techno, four-on-the-floor kick, rolling bass, mysterious pads, lifts into a brighter section"),
    9: ("post-rock", 90, "ambient post-rock, shimmering delayed guitars, warm bass, swelling drums, slow rise to a full band"),
    10: ("heroic adventure", 100, "orchestral adventure, brass fanfare motifs, staccato strings, timpani, heroic but not bombastic"),
}


def api(method, path, body=None):
    raw, _ = request(path, body=json.dumps(body).encode() if body is not None else None,
                     ctype="application/json" if body is not None else None)
    return json.loads(raw) if raw else {}


def load():
    return json.loads(JOBS.read_text()) if JOBS.exists() else []


def queue(only, n):
    jobs = load()
    rng = random.Random()
    for k in ([only] if only else OPTIONS):
        label, bpm, prompt = OPTIONS[k]
        for _ in range(n):
            seed = rng.randint(1, 2_000_000_000)
            body = {"model": MODEL, "prompt": f"{prompt}, {TAIL}", "lyrics": "[inst]",
                    "audio_duration": DURATION, "audio_format": "wav", "bpm": bpm, "seed": seed,
                    "use_random_seed": False, "async": True, "collection": "Hermes OTel film music"}
            job = api("POST", "/v1/music/generations", body)
            jobs.append({"id": job.get("id"), "option": k, "label": label, "bpm": bpm, "seed": seed})
            print(f"  queued id={job.get('id')}  option {k} ({label}, {bpm} bpm) seed {seed}")
    JOBS.write_text(json.dumps(jobs, indent=1) + "\n")


def status():
    for j in load():
        st = api("GET", f"/v1/jobs/{j['id']}")
        print(f"  id={j['id']:>6}  {st.get('status', '?'):<10} option {j['option']} ({j['label']})")


def fetch(jid, repeat=None):
    st = api("GET", f"/v1/jobs/{jid}")
    if st.get("status") != "PROCESSED":
        sys.exit(f"job {jid} is {st.get('status')}")
    url = st.get("output_audio_url") or st.get("result_url") or (st.get("result") or {}).get("url")
    if not url:
        sys.exit(f"no result url in job {jid}: {str(st)[:300]}")
    data, _ = request(url)
    out = ROOT / "assets" / "bgm"
    out.mkdir(parents=True, exist_ok=True)
    src = out / f"pick-{jid}.wav"
    src.write_bytes(data)
    norm = "loudnorm=I=-16:TP=-1.5:LRA=11"
    if repeat:
        a, b = (t - 0.010 for t in repeat)      # cut 10 ms ahead of the beat so the transient stays whole
        graph = (f"[0:a]atrim=0:{b + XF / 2:.4f},asetpts=PTS-STARTPTS[x];"
                 f"[0:a]atrim={a - XF / 2:.4f},asetpts=PTS-STARTPTS[y];"
                 f"[x][y]acrossfade=d={XF}:c1=tri:c2=tri,{norm}")
        flt = ["-filter_complex", graph]
    else:
        flt = ["-af", norm]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *flt, "-ar", "48000", str(out / "bed.wav")],
                   check=True)
    print(f"  {src.name} -> assets/bgm/bed.wav (-16 LUFS); now run tools/build.py and render")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["queue", "status", "fetch"])
    ap.add_argument("id", nargs="?")
    ap.add_argument("--only", type=int)
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--repeat", type=float, nargs=2, metavar=("A", "B"))
    a = ap.parse_args()
    if a.cmd == "queue":
        queue(a.only, a.n)
    elif a.cmd == "status":
        status()
    else:
        fetch(a.id, a.repeat)


if __name__ == "__main__":
    main()
