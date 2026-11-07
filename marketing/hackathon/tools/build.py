"""Lay the film out from the narration: scene slots, word anchors, index.html and the sound.

    python3 marketing/hackathon/tools/build.py

Reads cues.json (tools/synth_voice.py), transcribes each scene's voice once for word timings
(cached in cues.words.json), then:
  - writes index.html: one host per scene, the voice clips, the music bed and every sound effect;
  - rewrites the `/*CUES*/ ... /*END*/` line in each compositions/sNN.html with that scene's
    duration, lead, sentence windows and named anchors, so the picture lands on the spoken word.
Scene files are hand-written; only their CUES line is generated.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEAD = 0.45
TAIL = {"S01": 0.85, "S03": 0.9, "S06": 0.55, "S08": 2.9}
DEFAULT_TAIL = 0.6
MUSIC_VOL_UNDER_VOICE = 0.34  # the MusicGen bed is peak-normalised only (~-20 dB mean)

# Named anchors per scene: (name, regex on a lower-cased, punctuation-free word, which occurrence).
# Whisper hears the synthetic voice imperfectly ("Cygnos" for SigNoz), so patterns are loose.
ANCHORS = {
    "S01": [("born", r"born", 0), ("cattle", r"cattle", 0), ("backwards", r"backward", 0), ("tracks", r"track", 0)],
    "S02": [("cover", r"cover", 0), ("runs", r"runs?", 0), ("answer", r"answer", 0), ("gone", r"gone", 0)],
    "S03": [("hermes", r"hermes", 0), ("otel", r"o?tel|hotel", 0), ("agent", r"agent", 0), ("trace", r"trace", 0)],
    "S04": [("root", r"root", 0), ("model", r"model", 0), ("nests", r"nest", 0), ("tokens", r"tokens?", 0),
            ("outcome", r"outcome", 0)],
    "S05": [("tab", r"tab", 0), ("land", r"land", 0), ("totals", r"totals?", 0), ("logs", r"logs?", 0),
            ("span", r"span", 0)],
    "S06": [("traces", r"traces?", 0), ("metrics", r"metrics?", 0), ("logs", r"logs?", 0), ("otlp", r"otlp|o", 0),
            ("grafana", r"grafana", 0), ("signoz", r"sig|cyg|sign", 0), ("openobserve", r"open", 0),
            ("uptrace", r"up", 0), ("jaeger", r"ya|ye|jae|jay|yei|yay", 0), ("phoenix", r"ph|fe|fi", 0),
            ("langfuse", r"lang", 0), ("twentyseven", r"twenty|27", 0), ("several", r"several", 0)],
    "S07": [("read", r"read", 0), ("tracks", r"tracks?", 0), ("ask", r"ask", 0), ("draws", r"draws?", 0),
            ("chat", r"chat", 0)],
    "S08": [("nothing", r"nothing", 0), ("open", r"open", 0), ("catalog", r"catalog", 0), ("hermes", r"hermes", 0),
            ("follow", r"follow", 0), ("messenger", r"messenger", 0)],
}

# Sound effects: (scene, anchor or seconds, offset s, file, volume). The anchor resolves to the
# scene-local time the picture uses; the effect is placed so its attack lands one frame later.
SFX = [
    *[("S01", f"stamp{i}", 0.033, ["click-soft.mp3", "key-press.mp3"][i % 2], 0.55) for i in range(12)],
    ("S01", "flood", -0.15, "whoosh-cinematic.mp3", 0.5),
    ("S02", "wipe", 0.0, "whoosh.mp3", 0.6),
    ("S03", "bars", 0.0, "typing.mp3", 0.35),
    ("S03", "title", 0.033, "impact-bass-1.mp3", 0.75),
    ("S04", "push", 0.0, "whoosh-short.mp3", 0.45),
    ("S05", "tab1", 0.0, "click-soft.mp3", 0.6),
    ("S05", "tab2", 0.0, "click-soft.mp3", 0.6),
    ("S06", "riser_at", 0.0, "riser.mp3", 0.35),
    *[("S06", f"b{i}", 0.0, ["whoosh-short.mp3", "whoosh.mp3"][i % 2], 0.5) for i in range(7)],
    ("S06", "wall", 0.0, "impact-bass-2.mp3", 0.6),
    ("S07", "push", 0.0, "whoosh-short.mp3", 0.4),
    ("S08", "flood", -0.15, "whoosh-cinematic.mp3", 0.45),
    ("S08", "typing", 0.0, "typing.mp3", 0.45),
    ("S08", "mark", 0.033, "impact-bass-1.mp3", 0.8),
]


def probe(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def words_for(sid: str, wav: Path, cache: dict) -> list[dict]:
    key = f"{sid}:{wav.stat().st_mtime_ns}"
    if cache.get(sid, {}).get("key") == key:
        return cache[sid]["words"]
    subprocess.run(["npx", "hyperframes", "transcribe", str(wav), "--json"], capture_output=True, check=True)
    words = json.loads((wav.parent / "transcript.json").read_text())
    cache[sid] = {"key": key, "words": [{"t": norm(w["text"]), "s": w["start"], "e": w["end"]} for w in words]}
    return cache[sid]["words"]


def anchor(words: list[dict], pattern: str, nth: int, after: float) -> float | None:
    hits = [w for w in words if w["s"] >= after and re.match(pattern, w["t"])]
    return hits[nth]["s"] if len(hits) > nth else None


def events(sid: str, dur: float, lead: float, sents: list, anchors: dict) -> dict:
    """Picture events derived from the voice. Everything is scene-local seconds."""
    a = anchors
    v_end = sents[-1][1]
    ev: dict[str, float] = {}
    if sid == "S01":
        for i in range(12):
            ev[f"stamp{i}"] = 0.35 + i * (min(v_end, dur - 1.0) - 0.6) / 12
        ev["flood"] = dur - 0.5
    elif sid == "S02":
        ev["wipe"] = a.get("answer", sents[-1][0] + 1.2) + 0.15
        ev["gap"] = a.get("gone", v_end - 0.6)
    elif sid == "S03":
        ev["bars"] = 0.15
        ev["title"] = a.get("hermes", lead) + 0.05
        ev["sub"] = a.get("agent", ev["title"] + 1.6) - 0.6
        ev["kicker"] = a.get("trace", sents[-1][0])
    elif sid == "S04":
        ev["rows"] = a.get("model", sents[-1][0])
        ev["push"] = a.get("tokens", sents[-1][0] + 3.0) - 0.45
    elif sid == "S05":
        ev["tab1"] = a.get("totals", lead + 3.5) - 0.2
        ev["tab2"] = a.get("logs", lead + 6.0) - 0.45
        ev["punch1"] = ev["tab1"] + 0.55
        ev["punch2"] = ev["tab2"] + 0.55
    elif sid == "S06":
        ev["sig_t"], ev["sig_m"], ev["sig_l"] = (a.get(k, lead + i) for i, k in enumerate(("traces", "metrics", "logs")))
        names = ["grafana", "signoz", "openobserve", "uptrace", "jaeger", "phoenix", "langfuse"]
        prev = sents[1][0]
        for i, n in enumerate(names):
            t = a.get(n)
            if t is None or t < prev:
                t = prev + 0.9
            ev[f"b{i}"] = t - 0.06
            prev = t
        ev["wall"] = a.get("twentyseven", sents[-1][0]) - 0.1
        ev["riser_at"] = max(0.0, ev["b0"] - 3.0)
    elif sid == "S07":
        ev["clip"] = 0.0
        ev["push"] = a.get("draws", sents[-1][0] + 1.5) - 0.3
    elif sid == "S08":
        ev["flood"] = 0.0
        ev["nothing"] = a.get("nothing", lead)
        ev["typing"] = a.get("open", sents[1][0]) + 0.1
        ev["mark"] = a.get("hermes", sents[2][0]) - 0.1
        ev["follow"] = a.get("follow", ev["mark"] + 1.0)
    return {k: round(v, 3) for k, v in ev.items()}


def main() -> int:
    cues = json.loads((ROOT / "cues.json").read_text())
    wcache_p = ROOT / "cues.words.json"
    wcache = json.loads(wcache_p.read_text()) if wcache_p.exists() else {}

    t = 0.0
    plan = []
    for sid, sc in cues["scenes"].items():
        tail = TAIL.get(sid, DEFAULT_TAIL)
        dur = round(LEAD + sc["duration"] + tail, 3)
        sents = [[round(LEAD + s["start"], 3), round(LEAD + s["end"], 3)] for s in sc["sentences"]]
        words = words_for(sid, ROOT / "assets" / "voice" / f"{sid}.wav", wcache)
        anchors = {}
        for name, pat, nth in ANCHORS.get(sid, []):
            prev = max(anchors.values(), default=-1.0)
            hit = anchor(words, pat, nth, after=prev)
            if hit is not None:
                anchors[name] = round(LEAD + hit, 3)
        ev = events(sid, dur, LEAD, sents, anchors)
        plan.append({"id": sid.lower(), "sid": sid, "start": round(t, 3), "dur": dur, "voice": sc["duration"],
                     "sents": sents, "anchors": anchors, "ev": ev, "kicker": sc["kicker"]})
        t += dur
    total = round(t, 3)
    wcache_p.write_text(json.dumps(wcache) + "\n")

    for p in plan:
        f = ROOT / "compositions" / f"{p['id']}.html"
        if not f.exists():
            print(f"  (no {f.name} yet)")
            continue
        cue = {"dur": p["dur"], "lead": LEAD, "sents": p["sents"], "a": p["anchors"], "ev": p["ev"]}
        src = f.read_text()
        new = re.sub(r"/\*CUES\*/.*?/\*END\*/", "/*CUES*/" + json.dumps(cue) + "/*END*/", src, flags=re.S)
        f.write_text(new)

    hosts, audio = [], []
    for p in plan:
        hosts.append(
            f'      <div id="{p["id"]}" data-composition-id="{p["id"]}" data-composition-src="compositions/{p["id"]}.html"\n'
            f'           class="clip" data-start="{p["start"]}" data-duration="{p["dur"]}" data-track-index="1"\n'
            f'           data-track-kind="graphics" data-width="1920" data-height="1080"></div>')
        audio.append(f'      <audio id="vo-{p["id"]}" class="clip" src="assets/voice/{p["sid"]}.wav" '
                     f'data-start="{round(p["start"] + LEAD, 3)}" data-duration="{p["voice"]}" data-track-index="10" '
                     f'data-volume="1"></audio>')
    sfx = []
    by_sid = {p["sid"]: p for p in plan}
    for n, (sid, ev, off, file, vol) in enumerate(SFX):
        p = by_sid[sid]
        at = p["ev"].get(ev, p["anchors"].get(ev)) if isinstance(ev, str) else ev
        if at is None:
            continue
        start = round(p["start"] + at + off, 3)
        d = round(probe(ROOT / "assets" / "sfx" / file), 3)
        sfx.append(f'      <audio id="sfx-{n:02d}" class="clip" src="assets/sfx/{file}" data-start="{max(0, start)}" '
                   f'data-duration="{d}" data-track-index="{12 + n % 3}" data-volume="{vol}"></audio>')
    bed = ROOT / "assets" / "bgm" / "bed.wav"
    music = (f'      <audio id="music-bed" class="clip" src="assets/bgm/bed.wav" data-timeline-role="music" data-start="0" '
             f'data-duration="{total}" data-track-index="11" data-volume="{MUSIC_VOL_UNDER_VOICE}" '
             f'data-fade-in="0.4" data-fade-out="2.4"></audio>') if bed.exists() else \
        "      <!-- no assets/bgm/bed.wav yet: run tools/gen_music.py or drop a track there -->"

    html = (ROOT / "tools" / "index.template.html").read_text()
    html = html.replace("{{TOTAL}}", str(total)).replace("{{HOSTS}}", "\n".join(hosts)) \
        .replace("{{VOICE}}", "\n".join(audio)).replace("{{MUSIC}}", music).replace("{{SFX}}", "\n".join(sfx))
    (ROOT / "index.html").write_text(html)
    (ROOT / "timeline.json").write_text(json.dumps({"total": total, "scenes": plan}, indent=1) + "\n")
    for p in plan:
        print(f"{p['sid']} {p['start']:6.2f} → {p['start'] + p['dur']:6.2f}  ({p['dur']:.2f}s)  anchors={len(p['anchors'])}"
              f"/{len(ANCHORS.get(p['sid'], []))}")
    print(f"total {total:.2f}s {'OK' if total < 89.9 else 'OVER THE 90 s LIMIT'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
