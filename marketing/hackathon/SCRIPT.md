# SCRIPT.md — narration, draft 1

The voice is the clock: scene lengths follow the synthesised audio (`tools/synth_voice.py` writes
`cues.json`). One sentence per line under each scene heading. Lines starting with `>` are on-screen
text only (kicker / caption), never spoken. Edit a line and re-run the synth; only changed sentences
are re-synthesised.

Target: under 90 s (the hackathon limit), aiming for 82–86 s. About 175 spoken words, ~140 wpm with
the pauses. Tone: Nous house voice — confident, terse, a little mythic, no hype words.

Spelling for the voice lives in `tools/pronounce.json` (Hermes O-Tel, O-T-L-P, A-P-I, SigNoz,
Langfuse, Jaeger); write the words normally here.

## S01 — hook: the myth (cobalt engraving world)

> THE MYTH
The day he was born, Hermes stole Apollo's cattle and walked them home backwards, so nobody could follow the tracks.

## S02 — tension: agents still hide them

> THE PROBLEM
Agents still cover their tracks.
A turn runs, an answer appears, and everything in between is gone.

## S03 — reveal

> HERMES OTEL
Hermes OTel is OpenTelemetry for Hermes Agent.
Every turn leaves a trace.

## S04 — the trace (real waterfall)

> ONE TURN, ONE TRACE
The turn is the root.
Each model call, API request, tool, skill and sub-agent nests inside it, carrying its tokens, timing, arguments and outcome.

## S05 — the dashboard tab (real dashboard)

> BUILT INTO HERMES
A tab in the Hermes dashboard shows turns as they land, totals tokens and tool time, and puts the agent's own logs beside the span that wrote them.

## S06 — the payoff: every thread, every backend (real backend UIs)

> TRACES · METRICS · LOGS
Traces, metrics and logs go out over OTLP, to the tools you already run.
Grafana. SigNoz. OpenObserve. Uptrace. Jaeger. Phoenix. Langfuse.
Twenty-seven backend types, several at once.

## S07 — Hermes reads its own tracks (real terminal recording)

> ASK THE AGENT
And Hermes can read its own tracks.
Ask what the last turn did, and it draws the trace right in the chat.

## S08 — claim and end card

> PRIVATE BY DEFAULT
With no backend configured, nothing leaves your machine.
Open source, and in the Hermes plugin catalog.
Hermes OTel. Follow the messenger.
