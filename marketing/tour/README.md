# hermes-otel tour video

A narrated, screenshot-driven tour of the plugin: what it records, how hooks become spans, the three
signals, the dashboard tab, and the same workload read back from seven backends. Built with
[HyperFrames](https://github.com/heygen-com/hyperframes); narrated with NVIDIA Magpie TTS.

```
marketing/tour/
  DESIGN.md          brand brief for this piece
  SCRIPT.md          locked narration, one sentence per line
  STORYBOARD.md      scene list and transitions
  prompts.yaml       the workload replayed against every backend (turn_batch format)
  capture/           Playwright capture of the Hermes dashboard and of each backend's own UI
  tools/             synth_voice.py (TTS → assets/voice), prep_frames.py, build_comp.py
  assets/shots/      raw 3200x2000 captures per source (+ manifest.json with the trace ids) — committed
  assets/frames/     1920-wide JPEGs the composition uses — generated, not in git
  assets/voice/      per-scene narration MP3s (not in git), cues.json + durations.json (in git)
  index.html         orchestrator (generated)
  compositions/      one sub-composition per scene (generated)
  renders/           output MP4s — not in git
```

Only sources are committed: the script, the capture and build tools, the raw screenshots and the
cue timings. After a fresh clone, steps 2 and 3 below regenerate the narration (needs the Magpie
TTS service; unchanged sentences are not re-synthesised when the cache exists) and the frames
before the composition can be rendered.

## Rebuild

```bash
# 1. capture one backend at a time (stack up, configs pointed at it, workload run)
uv run --extra dev python scripts/turn_batch/run_batch.py --prompts marketing/tour/prompts.yaml --profile minimal --token V261010-<name>
uv run --with playwright python marketing/tour/capture/dashboard.py --source <name>-local --token V261010-<name>
uv run --with playwright python marketing/tour/capture/backend.py --source <name>-local

# 2. narration (sentence by sentence; cached per sentence)
python3 marketing/tour/tools/synth_voice.py --url https://magpie.lan

# 3. frames + composition
uv run --with pillow python marketing/tour/tools/prep_frames.py
python3 marketing/tour/tools/build_comp.py

# 4. check and render
cd marketing/tour && npx hyperframes lint && npx hyperframes validate && npx hyperframes inspect
npx hyperframes render --quality high --output renders/hermes-otel-tour.mp4
```

Editing the narration: change `SCRIPT.md`, re-run `synth_voice.py` (only changed sentences are
synthesised), then `build_comp.py` (scene lengths follow the audio).
