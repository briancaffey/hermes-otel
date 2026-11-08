# SCRIPT.md — narration, draft 3

The voice is the clock: scene lengths follow the narration. One sentence per line under each scene
heading. Lines starting with `>` are on-screen text only (kicker / caption), never spoken. Edit a line
and re-run `tools/narrate.py`: it re-produces only the scenes whose lines changed.

Voice: Dia 1.6B cloning one designed "Narrator" sample, produced on club.lan's Narration Studio
(autopilot: take → StudioVoice clean → ASR → trim → −16 LUFS → grade → retake). Spoken spellings
live in the episode lexicon (`tools/narrate.py`, LEXICON), not here: write the words normally.

Target: under 90 s (hackathon rule). Budget ≈ 165 spoken words at Dia's ~2.5 words/s plus ~1 s of
lead/tail per scene. Tone: plain, a little mythic at the top, then say what it does. No hype words.

Fact notes (from the repo, v1.32.6): the local store is a SQLite *file* under `$HERMES_HOME`
(owner-only), not in-memory; it holds spans and metrics always, logs when log capture is on. The
dashboard tab reads it or any of 15 backend types with a read adapter. Claude Code compat
(`metric_name_compat`) emits four of Claude Code's eight metrics (session count, token usage, cost
usage, active time) under Claude Code's names, units and labels — so never "the same metrics".
Conventions: OpenTelemetry GenAI (`gen_ai.*`) and OpenInference (`llm.*`, `input.value`) on every
span. Fan-out: one batch processor, thread and queue per backend.

## S01 — the myth (print world)

> THE MYTH
The day he was born, Hermes stole Apollo's cattle and walked them backwards, so no one could follow the tracks.

## S02 — agents still cover their tracks

> THE BLACK BOX
Agents still cover their tracks.
Question in, answer out, and nothing in between.

## S03 — the telescope: why OpenTelemetry

> OPENTELEMETRY FOR HERMES AGENT
Hermes OTel brings the tracks back with OpenTelemetry, the open standard for traces, metrics and logs.

## S04 — one turn, one trace

> ONE TURN · ONE TRACE
Each turn becomes one trace.
Every model call, tool and sub-agent nests inside it, with tokens, cost and timing.

## S05 — start local: SQLite + the dashboard tab

> NO BACKEND NEEDED
With no backend at all, a local SQLite file keeps all three on your machine.
A new tab in the Hermes dashboard shows every turn, chart and log.

## S06 — the tab reads every backend

> ONE TAB · ANY BACKEND
Connect a backend, and the same tab reads it back.

## S07 — any OTLP backend, async fan-out (tile wall + rapid cuts)

> ANY OTLP BACKEND · ALL AT ONCE
Send it anywhere that speaks OTLP.
List several, and each gets the same data on its own async queue, so a slow one never stalls the agent.

## S08 — two conventions

> GEN_AI · OPENINFERENCE
Every span speaks both conventions: OpenTelemetry GenAI and OpenInference.

## S09 — Claude Code metrics

> CLAUDE CODE DASHBOARDS
Flip one switch, and sessions, tokens and cost use Claude Code's own metric names, so your Claude Code dashboards show Hermes too.

## S10 — Hermes reads its own traces

> ASK THE AGENT
And Hermes can read its own traces.

## S11 — close

> FOLLOW THE MESSENGER
Open source, and in the Hermes plugin catalog.
Hermes OTel. Follow the messenger.
