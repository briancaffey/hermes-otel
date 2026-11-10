"""Lay the film out from the narration: scene slots, the CUES line of every scene, index.html, sound.

    python3 marketing/hackathon/tools/build.py

Reads cues.json (tools/narrate.py: per-scene clean voice + ASR word timings from club.lan), then:
  - gives every scene a slot: LEAD + voice + tail (+ EXTRA for picture-only beats);
  - rewrites the `/*CUES*/ ... /*END*/` line in each compositions/sNN.html with that scene's
    duration, lead, sentence windows and its words (scene-local seconds), so a scene can land a
    move on a spoken word with `at(/regex/)` instead of a hand-kept table;
  - writes index.html: one host per scene, the voice clips, one music bed and the sound effects.
Scene files are hand-written; only their CUES line is generated.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEAD = 0.4
TAIL = {"S01": 0.8, "S03": 0.8, "S07": 0.3, "S11": 2.4}
DEFAULT_TAIL = 0.45
EXTRA = {"S06": 1.0, "S07": 7.1, "S10": 2.6}  # S07: rapid backend cuts + wall after the voice; S10: the tree draws
MUSIC_VOL = 0.2               # under the voice; the ACE-Step bed is normalised to -16 LUFS first
LIMIT = 89.9

# Sound: (scene, where, offset s, file, volume). `where` = a regex on a spoken word (first hit),
# ("nth", regex, n), a scene-local time in seconds, or ("end", seconds before the scene ends).
# Only quiet, literal effects: clicks on clicks, keys on typing, short whooshes on cuts.
SFX = [
    ("S02", ("end", 0.5), 0.0, "whoosh-short.mp3", 0.35),
    ("S04", r"nests?", 0.0, "whoosh-short.mp3", 0.3),
    ("S05", r"tab", -0.1, "click-soft.mp3", 0.5),
    ("S05", r"chart", -0.1, "click-soft.mp3", 0.45),
    ("S05", r"log", -0.1, "click-soft.mp3", 0.45),
    ("S06", r"connect", 0.0, "click-soft.mp3", 0.5),
    *[("S07", ("cut", i), 0.0, ["whoosh-short.mp3", "whoosh.mp3", "click-soft.mp3"][i % 3], 0.22)
      for i in range(18)],
    ("S09", r"switch", -0.05, "click-soft.mp3", 0.6),
    ("S10", ("end", 0.3), 0.0, "whoosh-short.mp3", 0.3),
    ("S11", r"catalog", 0.1, "typing.mp3", 0.35),
]
# S07's rapid cuts: one backend UI every CUT seconds once the voice ends (mirrors s07.html).
CUT = 0.30


def probe(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def main() -> int:
    cues = json.loads((ROOT / "cues.json").read_text())
    plan, t = [], 0.0
    for sid, sc in cues["scenes"].items():
        voice = sc["duration"]
        dur = round(LEAD + voice + TAIL.get(sid, DEFAULT_TAIL) + EXTRA.get(sid, 0), 3)
        sents = [[round(LEAD + s["start"], 3), round(LEAD + s["end"], 3)] for s in sc["sentences"]]
        words = [{"t": norm(w["t"]), "s": round(LEAD + w["s"], 3), "e": round(LEAD + w["e"], 3)}
                 for w in sc.get("words", [])]
        plan.append({"id": sid.lower(), "sid": sid, "start": round(t, 3), "dur": dur, "voice": voice,
                     "sents": sents, "words": words, "kicker": sc["kicker"]})
        t += dur
    total = round(t, 3)

    for p in plan:
        f = ROOT / "compositions" / f"{p['id']}.html"
        if not f.exists():
            print(f"  (no {f.name} yet)")
            continue
        cue = {"dur": p["dur"], "lead": LEAD, "sents": p["sents"], "w": p["words"]}
        src = f.read_text()
        f.write_text(re.sub(r"/\*CUES\*/.*?/\*END\*/", lambda _m: "/*CUES*/" + json.dumps(cue) + "/*END*/",
                            src, flags=re.S))

    def where(p, w):
        words = p["words"]
        if isinstance(w, (int, float)):
            return w
        if isinstance(w, str):
            hit = next((x for x in words if re.match(w, x["t"])), None)
            return hit["s"] if hit else None
        if w[0] == "end":
            return p["dur"] - w[1]
        if w[0] == "nth":
            hits = [x for x in words if re.match(w[1], x["t"])]
            return hits[w[2]]["s"] if len(hits) > w[2] else None
        if w[0] == "cut":
            return LEAD + p["voice"] + 0.1 + w[1] * CUT
        return None

    hosts, voice, sfx = [], [], []
    for p in plan:
        hosts.append(
            f'      <div id="{p["id"]}" data-composition-id="{p["id"]}" data-composition-src="compositions/{p["id"]}.html"\n'
            f'           class="clip" data-start="{p["start"]}" data-duration="{p["dur"]}" data-track-index="1"\n'
            f'           data-track-kind="graphics" data-width="1920" data-height="1080"></div>')
        voice.append(f'      <audio id="vo-{p["id"]}" class="clip" src="assets/voice/{p["sid"]}.wav" '
                     f'data-start="{round(p["start"] + LEAD, 3)}" data-duration="{p["voice"]}" data-track-index="10" '
                     f'data-volume="1"></audio>')
    by_sid = {p["sid"]: p for p in plan}
    for n, (sid, w, off, file, vol) in enumerate(SFX):
        p = by_sid.get(sid)
        at = where(p, w) if p else None
        if at is None:
            print(f"  sfx {n} ({sid} {w}) has no anchor; skipped")
            continue
        start = round(p["start"] + at + off, 3)
        d = round(probe(ROOT / "assets" / "sfx" / file), 3)
        sfx.append(f'      <audio id="sfx-{n:02d}" class="clip" src="assets/sfx/{file}" data-start="{max(0, start)}" '
                   f'data-duration="{d}" data-track-index="{12 + n % 3}" data-volume="{vol}"></audio>')
    bed = ROOT / "assets" / "bgm" / "bed.wav"
    music = (f'      <audio id="music-bed" class="clip" src="assets/bgm/bed.wav" data-timeline-role="music" data-start="0" '
             f'data-duration="{total}" data-track-index="11" data-volume="{MUSIC_VOL}" '
             f'data-fade-in="0.6" data-fade-out="2.6"></audio>') if bed.exists() else \
        "      <!-- no assets/bgm/bed.wav yet: run tools/gen_media.py (bgm-*) and pick one -->"

    html = (ROOT / "tools" / "index.template.html").read_text()
    html = html.replace("{{TOTAL}}", str(total)).replace("{{HOSTS}}", "\n".join(hosts)) \
        .replace("{{VOICE}}", "\n".join(voice)).replace("{{MUSIC}}", music).replace("{{SFX}}", "\n".join(sfx))
    (ROOT / "index.html").write_text(html)
    (ROOT / "timeline.json").write_text(json.dumps({"total": total, "scenes": [
        {k: v for k, v in p.items() if k != "words"} for p in plan]}, indent=1) + "\n")
    for p in plan:
        print(f"{p['sid']} {p['start']:6.2f} → {p['start'] + p['dur']:6.2f}  ({p['dur']:.2f}s, voice {p['voice']:.2f}s, "
              f"{len(p['words'])} words)")
    print(f"total {total:.2f}s {'OK' if total < LIMIT else 'OVER THE 90 s LIMIT'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
