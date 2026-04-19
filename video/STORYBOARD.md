# STORYBOARD.md

Silent, 1920×1080, 30fps. ~28 seconds total. Five scenes, hard geometric wipes between.

Colors: canvas `#0a0a0c`, text `#f5f5f7`, mute `#8a8a92`, accent `#3d8bff`, amber `#ffb300`.
Typography: Inter (sans) for ink, JetBrains Mono for code/identifiers/numbers.

---

## Scene 1 — Problem (0.0 → 4.5s)

**Mood:** clinical tension. Silent system running blind.
**Hero frame:** centered pulsing "BLACK BOX" label in mono, with ambient terminal-like ticks beneath (just decorative rule-lines, no content). Small subtitle "HERMES AGENT · TURN IN PROGRESS" in mute gray above.

**Elements**
- `scene1-label`: "BLACK BOX" in JetBrains Mono Bold, 160px, center, letter-spacing 0.02em
- `scene1-subtitle`: "HERMES AGENT · TURN IN PROGRESS" mute gray, 28px Mono
- `scene1-ticks`: row of 12 vertical bars beneath the label, amber, animated as a waveform-ish rhythm

**Motion**
- Subtitle fades in at 0.2s (opacity only, no y).
- "BLACK BOX" letters fade in in sequence (stagger 0.06s) from 0.5s.
- Ticks pulse continuously (scaleY stagger) for the whole scene.
- No exit animations — hard wipe transition handles it.

---

## Scene 2 — Title (4.5 → 9.0s)

**Mood:** confident resolution.
**Hero frame:** three-line title block, left-aligned on column 2:
```
hermes-otel
————————————
OPEN-TELEMETRY
FOR HERMES AGENT
```

**Elements**
- `scene2-title`: "hermes-otel" JetBrains Mono Bold, 180px, accent blue `#3d8bff`
- `scene2-rule`: horizontal rule 800px wide, 2px, white
- `scene2-tagline`: "OPEN-TELEMETRY FOR HERMES AGENT" Inter 800, 56px, tracking 0.1em
- `scene2-version`: "v0.1 · OTLP/HTTP" bottom-right corner, Mono 24px, mute gray

**Motion**
- Title slides in from x=-120 with opacity 0 → 1 at 0.2s (expo.out, 0.7s).
- Rule expands scaleX 0 → 1 from left at 0.6s (power4.out, 0.5s).
- Tagline fades + slides y=20 → 0 at 0.85s (power3.out, 0.5s).
- Version pair fades in at 1.5s.
- No exits.

---

## Scene 3 — Hooks → Spans tree (9.0 → 16.5s)

**Mood:** the payoff diagram. This is the technical moment.
**Hero frame:** left side — stacked list of 4 hook names; right side — span hierarchy tree. Connector lines (accent blue) drawn from each hook into the matching span node.

**Elements**
- `scene3-heading`: "LIFECYCLE HOOKS → OTEL SPANS" at top, Inter 800, 48px, tracking 0.08em
- `scene3-hooks` (left column, Mono 34px):
  - `on_session_start`
  - `pre_api_request`
  - `pre_tool_use`
  - `on_session_end`
- `scene3-tree` (right column, Mono 34px, nested):
  ```
  session.agent              (GENERAL)
  └─ llm.claude-opus          (LLM)
      └─ api.claude-opus      (LLM)
          └─ tool.Bash        (TOOL)
  ```
- `scene3-connectors`: SVG lines from each hook to its span, accent blue, drawn progressively
- `scene3-kind-badges`: span kinds (GENERAL/LLM/TOOL) rendered as small uppercase labels in amber

**Motion**
- Heading fades in at 0.15s.
- Hook names stagger in from x=-40 at 0.4s (0.1s between, expo.out).
- Tree nodes reveal top-down at 1.2s (stagger 0.25s — feels like the tree is building).
- Connector lines draw via stroke-dashoffset at 1.4s, each matched to its node (0.25s stagger).
- Kind badges pop in with scale 0.8 → 1 after each node lands.
- Scene holds from ~5.5s to ~7.5s fully formed, no exits.

