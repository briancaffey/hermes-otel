---
sidebar_position: 5
title: "The observability skill"
description: "hermes_otel:observability, the bundled Hermes skill: a terminal query tool the agent runs from the chat to list turns, draw a trace as a span tree, total up tokens and cost, and read metrics and logs from the live store or any configured backend."
---

# The observability skill

The plugin registers one Hermes skill, `hermes_otel:observability`. It turns the agent itself into an observability client: from the chat, the agent can list its recent turns, draw one turn as the span tree this documentation uses everywhere, add up tokens and cost, find slow or failed tools, read metrics and logs, and run read-only SQL, against the local live store or any configured backend that the dashboard can query.

Plugin skills are explicit-load only, so it never shows up in the `<available_skills>` catalog. Ask for it by name, or just ask the question:

```text
Load hermes_otel:observability and show me the tree of my last turn.
Why was my last turn slow?
What did this session cost?
How often was the deployer skill loaded this week?
```

Set `discovery_prompt: true` to add a one-line hint to every system prompt so the model reaches for the skill on its own when a question is about past behaviour (off by default; it changes what the model sees every turn).

## What the agent runs

The skill ships a script, `scripts/otel.py`, next to its `SKILL.md`. Hermes lists it under the skill's linked files and expands `${HERMES_SKILL_DIR}` in the skill text, so the skill hands the agent a command it can run with its terminal tool:

```bash
python3 ${HERMES_SKILL_DIR}/scripts/otel.py trace last
```

You can run the same script yourself; on a default install it is at `~/.hermes/plugins/hermes_otel/skills/observability/scripts/otel.py`. It needs only Python 3 and the standard library to read the live store. Querying a backend with `--source` additionally needs `pyyaml` (to read the config file), which the Hermes interpreter has.

| Command | Shows |
|---|---|
| `status` | the live store's path and fill (spans, metric points, logs, oldest to newest), the config file in use, every configured backend and whether it can be queried from here |
| `traces` | recent turns, one row each: when, root, session, model, duration, tokens, cost, spans, status |
| `trace last` · `trace last-2` · `trace <id>` | one turn as the span tree: tree guides, duration, a waterfall bar, and a one-line summary per span kind |
| `span <span-id>` | every attribute of one span |
| `sessions` | one row per session: turns, spans, tool calls, errors, tokens, cost, first and last |
| `stats` | the window's totals: turns, sessions, errors, tokens, cost, one table per model and per tool, skills loaded, approvals, the slowest turns |
| `metrics [name]` | the instruments with data, or one instrument's totals per group and per time bucket |
| `logs` | captured log records, filterable by level, logger, text, trace and session |
| `sql "<select …>"` | read-only SQL against the live store |

Common options: `--json` on every command; `--since 30m|2h|3d|1w|<ISO time>` and `--until` for the window; `--limit`; `--source <backend name or type>` to query a backend instead of the live store; `--db <file>` to point at a live store other than `$HERMES_HOME/hermes_otel_live.db`. `traces` and `stats` filter with `--session`, `--status error`, `--model`, `--tool`, `--name`, `--text` and `--min-duration`. While a turn is running, `trace last` is that turn (its root span arrives when it ends); `last-2` is the previous one. `trace` adds `--attrs` (every attribute under each span), `--io` (captured input and output under `llm`, `api` and `tool` spans, clipped to `--io-chars`), `--width` and `--no-bars`.

## What a tree looks like

This is a real turn: `hermes -z` was asked to load the skill and run the tool three times, and the tool then drew that turn.

