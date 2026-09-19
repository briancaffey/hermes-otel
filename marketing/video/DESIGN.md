# DESIGN.md — hermes-otel intro video

## Style Prompt

Swiss Pulse variant, tuned for developer-tooling observability. Clinical, precise, grid-locked compositions over near-black. Electric blue as the single load-bearing accent, amber reserved for warnings/outcomes. Numbers and monospace identifiers dominate — this is a video about spans, hooks, and schema, so code-like typography carries meaning. Hard cuts and geometric wipes. Every element snaps to an invisible 12-column grid; nothing floats or drifts.

## Colors

| Hex       | Role                                                   |
| --------- | ------------------------------------------------------ |
| `#0a0a0c` | Canvas — near-black, slightly cool                     |
| `#f5f5f7` | Primary text, rule lines, grid dividers                |
| `#8a8a92` | Secondary text, inactive labels, axis ticks            |
| `#3d8bff` | Electric blue — the single accent (active states, key values, connectors) |
| `#ffb300` | Amber — reserved for outcome/warning (errors, "timed_out" status, highlight flash) |

Do not introduce additional hues. Gradients are allowed only as radial glows around the blue accent (not linear gradients on dark background — H.264 banding).

## Typography

- **Headlines:** `IBM Plex Sans` 700, 96–140px, tight tracking (`-0.02em`)
- **Body / labels:** `IBM Plex Sans` 500, 24–36px
- **Code / identifiers / schema keys:** `IBM Plex Mono` 500, tabular-nums. This is the voice of the video — it speaks for hooks, span names, attribute keys, install commands.
- Numbers always use `font-variant-numeric: tabular-nums` so counters don't jitter.
- Register logic: sans for prose (titles, taglines), mono for every identifier tied to the plugin's schema.

## Motion

- **Ease signature:** `expo.out` for entries, `power4.out` for transforms, `power2.in` only for the final fade. Nothing floats — entries snap and settle.
- **Durations:** entries 0.5–0.7s, counter roll-ups 1.0–1.4s, scene transitions 0.5s.
- **Stagger:** 0.08–0.12s between sibling elements in a list.
- **Transitions between scenes:** hard wipes (horizontal line sweep) or geometric slides — NO crossfades.
- **Offset first animation by 0.15s** so the opening frame reads before anything moves.

## Grid

- 12-column grid, 80px outer padding, 40px gutter at 1920×1080.
- Diagrams (hook→span tree, backend fan-out) align to column edges.
- Scene-content container: `width: 100%; height: 100%; padding: 80px 120px; display: flex; flex-direction: column; gap: 32px; box-sizing: border-box`.

## What NOT to do

1. **No floating/drifting motion.** This is developer tooling, not a brand reel. Entries snap; they don't ease in for 2 seconds.
2. **No linear gradients across dark backgrounds.** Radial glows only — linear gradients produce visible H.264 banding.
3. **No decorative colors.** Blue = active/accent. Amber = outcome/warning. Everything else is grayscale. Don't introduce green, red, or purple.
4. **No sans-serif for code.** Identifiers, span names, hook names, attribute keys all render in JetBrains Mono. Mixing sans into code tokens erodes the technical register.
5. **No crossfades between scenes.** Hard cuts or geometric wipes — crossfades soften the clinical feel.
