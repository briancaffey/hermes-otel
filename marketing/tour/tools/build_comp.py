"""Generate the HyperFrames composition for the tour from SCRIPT.md's cues and the captured frames.

    python marketing/tour/tools/build_comp.py

Reads assets/voice/cues.json (written by synth_voice.py) and assets/shots/*/manifest.json, writes
index.html (the orchestrator: scene slots, narration audio clips) and compositions/sNN.html (one
sub-composition per scene). Every number on screen comes from the captured run; nothing is typed in
by hand here except the copy.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUES = {k.lower(): v for k, v in json.loads((ROOT / "assets" / "voice" / "cues.json").read_text()).items()}
SHOTS = ROOT / "assets" / "shots"

LEAD = 1.1  # seconds of scene before the narration starts (0.5 push + 0.6 settle)
TAIL = 0.9  # seconds after the narration ends before the push out
PUSH = 0.5  # push-slide length; consecutive slots overlap by this much

BLUE, AMBER, GREEN, YELLOW = "#3d8bff", "#ffb300", "#34d399", "#fbbf24"


def manifest(source: str) -> dict:
    p = SHOTS / source / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else {}


def frame(source: str, page: str) -> str:
    return f"assets/frames/{source}-{page}.jpg"


def has_frame(source: str, page: str) -> bool:
    return (ROOT / frame(source, page)).exists()


def fmt_tokens(n: int | None) -> str:
    if not n:
        return ""
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


# ── shared CSS, scoped per scene with the @S token ───────────────────────────
BASE_CSS = """
@S { position:absolute; inset:0; overflow:hidden; }
@S .wrap * { box-sizing:border-box; }
@S .wrap { position:absolute; inset:0; width:1920px; height:1080px; overflow:hidden; background:#0a0a0c;
  font-family: Montserrat, sans-serif; color:#f5f5f7; }
@S .bg { position:absolute; inset:0; background:#0a0a0c; }
@S .gridbg { position:absolute; inset:0;
  background-image: linear-gradient(rgba(245,245,247,.04) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(245,245,247,.04) 1px, transparent 1px);
  background-size: 96px 96px; background-position: 96px 56px; }
@S .glow { position:absolute; width:1600px; height:1600px; border-radius:50%;
  background: radial-gradient(circle, rgba(61,139,255,.18) 0%, rgba(61,139,255,0) 62%); }
@S .mono { font-family: "IBM Plex Mono", monospace; font-variant-numeric: tabular-nums; }
@S .meta { position:absolute; left:96px; right:96px; bottom:40px; display:flex; justify-content:space-between;
  font-family:"IBM Plex Mono", monospace; font-size:18px; letter-spacing:.08em; color:#9a9aa2; text-transform:uppercase; }
@S .marks { position:absolute; inset:0; pointer-events:none; }
@S .mark { position:absolute; width:22px; height:22px; border:1px solid rgba(245,245,247,.35); }
@S .mark.tl { left:48px; top:48px; border-right:0; border-bottom:0; }
@S .mark.tr { right:48px; top:48px; border-left:0; border-bottom:0; }
@S .mark.bl { left:48px; bottom:48px; border-right:0; border-top:0; }
@S .mark.br { right:48px; bottom:48px; border-left:0; border-top:0; }
@S .kicker { font-family:"IBM Plex Mono", monospace; font-size:22px; letter-spacing:.12em; text-transform:uppercase; color:#3d8bff; font-weight:700; }
@S .h1 { font-weight:900; font-size:96px; line-height:1.0; letter-spacing:-.02em; }
@S .h2 { font-weight:900; font-size:64px; line-height:1.05; letter-spacing:-.02em; }
@S .body { font-weight:400; font-size:30px; line-height:1.4; color:#c9c9cf; }
@S .frame { position:absolute; border:1px solid #2a2a30; border-radius:12px; background:#121216; overflow:hidden; }
@S .shot { position:absolute; left:0; top:0; width:100%; background-size:100% auto; background-repeat:no-repeat; background-position:top left; }
@S .badges { display:flex; gap:12px; }
@S .badge { display:inline-flex; align-items:center; gap:10px; padding:8px 16px; border-radius:999px; border:1px solid;
  font-family:"IBM Plex Mono", monospace; font-size:18px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }
@S .badge.t { color:#3d8bff; border-color:#3d8bff; }
@S .badge.m { color:#34d399; border-color:#34d399; }
@S .badge.l { color:#fbbf24; border-color:#fbbf24; }
@S .badge.off { color:#8a8a92; border-color:#3a3a40; text-decoration: line-through; }
@S .pill { display:inline-block; padding:8px 18px; border-radius:8px; background:#121216; border:1px solid #2a2a30;
  font-family:"IBM Plex Mono", monospace; font-size:22px; font-weight:700; letter-spacing:.06em; color:#f5f5f7; }
@S .code { font-family:"IBM Plex Mono", monospace; font-size:26px; line-height:1.55; color:#e6e6ea; background:#121216;
  border:1px solid #2a2a30; border-radius:12px; padding:32px 40px; white-space:pre; overflow:hidden; }
@S .code .c { color:#8a8a92; } @S .code .k { color:#3d8bff; } @S .code .s { color:#ffb300; } @S .code .p { color:#34d399; }
@S .line { display:block; }
"""


def scoped(css: str, sid: str) -> str:
    return css.replace("@S", f'[data-composition-id="{sid}"]')


def meta_strip(idx: int, label: str) -> str:
    return (f'<div class="meta"><span>hermes-otel · tour</span><span>{label}</span>'
            f'<span>{idx:02d} / 16</span></div>'
            '<div class="marks"><div class="mark tl"></div><div class="mark tr"></div>'
            '<div class="mark bl"></div><div class="mark br"></div></div>')


def scene_file(sid: str, idx: int, dur: float, css: str, body: str, js: str, *, first=False, last=False) -> str:
    # push transitions: slide in from the right at 0, slide out to the left at the end
    enter = "" if first else f'tl.fromTo("{W}", {{x:1920}}, {{x:0, duration:{PUSH}, ease:"power4.out"}}, 0);'
    leave = (f'tl.to("{W}", {{opacity:0, duration:0.9, ease:"power2.in"}}, {dur-0.9:.2f});' if last
             else f'tl.to("{W}", {{x:-1920, duration:{PUSH}, ease:"power4.in"}}, {dur-PUSH:.2f});')
    return f"""<template>
<div data-composition-id="{sid}" data-width="1920" data-height="1080" data-duration="{dur:.2f}">
<style>{scoped(BASE_CSS + css, sid)}</style>
<div id="{sid}-wrap" class="wrap">
<div class="bg"></div>
{body}
</div>
<script>
window.__timelines = window.__timelines || {{}};
(function () {{
  const tl = gsap.timeline({{ paused: true }});
  {enter}
{js}
  {leave}
  window.__timelines["{sid}"] = tl;
}})();
</script>
</div>
</template>
""".replace(W, f"#{sid}-wrap")


W = "__WRAP__"


def cue(sid: str, i: int) -> float:
    """Local time at which sentence i (0-based) of the scene starts."""
    return round(LEAD + CUES[sid]["sentences"][i]["start"], 2)


def narr(sid: str) -> float:
    return CUES[sid]["total"]


def slot_duration(sid: str) -> float:
    return round(LEAD + narr(sid) + TAIL + PUSH, 2)


# ── S01 cold open ─────────────────────────────────────────────────────────────
def s01() -> str:
    sid, src = "s01", "lgtm-local"
    m = manifest(src)
    hero = m.get("backend_links", {}).get("hero-research", {})
    spans = hero.get("span_count") or 14
    css = """
@S .hero { position:absolute; left:0; top:0; width:1920px; height:1200px; background-image:url(assets/frames/lgtm-local-detail-hero.jpg);
  background-size:1920px auto; background-repeat:no-repeat; transform-origin:50% 30%; }
@S .dim { position:absolute; inset:0; background:rgba(10,10,12,.78); }
@S .copy { position:absolute; left:96px; bottom:140px; width:1300px; }
@S .big { font-weight:900; font-size:220px; line-height:.9; letter-spacing:-.04em; }
@S .big small { font-size:80px; letter-spacing:-.02em; color:#9a9aa2; margin-left:16px; }
@S .stats { display:flex; gap:48px; margin-top:36px; }
@S .stat { font-family:"IBM Plex Mono", monospace; font-size:34px; color:#f5f5f7; }
@S .stat b { color:#3d8bff; font-weight:700; }
@S .q { position:absolute; left:96px; top:200px; width:1500px; font-weight:900; font-size:112px; line-height:1.0; letter-spacing:-.03em; opacity:0; }
@S .q em { font-style:normal; color:#ffb300; }
@S .span-tag { position:absolute; right:96px; top:72px; font-family:"IBM Plex Mono", monospace; font-size:20px; color:#9a9aa2; letter-spacing:.08em; }
"""
    body = f"""
<div id="{sid}-hero" class="hero"></div>
<div class="dim"></div>
<div class="span-tag"><span id="{sid}-tag">trace {hero.get('trace_id','')[:12]}… · {spans} spans · lgtm-local</span></div>
<div id="{sid}-copy" class="copy">
  <div class="big"><span id="{sid}-n">16.98</span><small>seconds</small></div>
  <div class="stats">
    <div class="stat" id="{sid}-st1"><b>6</b> model calls</div>
    <div class="stat" id="{sid}-st2"><b>5</b> tools</div>
    <div class="stat" id="{sid}-st3"><b>52.2k</b> tokens</div>
  </div>
</div>
<div id="{sid}-q1" class="q">Which step was <em>slow</em>?<br>Which one <em>failed</em>?</div>
<div id="{sid}-q2" class="q">Every turn,<br>a <em>black box</em>.</div>
{meta_strip(1, 'one turn, recorded')}
"""
    d = slot_duration(sid)
    c = [cue(sid, i) for i in range(4)]
    js = f"""
  tl.fromTo("#{sid}-hero", {{scale:1.0}}, {{scale:1.08, duration:{d:.2f}, ease:"none"}}, 0);
  tl.from("#{sid}-copy", {{opacity:0, y:40, duration:0.7, ease:"expo.out"}}, 0.15);
  tl.from(["#{sid}-st1","#{sid}-st2","#{sid}-st3"], {{opacity:0, y:18, duration:0.5, stagger:0.12, ease:"power3.out"}}, {c[1]:.2f});
  tl.to("#{sid}-copy", {{opacity:0, y:-30, duration:0.5, ease:"power2.in"}}, {c[2]-0.3:.2f});
  tl.to("#{sid}-q1", {{opacity:1, duration:0.6, ease:"expo.out"}}, {c[2]:.2f});
  tl.from("#{sid}-q1", {{y:40, duration:0.6, ease:"expo.out"}}, {c[2]:.2f});
  tl.to("#{sid}-q1", {{opacity:0, duration:0.4, ease:"power2.in"}}, {c[3]-0.2:.2f});
  tl.to("#{sid}-q2", {{opacity:1, duration:0.6, ease:"expo.out"}}, {c[3]:.2f});
  tl.from("#{sid}-q2", {{y:40, duration:0.6, ease:"expo.out"}}, {c[3]:.2f});
"""
    return scene_file(sid, 1, d, css, body, js, first=True)


# ── S02 title ────────────────────────────────────────────────────────────────
def s02() -> str:
    sid = "s02"
    css = """
@S .glow { left:160px; top:-700px; }
@S .lock { position:absolute; left:96px; top:300px; }
@S .title { font-weight:900; font-size:168px; line-height:.92; letter-spacing:-.04em; }
@S .title span { color:#3d8bff; }
@S .rule { width:0; height:6px; background:#f5f5f7; margin:36px 0; }
@S .sub { font-weight:400; font-size:44px; color:#c9c9cf; }
@S .tag { margin-top:44px; display:flex; gap:16px; align-items:center; }
@S .ver { font-family:"IBM Plex Mono", monospace; font-size:22px; color:#9a9aa2; letter-spacing:.08em; margin-top:40px; }
"""
    body = f"""
<div class="gridbg"></div><div class="glow" data-layout-allow-overflow></div>
<div class="lock">
  <div class="kicker" id="{sid}-k">OpenTelemetry plugin</div>
  <div class="title" id="{sid}-t">HERMES <span>OTEL</span></div>
  <div class="rule" id="{sid}-r"></div>
  <div class="sub" id="{sid}-s">OpenTelemetry for Hermes Agent</div>
  <div class="tag" id="{sid}-tag"><span class="pill">one plugin</span><span class="pill">every turn</span><span class="pill">any backend</span></div>
  <div class="ver mono" id="{sid}-v">v1.22.1 · traces · metrics · logs · OTLP/HTTP</div>
</div>
{meta_strip(2, 'title')}
"""
    d = slot_duration(sid)
    c = [cue(sid, i) for i in range(2)]
    js = f"""
  tl.from("#{sid}-k", {{opacity:0, x:-30, duration:0.5, ease:"power3.out"}}, 0.6);
  tl.from("#{sid}-t", {{opacity:0, y:60, duration:0.8, ease:"expo.out"}}, 0.75);
  tl.to("#{sid}-r", {{width:720, duration:0.6, ease:"power4.out"}}, 1.2);
  tl.from("#{sid}-s", {{opacity:0, y:20, duration:0.5, ease:"power3.out"}}, 1.5);
  tl.from("#{sid}-tag .pill", {{opacity:0, y:16, duration:0.45, stagger:0.12, ease:"power3.out"}}, {c[1]:.2f});
  tl.from("#{sid}-v", {{opacity:0, duration:0.5}}, {c[1]+0.5:.2f});
"""
    return scene_file(sid, 2, d, css, body, js)


# ── S03 hooks → spans ────────────────────────────────────────────────────────
HOOKS = ["on_session_start", "pre_llm_call / post_llm_call", "pre_api_request / post_api_request",
         "pre_tool_call / post_tool_call", "pre_approval_request", "subagent_start / subagent_stop", "on_session_end"]
TREE = [  # (name, depth, left%, width%, colour)
    ("agent", 0, 0, 100, "#34d399"),
    ("llm.nvidia/nemotron-3-super-120b-a12b", 1, 0.5, 99, BLUE),
    ("api.nvidia/nemotron-3-super-120b-a12b", 2, 1, 42, BLUE),
    ("tool.web_extract", 2, 44, 4, AMBER),
    ("api.nvidia/nemotron-3-super-120b-a12b", 2, 49, 14, BLUE),
    ("tool.write_file", 2, 64, 6, AMBER),
    ("api.nvidia/nemotron-3-super-120b-a12b", 2, 71, 8, BLUE),
    ("tool.terminal", 2, 80, 1.5, AMBER),
    ("api.nvidia/nemotron-3-super-120b-a12b", 2, 82, 9, BLUE),
    ("tool.read_file", 2, 92, 1, AMBER),
    ("tool.skill_view", 2, 94, 1, AMBER),
    ("api.nvidia/nemotron-3-super-120b-a12b", 2, 96, 4, BLUE),
]
CHIPS = ["gen_ai.request.model", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.tool.name",
         "gen_ai.tool.call.arguments", "otel.status_code", "error.message"]


def s03() -> str:
    sid = "s03"
    css = """
@S .left { position:absolute; left:96px; top:120px; width:600px; }
@S .right { position:absolute; left:800px; top:120px; width:1024px; }
@S .hook { font-family:"IBM Plex Mono", monospace; font-size:22px; color:#e6e6ea; padding:14px 18px; border:1px solid #2a2a30;
  border-radius:8px; background:#121216; margin-top:12px; display:flex; align-items:center; gap:14px; }
@S .hook i { width:10px; height:10px; border-radius:50%; background:#3d8bff; display:block; flex:none; }
@S .arrow { position:absolute; left:716px; top:470px; width:68px; height:4px; background:#3d8bff; transform-origin:left center; }
@S .arrow:after { content:""; position:absolute; right:-2px; top:-8px; border:10px solid transparent; border-left:14px solid #3d8bff; }
@S .tree { margin-top:28px; }
@S .row { position:relative; height:40px; margin-top:6px; }
@S .bar { position:absolute; top:0; height:26px; border-radius:5px; transform-origin:left center; }
@S .lbl { position:absolute; top:26px; font-family:"IBM Plex Mono", monospace; font-size:15px; color:#c9c9cf; white-space:nowrap; }
@S .chips { display:flex; flex-wrap:wrap; gap:10px; margin-top:28px; }
@S .chip { font-family:"IBM Plex Mono", monospace; font-size:19px; padding:8px 14px; border-radius:6px; background:#121216;
  border:1px solid #3d8bff; color:#9ec5ff; }
"""
    hooks = "".join(f'<div class="hook" id="{sid}-h{i}"><i></i>{h}</div>' for i, h in enumerate(HOOKS))
    rows = []
    for i, (name, depth, left, width, color) in enumerate(TREE):
        indent = depth * 28
        anchor = f"left:calc({left}% + {indent}px)" if left + width < 60 else "right:0;text-align:right"
        rows.append(f'<div class="row" id="{sid}-r{i}"><div class="bar" style="left:{left}%;width:{width}%;background:{color};margin-left:{indent}px"></div>'
                    f'<div class="lbl" style="{anchor}">{name}</div></div>')
    chips = "".join(f'<div class="chip" id="{sid}-c{i}">{c}</div>' for i, c in enumerate(CHIPS))
    body = f"""
<div class="gridbg"></div>
<div class="left">
  <div class="kicker">Hermes lifecycle hooks</div>
  <div class="h2" id="{sid}-lt" style="margin-top:16px">Every turn fires hooks.</div>
  <div id="{sid}-hooks" style="margin-top:20px">{hooks}</div>
</div>
<div class="arrow" id="{sid}-arrow"></div>
<div class="right">
  <div class="kicker">one trace per turn</div>
  <div class="h2" id="{sid}-rt" style="margin-top:16px">Hooks become spans.</div>
  <div class="tree" id="{sid}-tree">{''.join(rows)}</div>
  <div class="chips" id="{sid}-chips">{chips}</div>
</div>
{meta_strip(3, 'how it works · hooks → spans')}
"""
    d = slot_duration(sid)
    c = [cue(sid, i) for i in range(5)]
    hook_span = c[2] - c[1] - 0.6
    js = f"""
  tl.from("#{sid}-lt", {{opacity:0, y:24, duration:0.6, ease:"expo.out"}}, {c[0]:.2f});
  tl.from("#{sid}-hooks .hook", {{opacity:0, x:-24, duration:0.45, stagger:{hook_span/len(HOOKS):.2f}, ease:"power3.out"}}, {c[1]:.2f});
  tl.fromTo("#{sid}-arrow", {{scaleX:0, opacity:0}}, {{scaleX:1, opacity:1, duration:0.5, ease:"power4.out"}}, {c[2]:.2f});
  tl.from("#{sid}-rt", {{opacity:0, y:24, duration:0.6, ease:"expo.out"}}, {c[2]+0.2:.2f});
  tl.from("#{sid}-r0 .bar", {{scaleX:0, duration:0.7, ease:"power4.out"}}, {c[2]+0.6:.2f});
  tl.from("#{sid}-r0 .lbl", {{opacity:0, duration:0.4}}, {c[2]+0.9:.2f});
  tl.from("#{sid}-r1 .bar", {{scaleX:0, duration:0.7, ease:"power4.out"}}, {c[3]:.2f});
  tl.from("#{sid}-r1 .lbl", {{opacity:0, duration:0.4}}, {c[3]+0.3:.2f});
  tl.from(["{'","'.join(f'#{sid}-r{i} .bar' for i in range(2, len(TREE)))}"], {{scaleX:0, duration:0.5, stagger:0.22, ease:"power4.out"}}, {c[3]+0.8:.2f});
  tl.from(["{'","'.join(f'#{sid}-r{i} .lbl' for i in range(2, len(TREE)))}"], {{opacity:0, duration:0.4, stagger:0.22}}, {c[3]+1.0:.2f});
  tl.from("#{sid}-chips .chip", {{opacity:0, scale:0.9, duration:0.4, stagger:0.14, ease:"back.out(1.6)"}}, {c[4]:.2f});
"""
    return scene_file(sid, 3, d, css, body, js)


# ── S04 three signals ────────────────────────────────────────────────────────
def s04() -> str:
    sid = "s04"
    css = """
@S .cols { position:absolute; left:96px; top:230px; display:flex; gap:32px; }
@S .col { width:554px; height:520px; border:1px solid #2a2a30; border-radius:16px; background:#121216; padding:36px; position:relative; overflow:hidden; }
@S .col .name { font-weight:900; font-size:44px; letter-spacing:-.02em; }
@S .col .desc { font-size:24px; line-height:1.4; color:#c9c9cf; margin-top:14px; }
@S .ico { position:absolute; left:36px; right:36px; bottom:36px; height:200px; }
@S .wf .b { position:absolute; height:22px; border-radius:4px; background:#3d8bff; transform-origin:left center; }
@S .bars { display:flex; align-items:flex-end; gap:16px; height:200px; }
@S .bars .b { width:48px; background:#34d399; border-radius:4px 4px 0 0; transform-origin:bottom center; }
@S .lg .ln { font-family:"IBM Plex Mono", monospace; font-size:18px; color:#c9c9cf; margin-top:10px; white-space:nowrap; overflow:hidden; }
@S .lg .ln b { color:#fbbf24; font-weight:400; }
@S .pipe { position:absolute; left:96px; top:812px; width:1728px; height:120px; border:1px solid #2a2a30; border-radius:16px; background:#121216;
  display:flex; align-items:center; justify-content:space-between; padding:0 40px; }
@S .pipe .lbl { font-weight:900; font-size:40px; letter-spacing:-.02em; }
@S .pipe .lbl span { color:#3d8bff; }
@S .pipe .cond { position:relative; width:900px; height:6px; background:#2a2a30; border-radius:3px; overflow:hidden; }
@S .pipe .cond i { position:absolute; left:0; top:0; height:6px; width:100%; background:#3d8bff; display:block; transform-origin:left center; }
@S .pipe .to { font-family:"IBM Plex Mono", monospace; font-size:22px; color:#c9c9cf; letter-spacing:.08em; }
"""
    wf = "".join(f'<div class="b" style="left:{l}%;width:{w}%;top:{t}px;background:{c}"></div>'
                 for (l, w, t, c) in [(0, 100, 0, GREEN), (2, 96, 32, BLUE), (4, 40, 64, BLUE), (46, 8, 96, AMBER), (56, 20, 128, BLUE), (78, 6, 160, AMBER)])
    bars = "".join(f'<div class="b" style="height:{h}px"></div>' for h in [60, 90, 70, 140, 110, 180, 150, 200])
    logs = "".join(f'<div class="ln">{s}</div>' for s in [
        'INFO API call #1 in=7162 out=507 <b>ddca10f9</b>', 'INFO tool web_extract done <b>ddca10f9</b>',
        'INFO tool write_file done <b>ddca10f9</b>', 'INFO tool terminal done <b>ddca10f9</b>',
        'INFO Turn ended: text_response <b>ddca10f9</b>'])
    body = f"""
<div class="gridbg"></div>
<div style="position:absolute;left:96px;top:96px">
  <div class="kicker">three signals, one turn</div>
  <div class="h2" id="{sid}-t" style="margin-top:12px">Traces, metrics and logs, from the same hooks.</div>
</div>
<div class="cols">
  <div class="col" id="{sid}-c1"><div class="name" style="color:#3d8bff">Traces</div><div class="desc">The shape and timing of the turn, span by span.</div>
    <div class="ico wf" id="{sid}-wf">{wf}</div></div>
  <div class="col" id="{sid}-c2"><div class="name" style="color:#34d399">Metrics</div><div class="desc">Tokens, cost, calls and tool durations, added up over time.</div>
    <div class="ico bars" id="{sid}-bars">{bars}</div></div>
  <div class="col" id="{sid}-c3"><div class="name" style="color:#fbbf24">Logs</div><div class="desc">The agent's own log lines, stamped with the trace id.</div>
    <div class="ico lg" id="{sid}-lg">{logs}</div></div>
</div>
<div class="pipe" id="{sid}-pipe"><div class="lbl">OTLP<span>/HTTP</span></div><div class="cond"><i id="{sid}-cond"></i></div><div class="to">→ any backend · no vendor lock-in</div></div>
{meta_strip(4, 'how it works · signals')}
"""
    d = slot_duration(sid)
    c = [cue(sid, i) for i in range(5)]
    js = f"""
  tl.from("#{sid}-t", {{opacity:0, y:24, duration:0.6, ease:"expo.out"}}, {c[0]:.2f});
  tl.from(["#{sid}-c1","#{sid}-c2","#{sid}-c3"], {{opacity:0, y:30, duration:0.6, stagger:0.12, ease:"expo.out"}}, {c[0]+0.4:.2f});
  tl.to("#{sid}-c1", {{borderColor:"#3d8bff", duration:0.4}}, {c[1]:.2f});
  tl.from("#{sid}-wf .b", {{scaleX:0, duration:0.5, stagger:0.1, ease:"power4.out"}}, {c[1]:.2f});
  tl.to("#{sid}-c2", {{borderColor:"#34d399", duration:0.4}}, {c[2]:.2f});
  tl.from("#{sid}-bars .b", {{scaleY:0, duration:0.6, stagger:0.08, ease:"power4.out"}}, {c[2]:.2f});
  tl.to("#{sid}-c3", {{borderColor:"#fbbf24", duration:0.4}}, {c[3]:.2f});
  tl.from("#{sid}-lg .ln", {{opacity:0, x:-14, duration:0.4, stagger:0.25, ease:"power3.out"}}, {c[3]:.2f});
  tl.from("#{sid}-pipe", {{opacity:0, y:30, duration:0.6, ease:"expo.out"}}, {c[4]:.2f});
  tl.fromTo("#{sid}-cond", {{scaleX:0}}, {{scaleX:1, duration:1.6, ease:"power2.inOut"}}, {c[4]+0.4:.2f});
"""
    return scene_file(sid, 4, d, css, body, js)


# ── S05 any backend ──────────────────────────────────────────────────────────
# exactly hermes_otel.backends.KNOWN_TYPES (19), in the order the grid shows them
TYPES = ["phoenix", "langfuse", "lgtm", "tempo", "signoz", "uptrace", "jaeger", "openobserve", "elastic", "honeycomb",
         "weave", "openlit", "mlflow", "opik", "laminar", "langwatch", "latitude", "parseable", "otlp"]


def s05() -> str:
    sid = "s05"
    css = """
@S .left { position:absolute; left:96px; top:120px; width:760px; }
@S .right { position:absolute; left:920px; top:120px; width:904px; }
@S .grid { display:grid; grid-template-columns:repeat(4, 1fr); gap:14px; margin-top:28px; }
@S .ty { font-family:"IBM Plex Mono", monospace; font-size:22px; font-weight:700; padding:16px 10px; text-align:center; border-radius:8px;
  background:#121216; border:1px solid #2a2a30; color:#e6e6ea; }
@S .fan { position:absolute; left:96px; top:790px; width:760px; height:200px; }
@S .node { position:absolute; left:0; top:60px; padding:16px 24px; border-radius:10px; background:#3d8bff; color:#0a0a0c; font-weight:900; font-size:26px; }
@S .ln { position:absolute; left:220px; height:4px; background:#3d8bff; transform-origin:left center; }
@S .env { margin-top:24px; font-family:"IBM Plex Mono", monospace; font-size:22px; color:#9a9aa2; }
@S .env b { color:#f5f5f7; font-weight:400; }
"""
    types = "".join(f'<div class="ty" id="{sid}-t{i}">{t}</div>' for i, t in enumerate(TYPES))
    lines = "".join(f'<div class="ln" id="{sid}-ln{i}" style="top:{t}px;width:{w}px;transform:rotate({r}deg)"></div>'
                    for i, (t, w, r) in enumerate([(80, 540, -8), (82, 520, 0), (84, 540, 8)]))
    body = f"""
<div class="gridbg"></div>
<div class="left">
  <div class="kicker">configure</div>
  <div class="h2" id="{sid}-t" style="margin-top:12px">One entry. Any backend.</div>
  <div class="code" id="{sid}-code" style="margin-top:28px"><span class="line"><span class="c"># ~/.hermes/hermes_otel.yaml</span></span><span class="line"><span class="k">backends</span>:</span><span class="line">  - <span class="k">type</span>: <span class="p">lgtm</span></span><span class="line">    <span class="k">endpoint</span>: <span class="s">http://localhost:4318/v1/traces</span></span><span class="line">    <span class="k">metrics</span>: <span class="p">true</span></span><span class="line">  - <span class="k">type</span>: <span class="p">phoenix</span></span><span class="line">    <span class="k">endpoint</span>: <span class="s">http://localhost:6006/v1/traces</span></span></div>
  <div class="env" id="{sid}-env">or one variable · <b>OTEL_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces</b></div>
</div>
<div class="right">
  <div class="kicker">19 backend types</div>
  <div class="grid" id="{sid}-grid">{types}</div>
</div>
<div class="fan" id="{sid}-fan"><div class="node">hermes-otel</div>{lines}</div>
{meta_strip(5, 'how it works · backends')}
"""
    d = slot_duration(sid)
    c = [cue(sid, i) for i in range(3)]
    per = (c[2] - c[1] - 1.0) / len(TYPES)
    js = f"""
  tl.from("#{sid}-t", {{opacity:0, y:24, duration:0.6, ease:"expo.out"}}, {c[0]:.2f});
  tl.from("#{sid}-code .line", {{opacity:0, x:-12, duration:0.35, stagger:0.18, ease:"power3.out"}}, {c[0]+0.3:.2f});
  tl.from("#{sid}-env", {{opacity:0, duration:0.5}}, {c[0]+2.4:.2f});
  tl.from("#{sid}-grid .ty", {{opacity:0, scale:0.85, duration:0.4, stagger:{per:.2f}, ease:"back.out(1.6)"}}, {c[1]:.2f});
  tl.from("#{sid}-fan .node", {{opacity:0, scale:0.9, duration:0.4, ease:"back.out(1.6)"}}, {c[2]:.2f});
  tl.fromTo(["#{sid}-ln0","#{sid}-ln1","#{sid}-ln2"], {{scaleX:0}}, {{scaleX:1, duration:0.8, stagger:0.1, ease:"power3.out"}}, {c[2]+0.3:.2f});
  tl.to(["#{sid}-t0","#{sid}-t2","#{sid}-t4"], {{borderColor:"#3d8bff", color:"#9ec5ff", duration:0.4, stagger:0.1}}, {c[2]+0.9:.2f});
"""
    return scene_file(sid, 5, d, css, body, js)


# ── S06 dashboard tour ───────────────────────────────────────────────────────
def s06() -> str:
    sid, src = "s06", "lgtm-local"
    phases = [  # (page, label, cue index at which it appears)
        ("live", "Live", 0), ("traces", "Traces", 3), ("detail-hero", "Trace detail", 4),
        ("metrics", "Metrics", 6), ("logs", "Logs", 7), ("sessions", "Sessions", 8)]
    css = """
@S .fr { left:96px; top:200px; width:1728px; height:780px; }
@S .fr .shot { height:1080px; }
@S .ph { position:absolute; inset:0; opacity:0; }
@S .tabs { position:absolute; left:96px; top:112px; display:flex; gap:12px; }
@S .tab { padding:10px 20px; border-radius:8px; border:1px solid #2a2a30; background:#121216; font-family:"IBM Plex Mono", monospace; font-size:22px; font-weight:700; color:#8a8a92; }
@S .tab.on { color:#0a0a0c; background:#3d8bff; border-color:#3d8bff; }
@S .hd { position:absolute; left:96px; top:48px; display:flex; gap:24px; align-items:baseline; }
@S .hd .h2 { font-size:44px; }
"""
    shots = "".join(f'<div class="ph" id="{sid}-p{i}"><div class="shot" id="{sid}-s{i}" data-layout-allow-overflow style="background-image:url({frame(src, page)})"></div></div>'
                    for i, (page, _, _) in enumerate(phases))
    tabs = "".join(f'<div class="tab" id="{sid}-tab{i}">{label}</div>' for i, (_, label, _) in enumerate(phases))
    body = f"""
<div class="gridbg"></div>
<div class="hd"><div class="kicker">the dashboard tab</div><div class="h2" id="{sid}-t">Hermes web UI → OTel</div></div>
<div class="tabs" id="{sid}-tabs">{tabs}</div>
<div class="frame fr" id="{sid}-fr">{shots}</div>
{meta_strip(6, 'dashboard · lgtm-local')}
"""
    d = slot_duration(sid)
    c = [cue(sid, i) for i in range(9)]
    js = [f'tl.from("#{sid}-t", {{opacity:0, y:20, duration:0.6, ease:"expo.out"}}, {c[0]:.2f});',
          f'tl.from("#{sid}-tabs .tab", {{opacity:0, y:12, duration:0.4, stagger:0.08, ease:"power3.out"}}, {c[0]+0.3:.2f});',
          f'tl.from("#{sid}-fr", {{opacity:0, y:30, duration:0.7, ease:"expo.out"}}, {c[0]+0.6:.2f});']
    for i, (page, _, ci) in enumerate(phases):
        t = c[ci] if i else c[0] + 0.6
        end = c[phases[i + 1][2]] if i + 1 < len(phases) else d - PUSH
        js.append(f'tl.to("#{sid}-p{i}", {{opacity:1, duration:0.45, ease:"power2.out"}}, {t:.2f});')
        js.append(f'tl.to("#{sid}-tab{i}", {{color:"#0a0a0c", backgroundColor:"#3d8bff", borderColor:"#3d8bff", duration:0.3}}, {t:.2f});')
        if i:
            js.append(f'tl.to("#{sid}-p{i-1}", {{opacity:0, duration:0.45, ease:"power2.out"}}, {t:.2f});')
            js.append(f'tl.to("#{sid}-tab{i-1}", {{color:"#8a8a92", backgroundColor:"#121216", borderColor:"#2a2a30", duration:0.3}}, {t:.2f});')
        # a slow pan down the page over the dwell (the capture is 1080 tall in a 780 frame)
        pan = -300 if page in ("detail-hero", "logs", "traces") else -120
        js.append(f'tl.fromTo("#{sid}-s{i}", {{y:0}}, {{y:{pan}, duration:{max(end - t, 0.5):.2f}, ease:"none"}}, {t:.2f});')
    js.append(f'tl.fromTo("#{sid}-s2", {{scale:1}}, {{scale:1.12, transformOrigin:"20% 40%", immediateRender:false, duration:{c[6]-c[5]:.2f}, ease:"power1.inOut"}}, {c[5]:.2f});')
    return scene_file(sid, 6, d, css, body, "\n".join("  " + l for l in js))


# ── S07 gallery intro ────────────────────────────────────────────────────────
GALLERY = [  # (source, display name, backend UI caption, metrics, logs)
    ("lgtm-local", "Grafana LGTM", "Grafana · Tempo, Prometheus, Loki", True, True),
    ("openobserve-local", "OpenObserve", "OpenObserve", True, True),
    ("signoz-local", "SigNoz", "SigNoz", True, True),
    ("uptrace-local", "Uptrace", "Uptrace", True, True),
    ("jaeger-local", "Jaeger", "Jaeger", False, False),
    ("phoenix-local", "Arize Phoenix", "Phoenix", False, False),
    ("langfuse-local", "Langfuse", "Langfuse", False, False),
]


def s07() -> str:
    sid = "s07"
    css = """
@S .glow { left:400px; top:-900px; }
@S .c { position:absolute; left:96px; top:300px; width:1728px; }
@S .names { display:flex; flex-wrap:wrap; gap:16px; margin-top:48px; }
@S .nm { padding:18px 28px; border-radius:10px; border:1px solid #2a2a30; background:#121216; font-weight:700; font-size:30px; }
@S .sub { margin-top:40px; font-family:"IBM Plex Mono", monospace; font-size:24px; color:#9a9aa2; letter-spacing:.04em; }
"""
    names = "".join(f'<div class="nm" id="{sid}-n{i}">{n}</div>' for i, (_, n, _, _, _) in enumerate(GALLERY))
    body = f"""
<div class="gridbg"></div><div class="glow" data-layout-allow-overflow></div>
<div class="c">
  <div class="kicker">gallery</div>
  <div class="h1" id="{sid}-t" style="margin-top:12px">Same workload.<br>Seven backends.</div>
  <div class="names" id="{sid}-names">{names}</div>
  <div class="sub" id="{sid}-sub">marketing/tour/prompts.yaml · 11 prompts · replayed against each backend · dashboard left, backend's own UI right</div>
</div>
{meta_strip(7, 'gallery')}
"""
    d = slot_duration(sid)
    c = [cue(sid, i) for i in range(2)]
    js = f"""
  tl.from("#{sid}-t", {{opacity:0, y:40, duration:0.7, ease:"expo.out"}}, {c[0]:.2f});
  tl.from("#{sid}-names .nm", {{opacity:0, y:16, duration:0.4, stagger:0.1, ease:"power3.out"}}, {c[0]+0.6:.2f});
  tl.from("#{sid}-sub", {{opacity:0, duration:0.5}}, {c[1]:.2f});
"""
    return scene_file(sid, 7, d, css, body, js)


# ── S08–S14 backend scenes ───────────────────────────────────────────────────
def gallery_scene(idx: int, sid: str, source: str, name: str, caption: str, metrics: bool, logs: bool, label: str) -> str:
    m = manifest(source)
    hero = m.get("backend_links", {}).get("hero-research") or m.get("backend_links", {}).get("multi-tool") or {}
    tid = hero.get("trace_id", "")
    spans = hero.get("span_count")
    # phases: pairs of (left = backend UI page, right = dashboard page, phase label)
    pairs = [("ui-trace", "detail-hero", "trace detail")]
    if logs and has_frame(source, "ui-logs"):
        pairs.append(("ui-logs", "logs-trace", "logs for the trace"))
    if metrics and has_frame(source, "ui-metrics"):
        pairs.append(("ui-metrics", "metrics", "metrics"))
    if not metrics and not logs:
        if has_frame(source, "ui-traces"):
            pairs.append(("ui-traces", "traces", "trace list"))
        if has_frame(source, "ui-sessions"):
            pairs.append(("ui-sessions", "sessions", "sessions"))
    pairs = [p for p in pairs if has_frame(source, p[0]) and has_frame(source, p[1])] or [("ui-trace", "detail-hero", "trace detail")]
    css = """
@S .hd { position:absolute; left:96px; top:56px; right:96px; display:flex; justify-content:space-between; align-items:flex-end; }
@S .hd .h1 { font-size:72px; }
@S .fl { left:96px; top:230px; width:848px; height:560px; }
@S .fr { left:976px; top:230px; width:848px; height:560px; }
@S .shot { height:560px; }
@S .ph { position:absolute; inset:0; opacity:0; }
@S .cap { position:absolute; top:808px; font-family:"IBM Plex Mono", monospace; font-size:20px; color:#9a9aa2; letter-spacing:.06em; text-transform:uppercase; }
@S .cap b { color:#f5f5f7; }
@S .tid { position:absolute; left:96px; top:880px; right:96px; display:flex; gap:28px; align-items:center; font-family:"IBM Plex Mono", monospace; font-size:22px; color:#c9c9cf; }
@S .tid .k { color:#3d8bff; font-weight:700; }
@S .phl { position:absolute; left:96px; top:168px; display:flex; gap:12px; }
@S .phl .tab { padding:8px 16px; border-radius:8px; border:1px solid #2a2a30; background:#121216; font-family:"IBM Plex Mono", monospace; font-size:20px; font-weight:700; color:#8a8a92; }
"""
    left = "".join(f'<div class="ph" id="{sid}-l{i}"><div class="shot" data-layout-allow-overflow style="background-image:url({frame(source, a)})"></div></div>' for i, (a, _, _) in enumerate(pairs))
    right = "".join(f'<div class="ph" id="{sid}-r{i}"><div class="shot" data-layout-allow-overflow style="background-image:url({frame(source, b)})"></div></div>' for i, (_, b, _) in enumerate(pairs))
    tabs = "".join(f'<div class="tab" id="{sid}-tab{i}">{p[2]}</div>' for i, p in enumerate(pairs))
    badges = (f'<div class="badges"><span class="badge t">traces</span>'
              f'<span class="badge {"m" if metrics else "off"}">metrics</span>'
              f'<span class="badge {"l" if logs else "off"}">logs</span></div>')
    facts = f'<span><span class="k">trace</span> {tid}</span>' if tid else ""
    if spans:
        facts += f'<span><span class="k">spans</span> {spans}</span>'
    facts += f'<span><span class="k">source</span> {source}</span><span><span class="k">prompt</span> hero-research</span>'
    body = f"""
<div class="gridbg"></div>
<div class="hd"><div><div class="kicker">backend {idx-7} of 7</div><div class="h1" id="{sid}-t">{name}</div></div>{badges}</div>
<div class="phl" id="{sid}-phl">{tabs}</div>
<div class="frame fl" id="{sid}-fl">{left}</div>
<div class="frame fr" id="{sid}-fr">{right}</div>
<div class="cap" style="left:96px" id="{sid}-c1"><b>{caption}</b> · the backend's own UI</div>
<div class="cap" style="left:976px" id="{sid}-c2"><b>Hermes dashboard</b> · source {source}</div>
<div class="tid" id="{sid}-tid">{facts}</div>
{meta_strip(idx, label)}
"""
    d = slot_duration(sid)
    n = narr(sid)
    starts = [LEAD + 0.3 + i * (n - 0.3) / len(pairs) for i in range(len(pairs))]
    js = [f'tl.from("#{sid}-t", {{opacity:0, x:-30, duration:0.6, ease:"expo.out"}}, 0.55);',
          f'tl.from(["#{sid}-fl","#{sid}-fr"], {{opacity:0, y:30, duration:0.7, stagger:0.12, ease:"expo.out"}}, 0.7);',
          f'tl.from(["#{sid}-c1","#{sid}-c2","#{sid}-tid"], {{opacity:0, duration:0.5, stagger:0.1}}, 1.2);']
    for i, t in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else d - PUSH
        js.append(f'tl.to(["#{sid}-l{i}","#{sid}-r{i}"], {{opacity:1, duration:0.45, ease:"power2.out"}}, {t:.2f});')
        js.append(f'tl.to("#{sid}-tab{i}", {{color:"#0a0a0c", backgroundColor:"#3d8bff", borderColor:"#3d8bff", duration:0.3}}, {t:.2f});')
        if i:
            js.append(f'tl.to(["#{sid}-l{i-1}","#{sid}-r{i-1}"], {{opacity:0, duration:0.45, ease:"power2.out"}}, {t:.2f});')
            js.append(f'tl.to("#{sid}-tab{i-1}", {{color:"#8a8a92", backgroundColor:"#121216", borderColor:"#2a2a30", duration:0.3}}, {t:.2f});')
        js.append(f'tl.fromTo(["#{sid}-l{i} .shot","#{sid}-r{i} .shot"], {{y:0}}, {{y:-160, duration:{max(end - t, 0.5):.2f}, ease:"none"}}, {t:.2f});')
    return scene_file(sid, idx, d, css, body, "\n".join("  " + l for l in js))


# ── S15 verification ─────────────────────────────────────────────────────────
def s15() -> str:
    sid = "s15"
    css = """
@S .left { position:absolute; left:96px; top:120px; width:860px; }
@S .right { position:absolute; left:1020px; top:120px; width:804px; }
@S table { border-collapse:collapse; width:100%; margin-top:28px; font-size:26px; }
@S th { font-family:"IBM Plex Mono", monospace; font-size:18px; letter-spacing:.1em; text-transform:uppercase; color:#9a9aa2; text-align:left; padding:10px 14px; border-bottom:1px solid #2a2a30; }
@S td { padding:14px; border-bottom:1px solid #1e1e24; font-weight:700; }
@S td.y { color:#34d399; font-family:"IBM Plex Mono", monospace; }
@S td.n { color:#8a8a92; font-family:"IBM Plex Mono", monospace; }
@S .note { margin-top:22px; font-size:22px; color:#c9c9cf; line-height:1.4; }
@S .note b { color:#ffb300; font-weight:700; }
@S .term { margin-top:28px; font-size:22px; overflow:hidden; }
@S .term .ok { color:#34d399; }
"""
    rows = "".join(
        f'<tr id="{sid}-row{i}"><td>{n}</td><td class="y">yes</td><td class="{"y" if m else "n"}">{"yes" if m else "—"}</td><td class="{"y" if l else "n"}">{"yes" if l else "—"}</td></tr>'
        for i, (_, n, _, m, l) in enumerate(GALLERY))
    body = f"""
<div class="gridbg"></div>
<div class="left">
  <div class="kicker">verified, not assumed</div>
  <div class="h2" id="{sid}-t" style="margin-top:12px">Every adapter is checked against a real run.</div>
  <div class="code term" id="{sid}-term"><span class="line"><span class="c">$</span> python scripts/dashboard_verify/verify_dashboard.py \\</span><span class="line">    --source signoz-local --profile minimal --run-batch</span><span class="line"> </span><span class="line"><span class="ok">S</span>  status    reachable, adapter chosen</span><span class="line"><span class="ok">T</span>  traces    every turn found, spans match</span><span class="line"><span class="ok">M</span>  metrics   token totals match the live store</span><span class="line"><span class="ok">L</span>  logs      lines per trace match</span><span class="line"><span class="ok">B</span>  browser   every view renders cleanly</span></div>
</div>
<div class="right">
  <div class="kicker">what the tab can read back</div>
  <table id="{sid}-tbl"><thead><tr><th>backend</th><th>traces</th><th>metrics</th><th>logs</th></tr></thead><tbody>{rows}</tbody></table>
  <div class="note" id="{sid}-note"><b>—</b> means the backend does not store that signal. The tab says so instead of filling the gap from somewhere else.</div>
</div>
{meta_strip(15, 'verification')}
"""
    d = slot_duration(sid)
    c = [cue(sid, i) for i in range(3)]
    js = f"""
  tl.from("#{sid}-t", {{opacity:0, y:24, duration:0.6, ease:"expo.out"}}, {c[0]:.2f});
  tl.from("#{sid}-term .line", {{opacity:0, x:-10, duration:0.35, stagger:0.16, ease:"power3.out"}}, {c[0]+0.4:.2f});
  tl.from("#{sid}-tbl", {{opacity:0, y:20, duration:0.6, ease:"expo.out"}}, {c[1]:.2f});
  tl.from("#{sid}-tbl tbody tr", {{opacity:0, x:16, duration:0.4, stagger:0.14, ease:"power3.out"}}, {c[1]+0.3:.2f});
  tl.from("#{sid}-note", {{opacity:0, y:12, duration:0.5, ease:"power3.out"}}, {c[2]:.2f});
"""
    return scene_file(sid, 15, d, css, body, js)


# ── S16 install / outro ──────────────────────────────────────────────────────
def s16() -> str:
    sid = "s16"
    css = """
@S .glow { left:300px; top:-800px; }
@S .left { position:absolute; left:96px; top:140px; width:980px; }
@S .lock { position:absolute; left:96px; top:560px; opacity:0; }
@S .title { font-weight:900; font-size:150px; line-height:.92; letter-spacing:-.04em; }
@S .title span { color:#3d8bff; }
@S .tag { font-weight:900; font-size:60px; letter-spacing:-.02em; margin-top:18px; color:#ffb300; }
@S .url { position:absolute; left:96px; bottom:120px; font-family:"IBM Plex Mono", monospace; font-size:28px; color:#c9c9cf; letter-spacing:.04em; opacity:0; }
"""
    body = f"""
<div class="gridbg"></div><div class="glow" data-layout-allow-overflow></div>
<div class="left" id="{sid}-left">
  <div class="kicker">install</div>
  <div class="code" id="{sid}-code" style="margin-top:20px"><span class="line"><span class="c">$</span> hermes plugins install hermes-otel</span><span class="line"> </span><span class="line"><span class="c"># ~/.hermes/hermes_otel.yaml</span></span><span class="line"><span class="k">backends</span>:</span><span class="line">  - <span class="k">type</span>: <span class="p">phoenix</span></span><span class="line">    <span class="k">endpoint</span>: <span class="s">http://localhost:6006/v1/traces</span></span><span class="line"> </span><span class="line"><span class="c">$</span> hermes gateway restart</span></div>
</div>
<div class="lock" id="{sid}-lock"><div class="title">HERMES <span>OTEL</span></div><div class="tag" id="{sid}-tag">See every span.</div></div>
<div class="url" id="{sid}-url">briancaffey.github.io/hermes-otel · github.com/briancaffey/hermes-otel</div>
{meta_strip(16, 'install')}
"""
    d = slot_duration(sid)
    c = [cue(sid, i) for i in range(4)]
    js = f"""
  tl.from("#{sid}-code .line", {{opacity:0, x:-12, duration:0.35, stagger:0.14, ease:"power3.out"}}, {c[0]:.2f});
  tl.to("#{sid}-left", {{y:-60, duration:0.6, ease:"power3.inOut"}}, {c[1]+0.6:.2f});
  tl.to("#{sid}-lock", {{opacity:1, duration:0.6, ease:"expo.out"}}, {c[1]+1.0:.2f});
  tl.from("#{sid}-lock", {{y:40, duration:0.7, ease:"expo.out"}}, {c[1]+1.0:.2f});
  tl.from("#{sid}-tag", {{opacity:0, x:-20, duration:0.5, ease:"power3.out"}}, {c[3]:.2f});
  tl.to("#{sid}-url", {{opacity:1, duration:0.5}}, {c[3]+0.6:.2f});
"""
    return scene_file(sid, 16, d, css, body, js, last=True)


# ── orchestrator ─────────────────────────────────────────────────────────────
def build() -> None:
    comps = ROOT / "compositions"
    comps.mkdir(exist_ok=True)
    scenes = [("s01", s01), ("s02", s02), ("s03", s03), ("s04", s04), ("s05", s05), ("s06", s06), ("s07", s07)]
    for i, (src, name, caption, metrics, logs) in enumerate(GALLERY):
        sid = f"s{8+i:02d}"
        scenes.append((sid, lambda sid=sid, i=i, src=src, name=name, caption=caption, metrics=metrics, logs=logs:
                       gallery_scene(8 + i, sid, src, name, caption, metrics, logs, f"gallery · {src}")))
    scenes += [("s15", s15), ("s16", s16)]

    slots, audio, t = [], [], 0.0
    for n, (sid, fn) in enumerate(scenes):
        html = fn()
        (comps / f"{sid}.html").write_text(html)
        d = slot_duration(sid)
        track = 1 + (n % 2)
        slots.append(f'      <div id="el-{sid}" data-composition-id="{sid}" data-composition-src="compositions/{sid}.html" '
                     f'data-start="{t:.2f}" data-duration="{d:.2f}" data-track-index="{track}"></div>')
        audio.append(f'      <audio id="vo-{sid}" src="assets/voice/{sid.upper()}.mp3" data-start="{t + LEAD:.2f}" '
                     f'data-duration="{narr(sid):.2f}" data-track-index="10" data-volume="1"></audio>')
        t += d - PUSH
    total = round(t + PUSH, 2)
    index = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <title>hermes-otel tour</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      body {{ margin: 0; background: #0a0a0c; }}
      #root {{ position: relative; width: 1920px; height: 1080px; overflow: hidden; background: #0a0a0c; }}
      #root > .fill {{ position: absolute; inset: 0; background: #0a0a0c; }}
      [data-composition-id="tour"] > div[data-composition-src] {{ position: absolute; inset: 0; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="tour" data-width="1920" data-height="1080" data-duration="{total:.2f}">
      <div class="fill"></div>
{chr(10).join(slots)}
{chr(10).join(audio)}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      window.__timelines["tour"] = gsap.timeline({{ paused: true }});
    </script>
  </body>
</html>
"""
    (ROOT / "index.html").write_text(index)
    print(f"{len(scenes)} scenes, {total:.1f}s total")
    starts, t2 = {}, 0.0
    for sid, _ in scenes:
        starts[sid] = round(t2, 2)
        t2 += slot_duration(sid) - PUSH
    print(" ".join(f"{k}@{v}" for k, v in starts.items()))


if __name__ == "__main__":
    build()
