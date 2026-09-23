---
name: observability
description: >-
  See what THIS Hermes agent did, from the terminal: list recent turns, draw one
  turn as a span tree (agent → llm → api → tool …), total up tokens and cost,
  find slow or failed tools, read metrics and logs, and query any configured
  OpenTelemetry backend or the local live store. Use for "why did my agent do
  this?", "what was slow?", "what did that cost?", "how often was skill X
  loaded?", "what did the sub-agent do?", and for configuring hermes-otel.
---

# Observability for your Hermes agent

This skill is shipped by the **hermes-otel** plugin and registered as
`hermes_otel:observability`. The plugin records every turn as one
OpenTelemetry trace: the session, each model call, each API round-trip, each
tool call, approvals, loaded skills and delegated sub-agents, plus token, cost
and latency metrics and (optionally) logs. Spans go to the backends you
configured **and** to a local SQLite live store, so there is always something
to query, even with no backend at all.

> 🪞 Opening this skill emits a `skill.observability` span in the very trace you
> are about to inspect. Observability, observing itself.

## The tool: `otel.py`

A terminal query tool ships next to this skill. Run it with the `terminal`
tool; it needs only Python 3 and the standard library for the live store:

```bash
python3 ${HERMES_SKILL_DIR}/scripts/otel.py <command> [options]
```

| Command | Shows |
|---|---|
| `status` | which Hermes home and profile this is, where the live store is and how full it is, the config file, every configured backend and whether it can be queried from here |
| `traces` | recent turns, one row each: when, root, session, model, duration, tokens, cost, spans, status |
| `trace last` · `trace last-2` · `trace <id>` | one turn as the span tree with a duration column, a waterfall bar and a one-line summary per span |
| `span <span-id>` | every attribute of one span (full prompts, tool arguments, results) |
| `sessions` | one row per session: turns, spans, tool calls, errors, tokens, cost, first/last |
| `stats` | the window's totals: turns, sessions, errors, tokens, cost, a table per model and per tool, skills loaded, approvals, the slowest turns |
| `metrics [name]` | the instruments with data, or one instrument's totals per group (`--group-by model`) and per time bucket (`--buckets`) |
| `logs` | captured log records (`capture_logs: true`), filterable by level, logger, text, trace, session |
| `sql "<select …>"` | read-only SQL against the live store, for anything the commands above do not cover |

Options every command takes: `--json` for machine-readable output, `--since 30m|2h|3d|1w|2026-09-20T10:00` and `--until` for the window, `--limit N`, and `--source <backend name or type>` to query a configured backend instead of the live store. `traces` and `stats` also take `--session`, `--status error`, `--model`, `--tool bash`, `--name skill.foo`, `--text`, `--min-duration MS`. `trace` takes `--attrs` (every attribute under each span) and `--io` (captured input/output under llm, api and tool spans).

A tree looks like this (a real `hermes -z` turn that loaded this skill and ran the tool three times):

