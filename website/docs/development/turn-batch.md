---
sidebar_position: 6
title: "Turn batches"
description: "A prompt library that drives predictable traces, metrics and logs through every configured backend, and a checker that reads them back."
---

# Turn batches

`scripts/turn_batch/` runs a library of prompts through a Hermes profile, one turn after another, and then reads every telemetry source back to see that each turn landed the way its prompt said it would. It is how the logs signal (and the trace and metric shapes around it) is exercised against real backends without anyone watching.

```bash
# 1. run the library through the `minimal` profile, in series
uv run --extra dev python scripts/turn_batch/run_batch.py --profile minimal

# 2. read every source back and compare with the expectations
uv run --extra dev python scripts/turn_batch/check_batch.py \
  --manifest ~/.hermes/turn_batches/<token>/runs.json \
  --python ~/git/hermes-agent/venv/bin/python
```

`run_batch.py` prints the manifest path when it finishes; `check_batch.py` writes `report.md` and `report.json` next to it and exits non-zero when a hard expectation failed anywhere.

## The prompts

`prompts.yaml` holds one entry per behaviour the plugin captures: a single terminal tool call, a turn with no tools, a file write-then-read, a tool that fails while the turn succeeds, a non-zero exit code, several tool calls in one turn, a tool result far above the preview cap, a web fetch, and a skill load (which opens a `skill.<name>` span). Each entry carries a `why` and an `expect` block:

| key | meaning |
|---|---|
| `status` | the root span's status, `ok` or `error` |
| `llm_calls_min` | at least this many `api.*` spans |
| `tools_min`, `tools_max` | bounds on `tool.*` spans |
| `tool_names_any` | some tool span's `tool.name` contains one of these |
| `span_names_any` | some span name contains one of these (`skill.systematic-debugging`) |
| `output_contains` | some span attribute contains this text |
| `logs_min` | at least this many log records correlated with the turn's trace |
| `log_levels_any` | some correlated record is at one of these levels |

Every prompt gets a **marker**, `<token>-<id>`, substituted for `{marker}` and appended as `(batch marker: ...)`, so each turn can be found by text in any backend; `{token}` and `{workdir}` are also available.

## Multi-agent scenarios

`scripts/turn_batch/prompts-multiagent.yaml` holds the fleet shapes (#399). They run on their own because they cost more tokens and time than the default batch, and `make refactor-check` must not pick them up:

```bash
uv run --extra dev python scripts/turn_batch/run_batch.py --prompts scripts/turn_batch/prompts-multiagent.yaml
uv run --extra dev python scripts/turn_batch/check_batch.py --manifest <runs.json>
```

| id | What it drives | What it expects |
|---|---|---|
| `delegation-batch` | one `delegate_task` with two tasks | two `subagent.*` spans, two rejoined child roots |
| `delegation-nested` | an orchestrator child that delegates to a leaf (`delegation.max_spawn_depth: 2`) | an `agent` tree three deep in one trace |
| `moa-turn` | a turn on a Mixture-of-Agents preset (`--provider moa -m cheap`) | two `moa_reference` and one `moa_aggregator` auxiliary `api.*` spans |

A prompt can carry three keys the default library doesn't need:

- `enable_toolsets`: adds toolsets the profile disables (`delegation` in the minimal profile).
- `config`: deep-merged into the profile's `config.yaml` for that one turn.
- `hermes_args`: added to its `hermes chat` command line.

The runner restores `config.yaml` byte for byte after each turn, which also undoes an agent that edits its own config mid-turn. The extra expectation keys are `span_prefix_count_min`, `subagent_roots_min`, `agent_depth_min` and `attr_values_min` (described in the file's header).

`chat -q` runs a delegation batch synchronously. The background path, where `subagent_stop` arrives in a later turn, is covered by `tests/integration/test_subagent_hierarchy.py`. Kanban across two profiles and a cron run need run modes the turn runner doesn't have; the kanban demo (`scripts/kanban_demo`) covers one profile's runs.

## How the turns run

Each prompt is one `hermes -p <profile> chat -Q --yolo -q "<prompt>"` process with stdin closed, a per-turn timeout, and a short pause before the next one. `--yolo` answers approval prompts, so nothing waits on a human, and a turn that times out is recorded and skipped over. The batch uses `chat -q` rather than `hermes -z` because a one-shot run swallows Hermes's stderr, which the batch keeps; both export spans, metrics and logs since 1.24.0 (see [OTel logs](/configuration/logs#one-shot-runs)).

Use a profile whose `hermes_otel.yaml` fans out to the backends under test with `capture_logs: true`, and whose `.env` holds their credentials; a small model keeps the batch cheap (the default library costs about 20k tokens per turn on a 30B model).

## What the checker reads

For every turn and every source, the checker goes through the plugin's own query CLI (the observability skill's `otel.py`, run in the profile's home with its `.env` loaded), so it sees exactly what the dashboard and the skill see:

1. `traces --text <marker>` to find the turn; a backend that ignores the text filter returns its newest traces instead, so the checker opens each candidate and keeps the one whose spans carry the marker.
2. `trace <id>` for the spans, checked against the `expect` block.
3. `logs --trace <id>` on every source that serves logs, for the correlated record count and levels.

The report is a prompt × source matrix (`spans/tools/logs` per cell), a findings list (`FAIL` for a broken expectation or query, `WARN` for something worth a look, such as records without a session id), and the run list with exit codes and durations.

## Reading a failure

- **`no trace found for the marker`** on one backend only: export or ingestion lag; rerun the checker with a longer `--wait`, then look at the plugin debug log for that backend's export lines.
- **`... none carries the marker in a span`**: the backend drops or truncates the captured input; look at the trace in its own UI.
- **`0 trace-correlated log record(s)`** while the live store has them: the records reached the backend without a trace id, or the adapter filters on a column the backend does not have; query the backend directly by time and compare the fields.
- **A root status of `error`** where `ok` was expected usually means the model never answered (provider outage, timeout); the turn's `stderr.txt` next to the manifest has the reason.
