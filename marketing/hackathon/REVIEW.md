# REVIEW.md — draft 3, 2026-11-08

## What changed from draft 1/2

- **Story**: the myth is back and leads into *why OpenTelemetry*: the cattle walked backwards
  (S01) → the prints vanish into a cave, agents still cover their tracks (S02) → Hermes at the
  telescope, OpenTelemetry's own mark (S03). Then: one turn = one trace (S04), local SQLite file +
  the dashboard tab (S05), the tab reading any backend (S06), any OTLP backend + async fan-out +
  18 rapid cuts + a 16-backend wall (S07), GenAI + OpenInference (S08), Claude Code metric names
  (S09), Hermes reading its own trace (S10), close (S11).
- **Voice**: Dia 1.6B cloning one designed narrator, cleaned by StudioVoice, ASR-graded, auto-retaken
  and trimmed on club.lan (was Kokoro `bm_george`). Autopilot retook S05, S09 (×2) and S10, S11 (×3).
- **Music**: one ACE-Step track (was a MusicGen placeholder). SFX: clicks and short whooshes only;
  impacts, riser, glitch and the cinematic whoosh are gone.
- **Art**: 11 generated engravings in the Goltzius plate's style, one screen-to-engraving style
  transfer (S04 dissolves from the engraving into the real capture).
- **Backends**: 17 backends' own UIs (was 7), each with its logo; the dashboard tab shown reading 7.

## Measured vs looked at

- `check`: 0 errors. Total 87.45 s (< 89.9 s).
- Render: 1920×1080, 30 fps, H.264 + AAC. Mix −14.5 LUFS integrated, peak −2.5 dBFS (target −16…−14).
- Looked at: snapshots of every scene (3 per scene), every scene changed after review, and 9 frames
  pulled from the rendered MP4.
- Not heard: the voice, the music and the mix were measured, not listened to. Every narration take
  was checked only by transcribing it back (Nemotron ASR, score ≥ 9.0 on all 11). "Hermes OTel"
  transcribes as "Hermes Hotel" (lexicon says "Oh-tel"); it needs a human listen.

## Fixed during review

- S03 title/subtitle sat on the telescope → split layout (type on paper, plate right); subtitle
  wrapped into the OTel chip → one line at 40 px.
- S07 kicker wrapped ("gRPC" on its own line) → shortened; the OTLP anchor regex matched any word
  with an "o" → anchored; queue lanes were empty → continuous flow, slow lane backs up visibly;
  the "27" wall was on screen 0.37 s → ~1.8 s.
- S11 emblem read as a pasted square → round medallion.
- Voice: S03 had 3.85 s of trailing silence (streaming ASR stretched the last word) → fixed in
  club.lan's trimmer (waveform silence pass); voice total 71.0 s → 62.1 s.

## Open / next

- P1 · 3D (TRELLIS-2) and LTX-2.5 clips are in `media.json` but were not generated: the trellis2 pod
  reports "CUDA device busy or unavailable" (needs a pod restart) and `ltx25-server` on a2 is
  inactive. Once up: `python3 tools/gen_media.py --only m01-telescope,m02-caduceus,v01-cattle,v03-telescope,v07-arms`
  and slot them into S01/S03/S07/S11.
- P2 · S06 flips seven backends in ~4.4 s; the names are a montage (`line_unread` kept on purpose).
- P2 · S09 pans the community dashboard past its "No data" panels (Claude Code-only metrics such as
  tool decisions); start lower if it reads as a negative.
- P2 · Plates a04-chart and a06-codex were generated but are unused.
