"""Audition narrator voices for the film on club.lan's Narration Studio (Dia 1.6B).

    python3 marketing/hackathon/tools/voices.py design --n 8   # N new Dia voices -> the voice library
    python3 marketing/hackathon/tools/voices.py audition       # each voice reads the film's opening
    python3 marketing/hackathon/tools/voices.py status         # scores, pace and where to listen
    python3 marketing/hackathon/tools/voices.py use Lyra       # make that voice the film's narrator

A Dia voice is a seed: with no reference clip, each seed speaks in a different voice. `design` takes
the reference line on N random seeds (each take cleaned by StudioVoice, transcribed, trimmed, levelled
and scored, like any Studio take) and saves every take that passes MIN_SCORE to the voice library
under its own name ("Narrator Lyra", ...). `audition` then makes one episode where every saved voice
reads AUDITION_SCENES from SCRIPT.md through the full autopilot, cloning its own sample, so the
voices can be compared on the real lines at https://club.lan (Narration Studio). `use` points
narration_state.json at the pick; run narrate.py --redo all after it to re-voice the film.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from narrate import DESIGN_LINE, SETTINGS, api, load_state, parse_script, save_state, wait_episode  # noqa: E402

STATE = Path(__file__).parent / "voices.json"
DESIGN_TITLE = "Hermes OTel — narrator voices (design takes)"
AUDITION_TITLE = "Hermes OTel — narrator voice auditions"
NAMES = ["Lyra", "Orion", "Vega", "Altair", "Corvus", "Cygnus", "Draco", "Auriga", "Carina", "Perseus",
         "Hydra", "Pavo", "Lupus", "Norma", "Volans", "Tucana"]
MIN_SCORE = 8.5
# One segment per group: the scenes' sentences joined. The myth + the black box, then the product line.
AUDITION_SCENES = [("S01", "S02"), ("S03",)]


def load() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"voices": []}


def save(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=1) + "\n")


def design(n: int) -> None:
    st = load()
    if not st.get("design_episode"):
        ep = api("POST", "/v1/episodes", {"title": DESIGN_TITLE,
                                          "description": "One Dia seed per take; the good ones become voices."})
        st["design_episode"] = ep["id"]
        st["design_segment"] = api("POST", f"/v1/episodes/{ep['id']}/segments", {"text": DESIGN_LINE})["id"]
        save(st)
    eid, segid = st["design_episode"], st["design_segment"]
    api("PATCH", f"/v1/episodes/{eid}", {"settings": {k: v for k, v in SETTINGS.items() if k != "lexicon"}})
    while n > 0:
        api("POST", f"/v1/segments/{segid}/takes", {"count": min(n, 8)})
        n -= 8
        wait_episode(eid, "design takes")
    seg = api("GET", f"/v1/episodes/{eid}")["segments"][0]
    known = {v["variant"] for v in st["voices"]}
    for v in sorted(seg["variants"], key=lambda v: v["id"]):
        g = v.get("grade") or {}
        score = g.get("take_score") or 0
        line = f"take {v['id']} seed={v.get('seed')} score={score} wps={g.get('wps')}"
        if v["id"] in known:
            continue
        if score < MIN_SCORE or len(st["voices"]) >= len(NAMES):
            print(f"  skipped  {line}")
            continue
        name = NAMES[len(st["voices"])]
        sample = api("POST", f"/v1/segments/{segid}/variants/{v['id']}/save-voice",
                     {"speaker_name": f"Narrator {name}", "label": f"seed {v.get('seed')}"})
        st["voices"].append({"name": name, "sample": sample["id"], "variant": v["id"], "seed": v.get("seed"),
                             "score": score, "wps": g.get("wps")})
        save(st)
        print(f"  saved    {line} -> Narrator {name} (voice sample {sample['id']})")


def audition() -> None:
    st = load()
    if not st.get("audition_episode"):
        st["audition_episode"] = api("POST", "/v1/episodes", {
            "title": AUDITION_TITLE, "description": "Every candidate voice reads the film's opening lines."})["id"]
        save(st)
    eid = st["audition_episode"]
    api("PATCH", f"/v1/episodes/{eid}", {"settings": SETTINGS})
    scenes = {s["sid"]: " ".join(s["sentences"]) for s in parse_script()}
    lines = [" ".join(scenes[sid] for sid in group) for group in AUDITION_SCENES]
    for v in st["voices"]:
        if v.get("segments"):
            continue
        v["segments"] = [api("POST", f"/v1/episodes/{eid}/segments",
                             {"text": text, "voice_sample_id": v["sample"]})["id"] for text in lines]
        save(st)
        print(f"  Narrator {v['name']}: segments {v['segments']}")
    api("POST", f"/v1/episodes/{eid}/produce", {})
    wait_episode(eid, "auditions")
    status()


def status() -> None:
    st = load()
    eid = st.get("audition_episode")
    segs = {s["id"]: s for s in api("GET", f"/v1/episodes/{eid}")["segments"]} if eid else {}
    for v in st["voices"]:
        print(f"  Narrator {v['name']:<8} sample {v['sample']:>3}  seed {v['seed']}")
        for segid in v.get("segments", []):
            s = segs.get(segid) or {}
            sel = next((x for x in s.get("variants", []) if x["id"] == s.get("selected_variant_id")), {})
            g = sel.get("grade") or {}
            print(f"      segment {segid} {s.get('status', '?'):<10} takes={len(s.get('variants', []))} "
                  f"score={g.get('take_score')} wps={g.get('wps')} {sel.get('duration_seconds') or 0:.1f}s "
                  f"{sel.get('cleaned_audio_url') or ''}")
    if eid:
        print(f"  listen: episode {eid} \"{AUDITION_TITLE}\" in the Narration Studio on club.lan")


def use(name: str) -> None:
    v = next((v for v in load()["voices"] if v["name"].lower() == name.lower()), None)
    if not v:
        sys.exit(f"no voice named {name}")
    ns = load_state()
    ns["voice_sample"], ns["voice_name"] = v["sample"], f"Narrator {v['name']}"
    save_state(ns)
    print(f"  film narrator -> Narrator {v['name']} (voice sample {v['sample']}); now: narrate.py --redo all")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["design", "audition", "status", "use"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--n", type=int, default=8)
    a = ap.parse_args()
    if a.cmd == "design":
        design(a.n)
    elif a.cmd == "audition":
        audition()
    elif a.cmd == "status":
        status()
    else:
        use(a.name)


if __name__ == "__main__":
    main()
