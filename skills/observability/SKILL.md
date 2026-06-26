---
name: observability
description: >-
  Turn on and understand OpenTelemetry observability for THIS Hermes agent via
  the hermes-otel plugin — choose a backend, configure export, and read the
  traces, metrics, and logs your agent produces. Use when a user asks "can I see
  what my agent is doing", "set up tracing/metrics for Hermes", "where do my
  traces go", or wants to debug agent behavior with telemetry.
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
agent                      ← the turn
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
3. **Point the plugin at it** in `~/.hermes/plugins/hermes_otel/config.yaml`
   (copy from `config.yaml.example`):
   ```yaml
   project_name: my-hermes
   backends:
     - type: openobserve
       endpoint: http://localhost:5080/api/default/v1/traces
       user: root@example.com
       password: Complexpass#123
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

## Key configuration knobs

| Setting | Effect |
|---|---|
| `backends:` | one or more OTLP targets; the plugin fans out in parallel |
| `metrics: true/false` | per-backend metric export (off for traces-only backends) |
| `capture_logs: true` | ship Python logs, correlated to the active span |
| `emit_genai_metrics` | also emit OTel-standard `gen_ai.*` metric names (default on) |
| `skill_spans` | emit `skill.<name>` execution-window spans (default on) |
| `capture_previews` | global privacy kill-switch for input/output previews |
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
