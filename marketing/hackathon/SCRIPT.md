# SCRIPT.md — narration, draft 4

The voice is the clock: scene lengths follow the narration. One sentence per line under each scene
heading. Lines starting with `>` are on-screen text only (kicker / caption), never spoken. Edit a line
and re-run `tools/narrate.py`: it re-produces only the scenes whose lines changed.

Voice: Dia 1.6B cloning one designed "Narrator" sample, produced on club.lan's Narration Studio
(autopilot: take → StudioVoice clean → ASR → trim → −16 LUFS → grade → retake). Spoken spellings
live in the episode lexicon (`tools/narrate.py`, LEXICON), not here: write the words normally.

Target: under 90 s (hackathon rule). Budget ≈ 165 spoken words at Dia's ~2.5 words/s plus ~1 s of
lead/tail per scene. Draft 4 is 213 words (draft 3 was 184 and ran 92.5 s): over the limit
until scenes are tightened or lines cut. Tone: plain, a little mythic at the top, then say what it does. No hype words.

Fact notes (from the repo, v1.32.6): the local store is a SQLite *file* under `$HERMES_HOME`
(owner-only), not in-memory; it holds spans and metrics always, logs when log capture is on. The
dashboard tab reads it or any of 15 backend types with a read adapter. Claude Code compat
(`metric_name_compat`) emits four of Claude Code's eight metrics (session count, token usage, cost
usage, active time) under Claude Code's names, units and labels — so never "the same metrics".
Conventions: OpenTelemetry GenAI (`gen_ai.*`) and OpenInference (`llm.*`, `input.value`) on every
span. Fan-out: one batch processor, thread and queue per backend.

## S01 — the myth (print world)

> A PLUGIN FOR HERMES AGENT
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

> LOCAL BY DEFAULT
Out of the box, Hermes OTel captures OpenTelemetry data locally, in a SQLite file on your machine.
The plugin adds a tab to the Hermes dashboard.
There you can browse every trace, chart your metrics and search your logs.

## S06 — the tab reads the backends too

> ONE TAB · FIFTEEN BACKENDS
The dashboard also displays data from fifteen OTel backends, using their APIs and custom adapters.

## S07 — any OTLP backend, async fan-out (tile wall + rapid cuts)

> ANY OTLP BACKEND · ALL AT ONCE
The plugin does asynchronous fan-out to any number of configured backends, so a slow backend doesn't slow your agent.
Hermes OTel supports virtually every OpenTelemetry compatible backend.

## S08 — two conventions

> GEN_AI · OPENINFERENCE
There are two popular conventions for annotating agent data: OpenTelemetry GenAI and OpenInference.
Hermes OTel supports both.

## S09 — Claude Code metrics

> CLAUDE CODE DASHBOARDS
Hermes OTel implements four of Claude Code's usage metrics: sessions, tokens, cost and active time, so your Claude Code dashboards show Hermes too.

## S10 — the skill: traces in chat

> ASK THE AGENT
Hermes OTel ships with a skill that lets your agent read and display traces right in its chat responses.

## S11 — close

> FOLLOW THE MESSENGER
Open source, and in the Hermes plugin catalog.
Hermes OTel. Follow the messenger.
