# REVIEW.md — first cut, 2026-11-04

## Creative calls

- Storyline A, "The Tracks" (see `STORYLINES.md`): the Homeric Hymn's cattle theft as the hook,
  two worlds (engraving print world in the hermes-agent site's `#0000f2` / `#f2f2f2`, Hermes Teal
  product world from the dashboard), the many-armed-Hermes thread motif for the backend fan-out,
  the caduceus cut from a 1592 Goltzius engraving (CC0, NGA) as the mark. Not built: B "one
  continuous take" (endless zoom through one trace) and C "type carries the argument" (beat-cut
  condensed caps, almost no narration).
- Caption grammar taken from the official Hermes Desktop film: tracked mono kicker + one short
  line, never the spoken sentence.
- Fonts are Hermes Agent's own design-system files (Rules Compressed, Collapse, Mondwest), copied
  from a hermes-agent checkout at build time; not committed. No Nous logo or seal anywhere.
- Narration accuracy: "one command to install" was changed to "in the Hermes plugin catalog",
  because a catalog install needs `install` then `enable`; both commands are shown on screen.

## Measured vs looked at

- `check`: ok (0 errors). Total 84.39 s (< 90 s limit). Every cut in S03/S04/S05/S06/S08 is
  anchored to a Whisper word timestamp of the synthesised voice (`cues.words.json`).
- Looked at: snapshots of every scene, every seam (±0.15 s), the review beat and phone sheets.
- Not checked by ear: the voice is Kokoro `bm_george` (placeholder); pronunciation was checked only by
  transcribing it back ("Hermes O-tel" transcribes as "Hermes Hotel"; it should *sound* right but
  needs a human listen). Music is a MusicGen placeholder. The mix was measured, not heard.

## Audio (measured on renders/hermes-otel-hackathon-draft1.mp4, not heard)

- Mix: −16.1 LUFS integrated, true peak −2.7 dBTP, no clipping (target −16 to −14 LUFS, ≤ −1 dBTP).
- Music bed (MusicGen-small placeholder, volume 0.34): residual vs the no-music render is about
  −34 dB mean over 10–50 s and −40 dB over 66–84 s, i.e. 18–24 dB under the voice. A produced
  track should be loudness-normalised (`npx hyperframes normalize-audio`) and the volume re-set.
- SFX: bundled CC0 library; stamps on the hoofprints, whooshes on cuts, a riser into the backend
  roll, impacts on the title and the mark.

## Weakest shots / next changes

- P1 · t=8.3–16.2 s · S02 terminal text is ~20 px; fine at 1080p, small on a phone → a tighter
  final push (1.8x on the question/answer rows only) once the narration is locked.
- P1 · t=46.8–47.2 s · S06 opens on a near-empty teal frame before "Traces" lands → start the signal
  words 0.15 s earlier or carry the S05 log line across.
- P1 · S06 backend roll cuts every 0.6–1.2 s (on the spoken names); chips read only as a pattern
  (`line_unread` kept on purpose: the montage is the point, S06's "27" wall holds).
- P2 · S01 hoofprints are created by script (12 identical nodes) rather than written in HTML; fine
  for render, less editable in Studio.
- P2 · S04 opens on just the root row for ~2 s ("The turn is the root") — deliberate, but a "ROOT"
  label on the row would make it read faster.

## Warnings kept on purpose

- `line_unread` on the S06 signal chips (montage) and S05's dimmed caption words (they light up in turn).
- `container_overflow` / `panel_out_of_canvas` info on camera wrappers (3200 px screenshots inside
  1920 px views — the crop is the intent).
