---
sidebar_position: 6
title: "Scenario harness"
description: "Real Hermes runs (one-shot, resumed, through the gateway, cron and kanban) with telemetry expectations checked against the live store and every configured backend."
---

# Scenario harness

`scripts/scenarios/` runs a library of **scenarios** through a real Hermes and then reads every telemetry source back to see that each one landed the way it should. A scenario is one or more turns that exercise a known slice of the hook contract: a single tool call, a resumed session, five sessions at once in one gateway process, an approval answered over HTTP, a cron job, two profiles working a kanban board. It checks telemetry, not the model's answers.

It is development tooling: nothing in `scripts/` is installed with the plugin.

## Running it

```bash
make scenarios                  # quick suite: the cli and resume scenarios, a few minutes
make scenarios-full             # every mode: gateway, approvals, cron, kanban, sub-agents
make scenarios ONLY=shell-echo,resume-three-turns
make scenarios BACKENDS_FROM=   # live store only
```

Each target does three things:

1. **`setup_profile.py`** creates or refreshes a dedicated Hermes profile, `scenarios`. It gets:
   - the model of another profile (`MODEL_FROM`, default `default`);
   - this checkout's `hermes_otel/` as its plugin;
   - a small toolset;
   - manual approvals with a 20-second timeout;
   - `gateway.standalone: true`, so a multiplexed host gateway never serves it;
   - the `backends:` of another profile's `hermes_otel.yaml` (`BACKENDS_FROM`, default `default`).

   `make scenarios-full` also builds a second profile, `scenarios-b`, for the kanban scenario. `SECOND_MODEL_ARGS` gives it its own model, for example:

   ```bash
   make scenarios-full SECOND_MODEL_ARGS="--second-provider nvidia \
     --second-model nvidia/nemotron-3-super-120b-a12b --copy-env NVIDIA_API_KEY"
   ```

2. **`run.py`** drives Hermes and writes `runs.json`, every turn's stdout and stderr, and each gateway's log under `~/.hermes/profiles/scenarios/scenario_runs/<token>/`.
3. **`check.py`** reads every source back through the plugin's own query CLI (`otel.py`, the same code the dashboard and the skill use). It writes `report.md` and `report.json`, and exits 1 when a hard expectation failed. A backend that doesn't answer is skipped and named in the report, never failed.

Your own profiles are never touched. A scenario's config overlays apply to the `scenarios` profile only and are put back byte for byte after it runs. Gateway scenarios start their own gateway with the API server on a loopback port and a throwaway key. Cron jobs and kanban boards are created for the scenario and removed afterwards. To remove the profiles: `hermes profile delete scenarios` (and `scenarios-b`).

## Modes

| mode | how the turns run | what it covers |
|---|---|---|
| `cli` | `hermes -p scenarios chat -Q --yolo -q "<prompt>"` from a scratch working directory | one turn: agent → llm → api → tool spans, logs, events |
| `resume` | turn 1 as `cli`, every later turn as `chat -r <session>` in a new process | turn numbering across processes, one trace per turn, one session id |
| `gateway` | one `hermes gateway run` per scenario. Its `sessions` × `turns` are sent over `POST /v1/runs`, `concurrency` sessions at a time. `approval:` answers `/v1/runs/{id}/approval` with `once` or `deny`; `timeout` leaves it unanswered | many sessions in one long-lived process, approvals over HTTP |
| `cron` | `cron create` a one-shot job, then `cron run` it, then `cron rm` it | the `cron` root, `hermes.turn.trigger=cron`, `hermes.cron.job_id` |
| `kanban` | one card per profile on a scratch board, worked by `kanban dispatch` passes until each card finishes | `kanban.*` spans, one trace per run, workers in two profiles |

Every turn's prompt carries a **marker**: `<token>-<id>`, or `<token>-<id>-s<session>t<turn>` when a scenario has several turns. It is substituted for `{marker}` and appended as `(scenario marker: …)`, so each turn can be found by text in any backend. `{token}` and `{workdir}` are also available.

## Suites

| suite | what runs | needs |
|---|---|---|
| `quick` | the nine one-turn shapes (shell echo, no tools, file round-trip, failing tool, non-zero exit, several tools, long output, web fetch, skill load), plus parallel tool calls, a `git commit` through the terminal (`cli-git-commit`), a three-turn resumed session, events-only logs, the three content-capture modes and redaction | a model the profile can call |
| `full` | quick, plus `gateway-five-sessions`, the approval trio (granted, denied, timed out), `cron-job`, `kanban-two-profiles`, `delegate-one` and `api-error-fatal` | the second profile for kanban |
| `extra` | `delegation-batch`, `delegation-nested`, `moa-turn` | more tokens. `moa-turn` needs `NVIDIA_API_KEY` |

## Reading the report

```text
| scenario              | turn | live      | jaeger-local |
|-----------------------|------|-----------|--------------|
| shell-echo            | 1    | ✓ 5/1/8   | ✓ 5/1/–      |
| resume-three-turns    | s1t1 | ✓ 3/0/3   | ✓ 3/0/–      |
| resume-three-turns    | run  | ✓         | ✓            |
```

