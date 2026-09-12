---
name: observability
description: >-
  Configure or query OpenTelemetry for THIS Hermes agent via hermes-otel. Use
  for historical agent behavior, skill/tool invocation counts, model calls,
  latency, errors, retries, cost, or questions such as "why did my agent do
  this?", "how often was this skill read?", and "what is slow?"
---

# Observability for your Hermes agent

This skill is shipped by the **hermes-otel** plugin and registered as
`hermes_otel:observability`. The plugin instruments your agent's lifecycle —
sessions, model calls, tool calls, sub-agents, and **skills** — and exports them
as OpenTelemetry spans, metrics, and logs to any OTLP/HTTP backend.

> 🪞 **You are looking at the feature work.** Because the plugin instruments
> skill loads, the act of opening this skill emits a `skill.observability` span
> in the very trace you're about to go inspect. Observability, observing itself.

## What you get, at a glance

A trace per turn, shaped like:

```
agent or cron              ← the turn
├── skill.<name>           ← a loaded skill (load → turn end; overlaps OK)
└── llm.<model>
    └── api.<model>        ← one HTTP round-trip
        ├── tool.<name>    ← each tool call
        └── subagent.<role>← a delegated child agent
```

Plus metrics (token usage, cost, tool/skill counts, durations) under both the
custom `hermes.*` names and the standard OTel GenAI `gen_ai.*` names, so generic
dashboards work out of the box.

## Turn it on (three steps)

1. **Install OTel runtime deps** into the hermes-agent venv (once):
   ```bash
   <hermes-venv>/bin/pip install \
     opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
   ```
2. **Run a backend.** Easiest local pick: OpenObserve or Grafana LGTM (traces +
   metrics + logs). Phoenix is great for LLM-span inspection (traces only).
3. **Point the plugin at it** in the durable user configuration file
   `~/.hermes/hermes_otel.yaml`:
   ```yaml
   project_name: my-hermes
   backends:
     - type: openobserve
       endpoint: http://localhost:5080/api/default/v1/traces
       user_env: OPENOBSERVE_USER
       password_env: OPENOBSERVE_PASSWORD
       metrics: true
   ```

On the next turn the startup banner prints `✓ Multi-backend fan-out active`.

## Verify it works

Run any turn, then open your backend UI and find the trace for `project_name`.
You should see the `agent` root with `llm` / `api` / `tool` children. If you
asked the agent to use a skill, you'll see a `skill.<name>` span spanning the
turn — including a `skill.observability` span from *this* skill.

Not seeing data? The usual suspects:
- **Metrics show "No Data"** — metric histograms are queried by suffix
  (`..._sum` / `..._count`), never the bare name.
- **Empty at "now"** — Prometheus-style backends go stale ~5 min after the
  process exits; widen the time range.
- **Nothing at all** — check the startup banner connected; check the endpoint
  port and that the backend container is up.

## Analyze existing agent behavior first

When the question is about what Hermes did historically, query the configured
telemetry backend before searching logs or parsing session dumps. Logs are useful
for failures that happened before export; they are not the primary usage ledger.

Start by establishing the observed window and denominator. A count without the
retention window and number of turns is misleading. Skill loads are represented
by `skill.<name>` spans; `tool.skill_view` counts all skill-loading calls.

Use the configured backend's query UI or API and inspect one narrow trace before
writing aggregates. Backend-specific query syntax, authentication, and table or
index names belong in that backend's documentation, not this portable skill.

For frequency analysis:

- Bound every query to an explicit retention window.
- Project only the span name, timestamp, trace ID, and attributes needed.
- Count `skill.<name>` spans for individual skill loads.
- Count `agent` and `cron` root spans for the turn denominator.
- Avoid fetching full captured prompts or tool results across many spans.

Report the retention window, total turns/traces, and exact span filter with every
frequency claim. A skill span proves the skill loaded; it does not prove the
model considered every other skill and rejected it. For missed-invocation
analysis, compare the root `agent` or `cron` span's input and
`hermes.turn.skills` fields against the skill catalog, then manually verify
high-confidence mismatches.

## Key configuration knobs

| Setting | Effect |
|---|---|
| `backends:` | one or more OTLP targets; the plugin fans out in parallel |
| `metrics: true/false` | per-backend metric export (off for traces-only backends) |
| `capture_logs: true` | ship Python logs, correlated to the active span |
| `emit_genai_metrics` | also emit OTel-standard `gen_ai.*` metric names (default on) |
| `skill_spans` | emit `skill.<name>` execution-window spans (default on) |
| `capture_previews` | global privacy kill-switch for input/output previews |
| `host_metrics` | sample CPU/GPU into `process.*` / `system.*` / `hw.*` metrics and per-tool utilization attributes (default off) |
| `sample_rate` | head sampling (0–1) for high-volume agents |

## Going deeper

- **Span shapes & attributes** — every span and field is documented in the
  plugin's `website/docs/reference/span-attributes.md` and
  `website/docs/architecture/span-hierarchy.md`.
- **Backends** — per-backend setup lives in `docker-compose/<backend>/`.
- **Privacy** — `capture_previews: false` suppresses all input/output values;
  previews are length-capped by `*_preview_max_chars`.

Observability should feel like turning on the lights. Pick a backend, run a
turn, and watch your agent's work draw itself.