---

## Scene 4 — Backend fan-out (16.5 → 21.5s)

**Mood:** capability showcase.
**Hero frame:** central `hermes-otel` node (small, blue), six backend chips radiating outward in a horizontal row: Phoenix, Langfuse, LangSmith, SigNoz, Jaeger, Tempo. Connector lines animate from center to each chip.

**Elements**
- `scene4-heading`: "ONE PLUGIN · MANY BACKENDS" Inter 800, 48px
- `scene4-center`: circle chip with text "hermes-otel", accent blue border, Mono 22px
- `scene4-chips` (6 rectangles, evenly distributed, each 220×84px, white border 2px):
  - Phoenix · Langfuse · LangSmith · SigNoz · Jaeger · Tempo
  - Each chip shows backend name (Inter 32px) + small kind badge: TRACES or TRACES+METRICS in mono
- `scene4-lines`: SVG connectors from center to each chip
- `scene4-caption`: "OTLP/HTTP · PARALLEL FAN-OUT" bottom, mute gray, Mono 22px

**Motion**
- Heading fade in at 0.15s.
- Center node scale 0 → 1 at 0.4s (back.out(1.5)).
- Lines draw outward from center, stagger 0.08s at 0.7s.
- Chips pop in (scale 0.9 → 1, opacity) staggered in with each line landing.
- Caption fades in at ~2.5s.

---

## Scene 5 — Schema + CTA (21.5 → 28.0s)

**Mood:** technical confidence, close.
**Hero frame:** two-column schema table (gen_ai / OpenInference) with four rows; CTA below.

**Elements**
- `scene5-heading`: "DUAL-CONVENTION ATTRIBUTES" Inter 800, 44px
- `scene5-table`: 4-row table
  | Metric              | gen_ai.*                        | OpenInference              |
  |---------------------|---------------------------------|---------------------------|
  | prompt tokens       | gen_ai.usage.input_tokens       | llm.token_count.prompt    |
  | completion tokens   | gen_ai.usage.output_tokens      | llm.token_count.completion|
  | cache read          | gen_ai.usage.cache_read_…       | llm.token_count.cache_read|
  | content prompt      | gen_ai.content.prompt           | input.value               |
  - Headers Inter 800 26px; rows Mono 24px, tabular-nums
  - Dividing rules mute gray
- `scene5-cta`: large line "hermes plugins install briancaffey/hermes-otel" Mono 38px, in an accent-blue code block with subtle glow
- `scene5-subcta`: "SEE EVERY SPAN." Inter 800, 40px, tracking 0.05em, white

**Motion**
- Heading fade at 0.15s.
- Table rows stagger in from y=20 at 0.5s (0.12s between rows, power3.out).
- CTA block scales in (0.92 → 1) at 2.0s, blue glow pulses twice.
- Final "SEE EVERY SPAN." fades in at 3.2s (opacity only).
- Last 0.8s: entire scene slowly fades to black via composition opacity (the ONLY exit animation in the video, as per rule).

---

## Transitions between scenes

- 1 → 2: hard vertical wipe, white line sweep L→R (0.35s)
- 2 → 3: horizontal black bar slides down over the frame (0.35s)
- 3 → 4: diagonal wipe bottom-left → top-right (0.4s)
- 4 → 5: horizontal wipe R→L (0.35s)

All transitions are CSS-based (clip-path), no WebGL shaders.

## Asset audit

| Asset                | Source            | Status     |
| -------------------- | ----------------- | ---------- |
| Fonts: Inter, JetBrains Mono | google-fonts | embedded by compiler |
| Narration audio      | —                 | skipped    |
| Background music     | —                 | none (silent) |
| Video / imagery      | —                 | none (pure typographic) |
| Icons / logos        | —                 | text-only backend names (no logos to avoid trademark issues) |

Everything is deterministic, no external media — this will render fast.
