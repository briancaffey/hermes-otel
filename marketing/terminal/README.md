# hermes-otel terminal demo

Terminal clips for the promo video: a "user" types into Hermes one character at a time, the
answers come in, and the observability skill and the otel MCP server turn the run into span
trees, token tables and trace searches. Recorded with [asciinema](https://asciinema.org) by a
script (nobody at the keyboard) and rendered to 1080p MP4s that drop into the HyperFrames tour
(`../tour`). Tracking issue: #442.

```
marketing/terminal/
  scenes/*.yaml      one scene per clip: the messages, typing speed, pauses
  tools/
    install_tools.sh asciinema 3.2.1 + agg 1.9.0 from their GitHub releases, pinned (#437)
    make_profile.sh  the `promo` Hermes profile (#438)
    demo_bashrc      the clean shell the recordings start in
    record.py        tmux + asciinema driver: types, waits for each turn, writes a raw cast (#439)
    edit_cast.py     shortens the model waits, cuts the tail -> casts/<scene>.cast
    render.py        cast -> 1920x1080 MP4 (asciinema-player in Chromium, or agg) (#441)
    fetch_assets.sh  the player bundle and JetBrains Mono into .cache/
    take.sh          record + edit + render one scene
  casts/<scene>.cast the edited casts (committed; also playable on the docs site)
  casts/<scene>.timeline.json  what the driver did and when (aligns the edit)
  renders/           MP4s and stills — not in git
```

## One-time setup

```bash
marketing/terminal/tools/install_tools.sh   # asciinema + agg into ~/.local/bin
marketing/terminal/tools/fetch_assets.sh    # player + font into .cache/
marketing/terminal/tools/make_profile.sh    # ~/.hermes/profiles/promo
docker compose -f docker-compose/jaeger/docker-compose.yaml up -d   # the backend the scenes read
```

Also needs `tmux`, `ffmpeg` and `uv` (for the Playwright and PyYAML script dependencies).

`make_profile.sh` copies only `NVIDIA_API_KEY` from the `minimal` profile, links the plugin from
this checkout, writes a `jaeger-local` exporter config and registers `opentelemetry-mcp` as `otel`
with `otel.py mcp-config --apply`. The profile is the reason the screen stays clean, so each
setting in it is there on purpose:

| Setting | Why |
|---|---|
| `toolsets: [terminal, skills]`, everything else disabled (incl. `image_gen`, `web`, `file`) | the banner lists only what the demo uses; shorter prompts, faster turns |
| `SOUL.md`: short answers, tool output in a fenced code block, bold only for the answer | the markdown renderer reflows plain lines into a paragraph; a fence keeps the span tree intact |
| `display.streaming: false`, `final_response_markdown: render` | the CLI streams raw markdown (`**67**`); off, the answer box renders it |
| `display.tool_preview_length: 64` | the skill runs `otel.py` by absolute path; untrimmed it wraps |
| `auxiliary.background_review.enabled: false` | the post-turn review fork keeps the prompt busy and leaves stray root spans |
| `telemetry.shared_metrics` with `offer_version: 2` | answers Hermes's "Help improve Hermes?" offer (No thanks) so it never appears |
| `onboarding.seen.*` | the one-time hints (OpenClaw residue, busy input, …) |
| removing the seeded `autonomous-ai-agents` skill | `--no-skills` still seeds it |

The random `✦ Tip:` line under the banner has no switch; it differs between takes.

## A take

```bash
cd marketing/terminal
tools/take.sh span-tree        # ~1.5 min recording + ~1-2 min render
open renders/span-tree.mp4
tools/take_all.sh              # every scene, in order: cli must follow span-tree
```

Scene prompts name the skill exactly (`hermes_otel:observability`) and keep the model away from
timestamp arithmetic: a failed `skill_view` or a non-zero `date -d` (GNU syntax on macOS) is a
child span with status ERROR, the plugin rolls that up to the turn, and the clip would show a red
`ERROR` row. That is the plugin telling the truth; it is just not the story for these clips.

`record.py` starts a detached tmux session at the scene's size, runs `asciinema rec
--capture-input` on `demo_bashrc`, and plays the scene: `type` (one character at a time,
~17 characters/s with jitter, a beat after punctuation, rare hesitations; seeded so a re-take types
the same way), `key`, `pause`, `wait_for` (text on screen), `say` (type a message, Enter, wait for
the turn) and `run` (a shell command). A turn has ended when the plugin's live store has a new
`agent` root span **and** the screen is quiet **and** the prompt reads `promo ❯` again.

`edit_cast.py` keeps typing at real speed, plays each model wait (Enter → Hermes's answer box)
faster so it lasts at most 4 s (tool-progress lines still appear in order), caps other pauses at
1.6 s, cuts at the scene's end before the driver's Ctrl-C, and with `start_at: ready` opens the clip
on Hermes's first idle screen instead of the launch.

## Rendering

`render.py` has two engines; the default is the better one.

| | `player` (default) | `agg` |
|---|---|---|
| How | asciinema-player 3.17.0 in headless Chromium; each frame is `seek(t)` on a paused player and a screenshot, piped to ffmpeg | asciinema's `agg` writes a GIF; ffmpeg scales and pads it to 1080p |
| Colour | true colour (Hermes's gold banner and status bar are 24-bit) | 256 colours per frame (fine for this content) |
| Text | Chromium's text rendering at the native size, JetBrains Mono | rasterised at agg's size, then upscaled |
| Frame | flat window with a title bar on the `#0d1117` canvas (`marketing/DESIGN.md`) | bare terminal, padded |
| Speed (45 s clip, M-series Mac) | ~45-120 s | ~45 s |

The font size is computed from the scene's columns and rows: 120x30 gives 24 px, 150x34 about
21 px. Frames are only re-rendered when the cast has new output, so idle stretches are cheap.
`--fps 60`, `--crf`, `--hold` and `--still <s>` (save a PNG at a time) are there for finishing.
