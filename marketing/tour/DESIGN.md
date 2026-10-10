# DESIGN.md — hermes-otel tour video

Brand truth for `marketing/tour/`. Inherits the "Swiss Pulse" brief of `marketing/video/DESIGN.md`
(clinical, grid-locked, near-black canvas, one electric-blue accent, amber for outcomes) and adds
what a longer, narrated, screenshot-driven piece needs.

## Concept angle

A flight recorder for an agent: every turn leaves a complete, structured record that any
instrument panel can read. Visual world: telemetry consoles, waterfall timelines, registration
marks, monospace metadata. The screenshots are the evidence; the graphics explain how the
evidence is produced.

## Colors

| Hex       | Role                                                                 |
| --------- | -------------------------------------------------------------------- |
| `#0a0a0c` | Canvas, near-black, slightly cool                                    |
| `#121216` | Panels and screenshot frames                                         |
| `#f5f5f7` | Primary text, rules, grid dividers                                   |
| `#8a8a92` | Secondary text, inactive labels, axis ticks                          |
| `#3d8bff` | Electric blue, the single accent: active states, key values, connectors, glows |
| `#ffb300` | Amber, reserved for outcomes and warnings (errors, highlight flashes) |

Signal colours for the three OTel signals, used only on badges and in the signal map:
traces `#3d8bff`, metrics `#34d399`, logs `#fbbf24`. No other hues. Radial glows only, never a
linear gradient across the dark canvas (H.264 banding).

## Typography (embedded families only)

- **Headlines:** `Montserrat` 900, 88–140px, tracking `-0.02em`. (The intro brief named IBM Plex
  Sans, which the renderer does not embed; Montserrat is the geometric stand-in.)
- **Body / labels:** `Montserrat` 400 and 700, 24–36px.
- **Identifiers, span names, attribute keys, commands, numbers:** `IBM Plex Mono` 400/700 with
  `font-variant-numeric: tabular-nums`. Mono is the voice of the plugin's schema.
- Uppercase labels get `letter-spacing: 0.08em`.

## Layout

- 1920×1080, 30 fps. 12-column grid, 96px outer padding, 32px gutter.
- Every scene has three layers: a background texture (radial glow, ghost grid, oversized ghost
  type), the midground content (screenshot frames, diagrams, code), and foreground accents
  (registration marks, scene index, monospace metadata strip at the bottom).
- Screenshots sit in a framed panel with a 1px `#2a2a30` border and a 12px radius on a `#121216`
  surface; pan/zoom moves inside the frame, the frame itself stays put.
- Split frames by default: evidence left, explanation right (or the reverse), never a centred stack.

## Motion

- Entries `expo.out` 0.5–0.7s; transforms `power4.out`; the final fade `power2.in`.
- Stagger 0.08–0.12s between siblings. Counters roll 1.0–1.4s.
- Transitions: push slide (primary, 60%), vertical push and squeeze as accents. No crossfades.
- Screenshot panels get a slow, deliberate pan or a 1.08× zoom over their dwell time, never both.
- First motion starts at 0.15s so the opening frame reads.

## Narration

Voice: NVIDIA Magpie `Magpie-Multilingual.EN-US.Mia` at 22.05 kHz, synthesised one sentence at a
time (the service ends an utterance at a semicolon or colon, so the script avoids both). Written
at ~150 words a minute, plain sentences, product names spelled for the voice ("Hermes O-Tel",
"O-T-L-P", "L-L-M", "A-P-I", "SigNoz" as "SIG-nahz", "Langfuse" as "LANG-fyooz").

## What not to do

No floating motion, no decorative colours, no sans for code, no crossfades, no linear gradients,
no fabricated numbers: every figure on screen comes from the captured run.
