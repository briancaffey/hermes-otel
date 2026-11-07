# SCRIPT.md — narration, draft 2

The voice is the clock: scene lengths follow the synthesised audio (`tools/synth_voice.py` writes
`cues.json`). One sentence per line under each scene heading. Lines starting with `>` are on-screen
text only (kicker / caption), never spoken. Edit a line and re-run the synth; only changed sentences
are re-synthesised.

Target: under 90 s (the hackathon limit). Draft 1 measured 2.43 spoken words/s and ~14 s of scene
lead-ins and tails, so the budget is about 180 spoken words. This draft is 178. Tone: plain and
direct, say what it does, no hype words.

Spelling for the voice lives in `tools/pronounce.json` (Hermes O-Tel, O-T-L-P, SigNoz, Langfuse,
Jaeger); write the words normally here.

Changes from draft 1: opens by saying what it is (the cattle myth is gone; "Follow the messenger"
stays only as the end-card line), four scenes on the dashboard tab instead of one, and the fan-out
says what is sent, where, and why it is safe.

## S01 — what it is

> HERMES OTEL · A HERMES AGENT PLUGIN
Hermes OTel is OpenTelemetry for Hermes Agent.
Every turn your agent takes becomes a trace.

## S02 — one turn, one trace (waterfall)

> ONE TURN · ONE TRACE
The turn is the root.
Every model call, tool, skill and sub-agent nests inside it, timed, with its inputs and outputs.

## S03 — the dashboard tab: Live

> BUILT INTO THE HERMES DASHBOARD
It adds an OTel tab to the Hermes dashboard, and it needs no backend.
Turns appear live, with their tokens and cost.

## S04 — inside a turn: trace detail, spans expanded

> INSIDE A TURN
Open one and expand any span: the model's conversation, the tool's command and output, and the logs it wrote.

## S05 — search, metrics, logs, settings

> SEARCH · METRICS · LOGS · SETTINGS
Search by tool, model or status, chart tokens and cost, and filter the agent's logs.
Settings shows every option, where each value came from, and what each backend receives.

## S06 — fan-out to backends

> ONE TRACE · EVERY BACKEND · AT ONCE
Then fan out.
Every backend you list receives the same trace at once, plus whatever metrics and logs it can store.
Each one sends on its own queue, so a slow backend never stalls the agent.
Grafana. SigNoz. OpenObserve. Uptrace. Jaeger. Phoenix. Langfuse.
Twenty-seven backend types.

## S07 — Hermes reads its own traces (terminal recording)

> ASK THE AGENT
Hermes can read its own traces too: ask about the last turn, and it draws the tree in the chat.

## S08 — close

> PRIVATE BY DEFAULT
With no backend, nothing leaves your machine.
Open source, and in the Hermes plugin catalog.
Hermes OTel. Follow the messenger.
