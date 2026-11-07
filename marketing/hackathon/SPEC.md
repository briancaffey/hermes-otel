# SPEC.md — "The Tracks" (storyline A)

```text
GOAL:    a hackathon judge finishes knowing that hermes-otel turns every Hermes turn into a real
         OpenTelemetry trace they can see in Hermes itself and in the backend they already run, and
         remembers the line "Follow the messenger".
OUTPUT:  HyperFrames, 1920 x 1080, 30 fps, MP4 for X (H.264, AAC).
TIME:    ~84 s (hard cap 89.9 s: "Keep the demo under 90 seconds") = ~2520 frames.
         The voice is the clock: scene slot = 0.4 s lead + voice + tail (cues.json, tools/build.py).
         Music bed 100 bpm → 18 frames a beat; the backend roll cuts every 2 beats (1.2 s).
LOOK:    two worlds, one system.
         PRINT (S01, S08): paper #f2f2f2, ink #0000f2 (hermes-agent site tokens --hermes-color-white /
           --hermes-color-blue). The medium is a 1592 copper engraving (Goltzius, *Mercurius*, CC0 NGA)
           as a duotone plate — every tone is engraved line, no gradients, no glow. Hoofprints are
           stamped ink (solid #0000f2, 4% rotation jitter, seeded).
         AGENT (S02–S07): ground #041c1c (Hermes Teal), ink #ffe6cb, the dashboard's own span colours
           agent #35d29b · llm/api #39bef9 · tool #fbbe25 (sampled from the capture), one accent
           #ffbd38 (Hermes Teal warm glow, solid, never as a glow) for the one word that matters.
           The medium is the real product UI at 1.4–2.2x, framed edge to edge (never a window
           floating in a void).
         TYPE: display "Rules Compressed" Medium, caps, 200–360 px, tracking -0.01em, line-height 0.86;
           captions "Collapse" Bold 54 px sentence case; kickers "JetBrains Mono" Bold 28 px caps (the 28 px floor),
           tracking 0.22em, #0000f2 on paper / #6e6eff (site --brand-lift) on teal; myth line
           "Mondwest" 60 px. Fonts are the Nous design-system files Hermes ships (tools/prep_assets.py).
         CAPTION GRAMMAR (from the official Hermes Desktop film): one kicker + one short line per
           beat, lower left at x=120, baseline y=930; never the spoken sentence word for word.
         DEPTH: none. No shadows, no blur except motion blur on moves. Grain 0.
OBJECTS: hoofprints (cloven, two lobes, drawn as SVG paths); the engraving's caduceus (cropped from
         the plate for the mark); span bars (rounded 3 px, the dashboard's colours); the real
         screens listed per shot; threads (1.5 px #ffe6cb lines, the many-armed-Hermes motif).
MOTION:  enter expo.out 0.5–0.7 s; camera power2.inOut 0.8–1.2 s; exits power3.in, 40% of enter;
         stamps: scale 1.12 → 1 in 4 frames, no fade; word stagger 70 ms; bar draw 0.35 s power3.out,
         60 ms apart; colour flood 0.5 s power2.inOut. Titles land and stay still.
         Motion blur on (data-motion-blur) for whips and camera moves.
SHOTS:
  S01 myth (print)   0–~8 s   frame 0 is a finished poster: plate right 55%, giant "TRACKS" at left
                               in blue caps. 12 hoofprints stamp right→left toward the god while
                               pointing left→right (walked backwards), one per ~0.45 s with a stamp
                               sound; camera pushes 1.0→1.18 toward the winged sandals. Kicker
                               THE MYTH; line "Fifty cattle. No way to follow." Hand-off: the last
                               print's ink floods the frame to teal (colour flood).
  S02 black box      ~8–16 s  real terminal still (span-tree recording): question at top, answer at
                               bottom; the rows between them (the two `find` tool calls) are wiped by
                               a teal bar sweeping across, leaving a hard empty gap. Kicker THE BLACK
                               BOX; line "Question in. Answer out. Nothing in between."
  S03 reveal         ~16–24 s the gap fills with 9 span bars drawing in the dashboard's colours, in
                               waterfall order; the bars slide together into the lockup "HERMES OTEL"
                               (Rules Compressed 340 px, "OTEL" in #ffbd38) over "OpenTelemetry for
                               Hermes Agent" (Collapse 48 px). Kicker NEW PLUGIN.
  S04 the trace      ~24–36 s real dashboard trace (lgtm-detail-hero): establish full width 1.2x,
                               rows reveal top-down on "nests inside it", then push 2.0x onto the
                               header stats (16.98 s · 52.2k tok · tools · outcome) on "tokens,
                               timing…". Caption "14 spans · 16.98 s · 52.2k tokens" (from the shot).
  S05 the dashboard  ~36–47 s one frame that rebuilds: the Hermes dashboard at 1.35x; Live → Metrics
                               → Logs swap under a fixed tab bar with a cream underline sliding to each
                               tab on its word; punch 1.8x onto Tokens by type / Tool duration, then
                               onto a log line next to its trace id. Kicker BUILT INTO HERMES.
  S06 every backend  ~47–64 s threads: from the teal centre, 7 lines shoot to the frame edges (the
                               many-armed Hermes), each pulling in one backend's real UI full-bleed on
                               the beat — Grafana (Tempo trace), SigNoz, OpenObserve, Uptrace, Jaeger,
                               Phoenix, Langfuse — name in Rules Compressed 220 px on a solid teal band
                               left 34%, the shot right 66% pushed onto its waterfall. Signal chips
                               T · M · L per backend from the README table. Then all seven shrink into
                               a 4+3 wall and "27" lands in #ffbd38: "backend types".
  S07 ask the agent  ~64–72 s the real span-tree terminal recording (sped 1.6x) at 1.25x; the tree
                               draws; push to 1.7x on the tree. Kicker ASK THE AGENT; line "It reads
                               its own trace."
  S08 close (print)  ~72–84 s colour flood back to paper. "Nothing leaves your machine." → install
                               command typed in mono (hermes plugins install hermes_otel) → the
                               caduceus cut from the plate rises, HERMES OTEL wipes in beside it,
                               "Follow the messenger." + repo URL + "Hermes Agent Plugin Hackathon ·
                               @NousResearch". Holds 2.6 s.
BANNED:  a dark void with small floating UI; glow, bloom, particles, lens flares; purple/blue
         gradients, glass cards; corner brackets, HUD readouts, frame borders as decoration; three
         cards in a row; a title over a dimmed screenshot; the logo docked in a corner; everything
         fading and sliding up; titles that drift after landing; breathing/pulsing loops; any number
         not read off a capture or the README; the Nous logo or seal (no implied endorsement); hype
         words (unlock, seamless, supercharge, effortless, game-changer, revolutionize).
QA:      frame 0 reads as a poster; every scene's hero ≥ 40% of the frame; UI text ≥ 26 px after
         push; caption never repeats the narration; S06 names land on the spoken name (±3 frames);
         total < 89.9 s; mix −16 to −14 LUFS, true peak ≤ −1 dBTP.
```
