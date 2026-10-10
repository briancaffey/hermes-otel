# SCRIPT.md — narration (locked once the audio is cut)

Voice: Magpie Mia, ~150 words a minute. One sentence per line, no semicolons or colons (the voice
service ends an utterance there). Pronunciation: "Hermes O-Tel", "O-T-L-P", "L-L-M", "A-P-I",
"SIG-nahz", "LANG-fyooz", "Up-trace", "Jay-ger", "Fee-nix", "Open-Observe".

## S01 — cold open (over the hero waterfall, dark)

Your agent just ran for seventeen seconds.
It made six model calls, used five different tools and spent fifty-two thousand tokens.
Which step was slow, which one failed, and what did it actually read?
If every turn is a black box, you are guessing.

## S02 — title

Hermes O-Tel is OpenTelemetry for Hermes Agent.
One plugin, every turn recorded, any backend you already run.

## S03 — how it works, hooks to spans

Hermes fires lifecycle hooks as a turn unfolds.
Session start, each L-L-M call, each A-P-I request, each tool call, approvals, sub-agents, session end.
The plugin subscribes to those hooks and turns them into one trace per turn.
The agent span is the root, the model call nests inside it, and every A-P-I request and tool call hangs off that.
Each span carries the model, the token counts, the tool name and its arguments, the status and the error text.

## S04 — three signals

The same turn produces three signals.
Traces show the shape and timing of the turn.
Metrics add up tokens, cost, calls and tool durations over time.
Logs carry the agent's own log lines, stamped with the trace id, so a line can be read next to the span that wrote it.
All three leave over O-T-L-P, the standard wire protocol, so nothing in the plugin is tied to one vendor.

## S05 — any backend

Point it at a backend with one entry in a yaml file, or with a single environment variable.
Nineteen backend types are supported, from Phoenix and Langfuse to Grafana, SigNoz, Elastic and Honeycomb, plus a generic O-T-L-P entry for anything else.
Several can run at once, and every signal fans out to all of them in parallel.

## S06 — the dashboard

The plugin also ships a dashboard tab for the Hermes web UI.
It keeps a live, local record of every turn, and it can read the same turns back from the backend you chose.
This is the Live tab, filling in as the agent works.
The Traces tab lists every turn, with its model, span count, tokens and the status of each tool.
Open a turn and you get the full waterfall.
Here the agent fetched a page, wrote a file, ran a shell command, read the file back and loaded a skill, with a model call before each step.
The Metrics tab totals tokens by type, calls by model and tool durations.
The Logs tab shows the agent's log lines, filtered by level, logger, session or a single trace.
And the Sessions view groups turns by conversation.

## S07 — gallery intro

The same run, read back from seven different backends.
Every screenshot you are about to see comes from the same recorded workload.

## S08 — Grafana LGTM

Grafana with Tempo, Prometheus and Loki.
The waterfall in Tempo matches the dashboard span for span, the token counters land in Prometheus, and the log lines sit in Loki with the trace id attached.

## S09 — OpenObserve

OpenObserve takes all three signals on a single port.
Traces, logs and metrics, each in its own stream, searchable with SQL.

## S10 — SigNoz

SigNoz, with its query builder over traces, metrics and logs.
The dashboard reads it through the same API the SigNoz UI uses.

## S11 — Uptrace

Uptrace groups the spans, charts the counters and keeps every log line next to the span that produced it.

## S12 — Jaeger

Jaeger, the classic trace store, in both its version one and version two lines.
Traces only, and the dashboard says so instead of pretending.

## S13 — Phoenix

Arize Phoenix speaks OpenInference, so each span shows up typed as an agent, an L-L-M call or a tool.
The plugin emits both attribute conventions, so Phoenix and the Gen-A-I backends read the same data.

## S14 — Langfuse

Langfuse turns the turn into a trace of observations, with sessions and token usage per generation.
Version three and version four are both supported.

## S15 — honesty and verification

Every adapter is checked against its backend with a real workload before a release.
The dashboard compares the backend's answer with its own live record, span for span and token for token.
What a backend cannot store is marked as such, never substituted.

## S16 — install

Install it with one command, add one backend entry, restart Hermes.
From the next turn on, every run leaves a complete record.
Hermes O-Tel.
See every span.