```text
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

Each line: the span name with the tree guides from the README, its duration,
where it sits in the turn, and what matters for its kind. A span whose status
is `ERROR` ends with `ERROR: <message>`.

## Recipes

Start with `status` once per conversation so you know the window you have (the
live store keeps the last `dashboard_live_max_spans` rows, 168 hours by
default) and which sources exist. Then:

| Question | Run |
|---|---|
| What did my last turn do? | `trace last` — add `--io` to read the prompt, response, tool arguments and results. While a turn is running, `last` is that turn (still without its root span); `last-2` is the one before it |
| Why was it slow? | `trace last` and read the duration column and the bars; `traces --since 1d --min-duration 30000` for every slow turn; `stats --since 1d` for the average per model and per tool |
| What did today cost? | `stats --since 1d` (per model), `sessions --since 1d` (per session), `traces --since 1d` (per turn) |
| What went wrong? | `traces --since 1d --status error`, then `trace <id>` and look for `ERROR:`; `logs --level error` if logs are captured |
| Did skill X load, and how often? | `traces --since 7d --name skill.X` counts turns; `sql "select count(*) from events where name='skill.X'"` counts loads |
| Which tools ran and how did they end? | `stats --since 1d` (the tools table: calls, completed, errors/timeouts, average), `traces --tool bash` for the turns that used one |
| What did the sub-agent do? | `trace <id>`: the child run nests under its `subagent.<role>` span; `span <id>` on the subagent span for its goal and summary |
| Was a command approved? | `trace <id>`: the `approval.<pattern>` span shows the choice and who decided |
| What is in the metrics? | `metrics`, then `metrics hermes.token.usage --since 1d --group-by model` (the `gen_ai.client.token.usage` twin is grouped by `gen_ai.request.model`) |
| Ask the backend instead | the same commands with `--source phoenix` (or the entry's `name:`); `status` lists which backends can be queried and for what |

Anything else: the live store is one table, `events`, with a `kind` column
(`span`, `metric`, `log`), indexed columns (`ts`, `trace_id`, `span_id`,
`parent_span_id`, `session_id`, `name`, `status`, `start_ns`, `end_ns`,
`duration_ms`, `level`, `logger`, `value`) and the full record as JSON in
`data`. Attributes are under `$.attributes`, for example:

```bash
python3 ${HERMES_SKILL_DIR}/scripts/otel.py sql "select name, json_extract(data, '\$.attributes.\"hermes.tool.outcome\"') as outcome, count(*) from events where kind='span' and name like 'tool.%' group by 1, 2"
```

## Report evidence, not impressions

- **State the window and the denominator** with every count: the retention
  window, the number of turns (`agent` and `cron` root spans) and the exact
  filter. "skill X loaded in 3 of 41 turns since Monday" is a finding; "skill X
  is rarely used" is not.
- **Inspect one narrow trace before aggregating.** `trace last` first, then
  `stats`; check that the spans you are counting mean what you think.
- **A skill span proves a load, not a decision.** To judge whether the model
  should have used a skill, compare the root span's input and
  `hermes.turn.skills` against the skill catalog and verify the mismatches by
  hand.
- **Do not dump full prompts across many spans.** `--io` and `span` are for one
  turn at a time; use `traces`, `stats` and `--json` for breadth. Captured
  content can be large and may be sensitive.
- **Backends lag.** A trace can take seconds to appear in a backend after the
  turn ends, and Prometheus-style metrics go stale a few minutes after the
  process exits. The live store is immediate.
- **Logs need `capture_logs: true`.** An empty `logs` result is usually that,
  not an absence of problems.

## If a command fails

- `no live store at …`: no turn has been recorded yet, `dashboard_live` is
  `false`, or the gateway runs with a different `HERMES_HOME`. Pass `--db
  <path>` to point at the file.
- `backend queries read the config file with pyyaml`: `--source` needs the
  interpreter Hermes runs on. `hermes --version` prints the install directory;
  use its `venv/bin/python` instead of `python3`.
- `backend … has no query adapter`: the backend's own UI is the way to query
  it. `status` shows which backends can be queried from here.

## Configuring hermes-otel

Telemetry to a backend needs one of two things: an environment variable such
as `OTEL_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces`, or
`~/.hermes/hermes_otel.yaml`:

```yaml
project_name: my-hermes
backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces        # traces
  - type: openobserve
    endpoint: http://localhost:5080/api/default/v1/traces
    user_env: OPENOBSERVE_USER
    password_env: OPENOBSERVE_PASSWORD
    metrics: true                                    # traces + metrics + logs
capture_logs: true
```

The startup banner prints `✓ Multi-backend fan-out active` when a backend is
configured. Knobs that change what this tool can show: `dashboard_live`
(the live store; on by default), `dashboard_live_max_spans` and
`dashboard_live_retention_hours` (how much it keeps), `capture_logs` (logs),
`content_capture` (`full`, `preview` or `off`: how much of prompts, responses
and tool I/O is recorded), `skill_spans` (the `skill.<name>` spans),
`host_metrics` (CPU/GPU per tool call). The full list, every backend and the
privacy settings are at https://briancaffey.github.io/hermes-otel/.

Not seeing data at all? `HERMES_OTEL_DEBUG=true` writes a per-span log next to
the plugin (`debug.log`) that says whether spans left the process and what each
backend answered.
