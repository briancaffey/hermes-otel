"""Narrate the film on club.lan's Narration Studio and pull the clips back for the build.

    python3 marketing/hackathon/tools/narrate.py            # sync SCRIPT.md, produce, export
    python3 marketing/hackathon/tools/narrate.py --design   # (re)design the narrator voice first
    python3 marketing/hackathon/tools/narrate.py --redo S03 # force new takes for some scenes

What happens on the site (inference.club, PRD 12 Narration Studio):
  1. --design: a "voice design" episode auditions the reference line on DESIGN_TAKES Dia seeds; each
     take is cleaned (Maxine StudioVoice), transcribed (Nemotron ASR), trimmed, levelled and scored;
     the best is saved to the voice library as the default "Hermes OTel Narrator" sample.
  2. The film episode gets one segment per scene (the scene's sentences joined), the pronunciation
     lexicon and the narrator sample (Dia clones it for every take: one consistent voice).
  3. Autopilot (POST /v1/episodes/<id>/produce): take -> clean -> ASR -> trim dead air -> -16 LUFS ->
     grade the transcript against the line -> retake while the score is under the bar.
  4. Export (GET /v1/episodes/<id>/export): 01.wav.. + manifest.json -> assets/voice/SNN.wav and
     cues.json (sentence windows from the ASR word timings) for tools/build.py.

Changed lines only: a scene whose text changed is PATCHed (which sends it back to pending) and
re-voiced; untouched scenes keep their takes. Episode ids live in tools/narration_state.json.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gen_media import request  # noqa: E402  (same club.lan client + token)

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(__file__).parent / "narration_state.json"
FILM_TITLE = "Hermes OTel — hackathon film"
DESIGN_TITLE = "Hermes OTel — narrator voice design"
SPEAKER = "Hermes OTel Narrator"
DESIGN_LINE = ("Every turn your agent takes becomes a trace: the model calls, the tools, the tokens "
               "and the cost, timed and nested, in the tools you already run.")
DESIGN_TAKES = 6
# Written -> spoken. Applied by the site before Dia speaks; grading compares against the spoken form.
LEXICON = {
    "Hermes OTel": "Hermes Oh-tel",
    "OTel": "Oh-tel",
    "OTLP": "O-T-L-P",
    "SQLite": "S-Q-Lite",
    "OpenTelemetry": "Open Telemetry",
    "OpenInference": "Open Inference",
    "GenAI": "Gen A-I",
    "async": "a-sync",
}
SETTINGS = {"lexicon": LEXICON, "max_attempts": 4, "accept_score": 8.5, "temperature": 1.3,
            "loudness_lufs": -16.0, "trim_max_gap": 0.35}


def api(method: str, path: str, body: dict | None = None):
    import urllib.request

    from gen_media import BASE, CTX, token
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {token()}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, context=CTX, timeout=600) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def parse_script() -> list[dict]:
    scenes, cur = [], None
    for line in (ROOT / "SCRIPT.md").read_text().splitlines():
        m = re.match(r"^## (S\d\d) — (.*)$", line)
        if m:
            cur = {"sid": m.group(1), "title": m.group(2), "kicker": "", "sentences": []}
            scenes.append(cur)
        elif cur and line.startswith(">"):
            cur["kicker"] = line[1:].strip()
        elif cur and line.strip():
            cur["sentences"].append(line.strip())
    return scenes


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=2) + "\n")


def wait_episode(eid: int, label: str) -> dict:
    t0 = time.time()
    while True:
        ep = api("GET", f"/v1/episodes/{eid}")
        st = [s["status"] for s in ep["segments"]]
        busy = sum(s in ("queued", "generating") for s in st)
        print(f"\r  {label}: {busy} working · {st.count('ready')} ready · {st.count('flagged')} flagged "
              f"· {st.count('error')} error · {time.time() - t0:.0f}s   ", end="", flush=True)
        if not busy:
            print()
            return ep
        time.sleep(10)


def design_voice(st: dict) -> int:
    eid = st.get("design_episode")
    if not eid:
        ep = api("POST", "/v1/episodes", {"title": DESIGN_TITLE,
                                          "description": "Auditions for the film's narrator."})
        eid = ep["id"]
        api("POST", f"/v1/episodes/{eid}/segments", {"text": DESIGN_LINE})
        st["design_episode"] = eid
        save_state(st)
    api("PATCH", f"/v1/episodes/{eid}", {"settings": {k: v for k, v in SETTINGS.items()
                                                       if k != "lexicon"}})
    seg = api("GET", f"/v1/episodes/{eid}")["segments"][0]
    api("POST", f"/v1/segments/{seg['id']}/takes", {"count": DESIGN_TAKES})
    seg = wait_episode(eid, "auditions")["segments"][0]
    takes = sorted(seg["variants"], key=lambda v: (v.get("grade") or {}).get("take_score", 0), reverse=True)
    for v in takes:
        g = v.get("grade") or {}
        print(f"    take {v['id']} seed={v.get('seed')} score={g.get('take_score')} "
              f"wps={g.get('wps')} dur={v.get('duration_seconds')} :: {v.get('transcript', '')[:70]}")
    best = takes[0]
    sample = api("POST", f"/v1/segments/{seg['id']}/variants/{best['id']}/save-voice",
                 {"speaker_name": SPEAKER, "label": f"seed {best.get('seed')}", "is_default": True})
    print(f"  saved voice sample {sample['id']} ({SPEAKER}, seed {best.get('seed')})")
    st["voice_sample"] = sample["id"]
    save_state(st)
    return sample["id"]


def sync_film(st: dict, scenes: list[dict], redo: set[str]) -> int:
    eid = st.get("film_episode")
    if not eid:
        eid = api("POST", "/v1/episodes", {"title": FILM_TITLE,
                                           "description": "marketing/hackathon/SCRIPT.md"})["id"]
        st["film_episode"] = eid
        st["segments"] = {}
        save_state(st)
    api("PATCH", f"/v1/episodes/{eid}", {"settings": SETTINGS, "voice_sample_id": st["voice_sample"]})
    ep = api("GET", f"/v1/episodes/{eid}")
    by_id = {s["id"]: s for s in ep["segments"]}
    for sc in scenes:
        text = " ".join(sc["sentences"])
        segid = st["segments"].get(sc["sid"])
        if segid not in by_id:
            segid = api("POST", f"/v1/episodes/{eid}/segments", {"text": text})["id"]
            st["segments"][sc["sid"]] = segid
            save_state(st)
        elif by_id[segid]["text"] != text:
            api("PATCH", f"/v1/segments/{segid}", {"text": text})
            print(f"  {sc['sid']}: line changed, will re-voice")
        if sc["sid"] in redo:
            api("POST", f"/v1/segments/{segid}/regenerate", {})
    return eid


def export(eid: int, st: dict, scenes: list[dict]) -> None:
    raw, _ = request(f"/v1/episodes/{eid}/export")
    zf = zipfile.ZipFile(io.BytesIO(raw))
    manifest = json.loads(zf.read("manifest.json"))
    by_seg = {s["segment_id"]: s for s in manifest["segments"]}
    vdir = ROOT / "assets" / "voice"
    vdir.mkdir(parents=True, exist_ok=True)
    cues = {"provider": "club.lan narration studio (Dia 1.6B + Maxine StudioVoice + Nemotron ASR)",
            "voice": f"{SPEAKER} (voice sample {st.get('voice_sample')})", "speed": 1.0, "gap": 0.0,
            "episode": eid, "scenes": {}}
    for sc in scenes:
        m = by_seg.get(st["segments"][sc["sid"]])
        if not m or not m.get("file"):
            sys.exit(f"{sc['sid']} has no take yet")
        (vdir / f"{sc['sid']}.wav").write_bytes(zf.read(m["file"]))
        words, dur = m.get("words") or [], float(m["duration"])
        # Sentence windows: split the ASR words in proportion to each sentence's word count.
        counts = [len(s.split()) for s in sc["sentences"]]
        total, sents, cum = sum(counts), [], 0
        for i, (s, c) in enumerate(zip(sc["sentences"], counts)):
            a = round(cum / total * len(words))
            cum += c
            b = round(cum / total * len(words)) - 1
            start = 0.0 if i == 0 or not words else words[min(a, len(words) - 1)]["start"]
            end = dur if i == len(counts) - 1 or not words else words[max(a, min(b, len(words) - 1))]["end"]
            sents.append({"text": s, "start": round(start, 3), "end": round(end, 3)})
        g = m.get("grade") or {}
        cues["scenes"][sc["sid"]] = {"title": sc["title"], "kicker": sc["kicker"], "duration": round(dur, 3),
                                     "sentences": sents, "seed": m.get("seed"),
                                     "take_score": g.get("take_score"), "transcript": m.get("transcript"),
                                     "words": [{"t": w["word"], "s": w["start"], "e": w["end"]} for w in words]}
        print(f"  {sc['sid']} {dur:5.2f}s score={g.get('take_score')} attempt={g.get('attempt')} "
              f"{m.get('status')} :: {m.get('transcript', '')[:60]}")
    (ROOT / "cues.json").write_text(json.dumps(cues, indent=1) + "\n")
    print(f"  total voice {sum(s['duration'] for s in cues['scenes'].values()):.1f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", action="store_true")
    ap.add_argument("--redo", default="")
    ap.add_argument("--export-only", action="store_true")
    ap.add_argument("--reprocess", action="store_true",
                    help="re-run clean -> ASR -> trim -> grade on the current takes (no new takes)")
    a = ap.parse_args()
    st = load_state()
    scenes = parse_script()
    if (a.design or not st.get("voice_sample")) and not (a.reprocess or a.export_only):
        design_voice(st)
    eid = sync_film(st, scenes, {x for x in a.redo.split(",") if x})
    if a.reprocess:
        for segid in st["segments"].values():
            api("POST", f"/v1/segments/{segid}/process", {})
        wait_episode(eid, "reprocess")
    elif not a.export_only:
        api("POST", f"/v1/episodes/{eid}/produce", {})
        ep = wait_episode(eid, "film")
        bad = [s for s in ep["segments"] if s["status"] != "ready"]
        if bad:
            print(f"  {len(bad)} segment(s) not ready (best take kept): {[s['id'] for s in bad]}")
    export(eid, st, scenes)


if __name__ == "__main__":
    main()