```text
$ python3 ~/.hermes/plugins/hermes_otel/skills/observability/scripts/otel.py trace last
trace 856433c66e23d0b8a0ef10d9ba2c1c53 · source live · 2026-09-21 23:53:22 UTC · session 20260921_195321_b18e68 · model openai/gpt-4o-mini

agent                             13.22 s  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇  turn 1 · 2 tools · 3 api calls · 39,919 tok · completed · cli
├── llm.openai/gpt-4o-mini        13.19 s  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇   openrouter
│   ├── api.openai/gpt-4o-mini     2.01 s  ▇▇                10,695 → 20 tok · tool_calls · 1 tool calls
│   ├── tool.skill_view             37 ms    ▇               completed
│   ├── api.openai/gpt-4o-mini     2.67 s    ▇▇▇             13,810 → 254 tok · 10,624 cached · tool_calls · 3 tool calls
│   ├── tool.terminal              3.81 s        ▇▇▇▇        completed · python3 /private/tmp/claude-501/-Users-brian-gi…
│   ├── tool.terminal              869 ms            ▇       completed · python3 /private/tmp/claude-501/-Users-brian-gi…
│   ├── tool.terminal              315 ms             ▇      completed · python3 /private/tmp/claude-501/-Users-brian-gi…
│   └── api.openai/gpt-4o-mini     3.09 s              ▇▇▇   14,832 → 308 tok · 13,952 cached · stop
└── skill.observability           10.97 s    ▇▇▇▇▇▇▇▇▇▇▇▇▇   skill_view · completed
── 10 spans · 13.22 s · 39,919 tokens
```

The summary column depends on the span kind:

| Span | Summary |
|---|---|
| `agent` / `cron` | turn number, tool and API-call counts, the turn's tokens and cost (counted once), final status, platform |
| `llm.*` | provider, message count |
| `api.*` | input → output tokens, reasoning and cached tokens, cost, finish reason, tool calls, retries |
| `tool.*` | outcome, the command or target, what blocked it |
| `approval.*` | the choice and who decided, timeouts |
| `subagent.*` | role, status, the goal |
| `skill.*` | how it was loaded |

A span in `ERROR` status ends its line with `ERROR: <message>`. When the summary does not fit the width it continues on the next line under the span.

## Sources

**The live store** is the default. It is on by default (`dashboard_live`), local, immediate, and holds every span the plugin produced whether or not a backend is configured, bounded by `dashboard_live_max_spans` and `dashboard_live_retention_hours`. It is the same file the [dashboard tab](/dashboard) reads.

**A configured backend** (`--source phoenix`, or the entry's `name:`) is queried through the same adapters the dashboard uses, so the same backends and capabilities apply: Phoenix, Langfuse, Jaeger, Tempo, SigNoz, OpenObserve and Uptrace for traces; metrics and logs only where the [dashboard's table](/dashboard#which-backends-can-the-tab-query) says so. `sessions` is a live-store view; on a backend use `traces --session`. `status` reports each backend's capabilities.

## The SQL escape hatch

The live store is one table, `events`. `kind` is `span`, `metric` or `log`; `data` holds the full record as JSON with attributes under `$.attributes`; the columns `ts`, `trace_id`, `span_id`, `parent_span_id`, `session_id`, `name`, `status`, `start_ns`, `end_ns`, `duration_ms`, `level`, `logger` and `value` are extracted and indexed. The file is opened read-only and only `SELECT`, `WITH`, `EXPLAIN` and `PRAGMA` statements are accepted.

```bash
otel.py sql "select name, json_extract(data, '\$.attributes.\"hermes.tool.outcome\"') as outcome, count(*) \
             from events where kind='span' and name like 'tool.%' group by 1, 2"
```

## How the skill guides the agent

Besides the commands, the skill text carries the method: run `status` first to learn the window; state the retention window, the number of turns and the exact filter with every count; inspect one trace before aggregating; treat a `skill.<name>` span as proof of a load, not of a decision; never dump full prompts across many spans (`--io` and `span` are for one turn at a time); expect backends to lag and Prometheus-style metrics to go stale after the process exits; remember that `logs` is empty unless `capture_logs` is on.

## Limits

- The script reads what the plugin recorded. With `content_capture: off`, `--io` and `span` show no prompts or tool output; with `preview`, clipped ones.
- The live store is bounded. For history beyond its window, query a backend.
- Backend queries go through adapters, so a backend type without one (Honeycomb, LangSmith, W&B Weave, Parseable, generic `otlp`) is listed by `status` as not queryable from here. Its own UI is the way in.