- **A turn cell** reads `spans/tools/logs` for that turn's trace in that source.
  - `–` means the source serves no logs.
  - `✗` means the trace was not found or an expectation failed. The findings list below the table says which.
- **A `run` row** is the scenario-level check across all of a scenario's turns.
- **A `– down` cell** is a source that didn't answer.

## Writing a scenario

Add an entry to `scripts/scenarios/scenarios.yaml`:

```yaml
- id: approval-denied
  mode: gateway
  suite: full
  why: the request answered "deny"; the approval span says so and the tool is blocked
  approval: deny
  prompt: "Run exactly this shell command in the terminal, as-is: rm -r scenario-scratch-{token}; echo {marker}-denied"
  expect:
    status: ok
    span_names_any: [approval.]
    span_attrs_any: {hermes.approval.choice: deny}
```

Other keys a scenario can carry:

- `turns:`: a list of prompts for `resume` and `gateway`. An item may be `{prompt, expect}` to override expectations for that turn.
- `config:`: deep-merged into the profile's `config.yaml` for this scenario.
- `otel:`: deep-merged into the profile's `hermes_otel.yaml` for this scenario.
- `enable_toolsets:`: turns on toolsets the profile disables.
- `hermes_args:`: added to the `chat` command line.
- `sessions` and `concurrency`: for `gateway`.
- `cron: {model, provider}`: pins the cron job's model.
- `kanban: {profiles: [a, b]}`: one card per profile.

Then run it with `make scenarios ONLY=<id>`. A scenario is ready once it passes on the live store and one backend.

### Expectations per turn (`expect:`)

| key | meaning |
|---|---|
| `status` | the root span's status, `ok` or `error` |
| `llm_calls_min` | at least this many `api.*` spans |
| `tools_min`, `tools_max` | bounds on `tool.*` spans |
| `tool_names_any` | some tool span's `tool.name` contains one of these |
| `tools_per_response_min` | at least this many tool calls after one `api.*` response (parallel calls; Hermes may run them one after another) |
| `tools_concurrent_min` | at least this many tool spans overlapped in time |
| `span_names_any`, `span_prefix_count_min` | span names |
| `output_contains` | some span attribute contains this text |
| `root_attrs` | `{key: value}` exact on the turn root (the outermost `agent` / `cron` span) |
| `span_attrs_any` | `{key: text}` some span carries the key with the text in its value (`""` = present) |
| `attrs_absent`, `attr_max_len` | privacy: keys never set, values never longer than |
| `text_absent` | the text appears in no span attribute and no log record |
| `logs_text_absent` | the text appears in no log record or event. Secrets are redacted on logs, not on span content |
| `subagent_roots_min`, `agent_depth_min`, `attr_values_min` | multi-agent shapes |
| `logs_min`, `log_levels_any` | log records correlated with the turn's trace |
| `events_min`, `events_max`, `event_names_any`, `logs_only_events` | structured events |
| `metrics_min` | `{instrument: n}` the instrument's total since the run started, in a source that serves metrics (metrics carry no trace id, so this is per run window, not per turn) |

### Expectations across turns (`expect_run:`)

| key | meaning |
|---|---|
| `turn_numbers` | `[1, 2, …]` the roots' `hermes.turn.number` per session |
| `same_session`, `sessions_distinct` | session ids |
| `traces_distinct` | every turn is its own trace |
| `distinct_root_attr_min` | `{key: n}` distinct values on the turn roots |
| `no_cross_session_logs` | no log record carries another turn's session id |
| `log_attribution_in` | every record's `hermes.log.attribution` is one of these |

## Multi-agent scenarios

The `extra` suite holds the costlier fleet shapes (#399):

| id | what it drives | what it expects |
|---|---|---|
| `delegation-batch` | one `delegate_task` with two tasks | two `subagent.*` spans, two rejoined child roots |
| `delegation-nested` | an orchestrator child that delegates to a leaf (`delegation.max_spawn_depth: 2`) | an `agent` tree three deep in one trace |
| `moa-turn` | a turn on a Mixture-of-Agents preset (`--provider moa -m cheap`) | two `moa_reference` and one `moa_aggregator` auxiliary `api.*` spans |

`delegate-one`, a single delegation, is in the `full` suite.

## Hook coverage

`scripts/scenarios/README.md` lists each of the plugin's 34 hooks, the scenario that drives it, and whether a failure there would fail a scenario. The ones no scenario drives:

- `on_session_reset`, `pre_command`, `agent_loop_stopped`: these need a messaging platform's slash commands or `/stop`.
- `on_human_input_request` / `on_human_input_resolved`: these need a human answering `clarify`.
- `pre_verify`: it fires only when Hermes's verification nudge triggers.
- `pre_transcription`: it needs a voice message.

The blocked and crashed kanban paths are covered by the [kanban demo](/development/kanban-demo).

## Related tools

- **`make refactor-check`** runs four `cli` scenarios (`shell-echo`, `no-tools`, `missing-file`, `multi-tool`) twice, once with the base ref's plugin and once with the working tree's, and diffs the shape of what landed (see [CONTRIBUTING](https://github.com/briancaffey/hermes-otel/blob/main/CONTRIBUTING.md)).
- **The dashboard verify harness** (`scripts/dashboard_verify`) uses the same scenarios as its workload.
