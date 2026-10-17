# Scenario harness

Real Hermes runs, each exercising a known slice of the hook contract, with
expectations checked against the plugin's live store and every configured
backend. It replaces `scripts/turn_batch` (#278). The docs page is
[Scenario harness](https://briancaffey.github.io/hermes-otel/development/scenarios).

```bash
make scenarios                       # quick suite: cli + resume turns, a few minutes
make scenarios-full                  # every mode: gateway, approvals, cron, kanban, sub-agents
make scenarios ONLY=shell-echo,resume-three-turns
make scenarios BACKENDS_FROM=        # live store only
```

Both targets run three steps you can also run by hand:

```bash
uv run --extra dev python scripts/scenarios/setup_profile.py [--backends-from default] \
    [--second-profile scenarios-b --second-provider nvidia \
     --second-model nvidia/nemotron-3-super-120b-a12b --copy-env NVIDIA_API_KEY]
uv run --extra dev python scripts/scenarios/run.py --suite quick|full|extra|all [--only ids]
uv run --extra dev python scripts/scenarios/check.py --manifest <profile home>/scenario_runs/<token>/runs.json
```

| file | role |
|---|---|
| `setup_profile.py` | creates or refreshes the `scenarios` Hermes profile: the model from another profile (`--model-from`, default `default`), this checkout's `hermes_otel/` as its plugin, a small toolset, manual approvals with a 20 s timeout, `gateway.standalone: true`, and optionally the backends of another profile (`--backends-from`) and a second profile for kanban (`--second-profile`) |
| `scenarios.yaml` | the library: one entry per scenario, with `why`, `mode`, `suite`, the prompt(s), overlays and expectations |
| `run.py` | drives Hermes; writes `runs.json`, every turn's stdout/stderr and each gateway's log under `<profile home>/scenario_runs/<token>/` |
| `check.py` | reads every source back through the plugin's own query CLI (`otel.py`), writes `report.md` / `report.json`, exits 1 on a hard failure |
| `common.py` | the scenario file, markers, overlays |

## Why a dedicated profile

The harness never touches your real profiles. The `scenarios` profile is its
own Hermes home under `~/.hermes/profiles/`, so:

- its plugin is the code in this checkout, copied fresh by every setup;
- a scenario's `config:` / `otel:` overlays are applied to that profile only and
  restored byte for byte afterwards;
- `gateway.standalone: true` keeps a multiplexed host gateway (`gateway.multiplex_profiles`)
  from serving it; gateway scenarios start their own `hermes -p scenarios gateway run`
  with the API server on a private loopback port and a throwaway key, and stop it afterwards;
- cron jobs and kanban boards are created in that profile and a scratch board
  file, then removed;
- only the variables its backends name (`${VAR}` in their entries) and any
  `--copy-env` names are copied into its `.env`: no bot token, and no `OTEL_*`
  variable that would switch on an extra exporter.

Delete it with `hermes profile delete scenarios` (and `scenarios-b`).

## Modes

| mode | how the turns run | what it covers |
|---|---|---|
| `cli` | `hermes -p scenarios chat -Q --yolo -q "<prompt>"`, CWD a scratch `workdir/` | one turn: agent → llm → api → tool spans, logs, events |
| `resume` | turn 1 as `cli`, every later turn `chat -r <session>` in a new process | turn numbering across processes, one trace per turn, the same session id |
| `gateway` | one gateway process per scenario, `sessions` × `turns` sent over `POST /v1/runs` (`concurrency` sessions at a time); `approval:` answers `/v1/runs/{id}/approval` with `once` / `deny`, or `timeout` leaves it unanswered | many sessions in one long-lived process, approvals over HTTP, `hermes.platform=api_server` |
| `cron` | `cron create` a one-shot job (prompt carries the marker), `cron run`, `cron rm` | the `cron` root, `hermes.turn.trigger=cron`, `hermes.cron.job_id` |
| `kanban` | one card per profile in `kanban.profiles` on a scratch board (`HERMES_KANBAN_DB`), `kanban dispatch` passes until every card is terminal | `kanban.*` spans, one trace per run, workers in different profiles |

Each turn's prompt gets a marker, `<token>-<id>` (or `<token>-<id>-s<session>t<turn>`
for multi-turn scenarios), substituted for `{marker}` and appended as
`(scenario marker: …)`, so `check.py` finds the turn by free-text search in any
source. Later turns of a session repeat earlier markers in their history and a
turn's title-generation call repeats its prompt, so the checker takes the
earliest turn root that carries the marker.

## Suites

| suite | scenarios | needs |
|---|---|---|
| `quick` (`make scenarios`) | the cli and resume scenarios | a model the profile can call |
| `full` (`make scenarios-full`) | quick + gateway, approvals, cron, kanban, `delegate-one`, `api-error-fatal` | the second profile for kanban (`--second-profile`) |
| `extra` | `delegation-batch`, `delegation-nested`, `moa-turn` | more tokens; `moa-turn` needs `NVIDIA_API_KEY` |

## Expectations

`expect:` is checked on every turn's trace in every source (a turn's own
`expect:` overrides keys); log keys only where the source serves logs.

| key | meaning |
|---|---|
| `status` | the parentless root's status, `ok` or `error` |
| `llm_calls_min` | at least this many `api.*` spans |
| `tools_min`, `tools_max` | bounds on `tool.*` spans |
| `tool_names_any` | some tool span's `tool.name` contains one of these |
| `tools_per_response_min` | at least this many tool spans started after one `api.*` span (the calls one response asked for) |
| `tools_concurrent_min` | at least this many tool spans overlapped in time |
| `span_names_any` | some span name contains one of these |
| `span_prefix_count_min` | `{prefix: n}` spans whose name starts with prefix |
| `output_contains` | some span attribute contains this text |
| `root_attrs` | `{key: value}` exact on the turn root (the outermost `agent` / `cron` span) |
| `span_attrs_any` | `{key: text}` some span has the key with the text in its value (`""` = present) |
| `attrs_absent` | no span sets these keys |
| `attr_max_len` | `{key: n}` no span's value is longer |
| `text_absent` | the text is in no span attribute and no correlated log record |
| `logs_text_absent` | the text is in no correlated log record or event (spans are not redacted, by design) |
| `subagent_roots_min`, `agent_depth_min`, `attr_values_min` | the multi-agent shapes (#399) |
| `logs_min`, `log_levels_any` | trace-correlated log records |
| `events_min`, `events_max`, `event_names_any` | records with an `event_name` |
| `logs_only_events` | every correlated record is an event (no stdlib log lines) |
| `metrics_min` | `{instrument: n}` the instrument's total since the run started, in a source that serves metrics (metrics carry no trace id, so per run window, not per turn) |

`expect_run:` is checked across a scenario's turns, per source:

| key | meaning |
|---|---|
| `turn_numbers` | `[1, 2, …]` the roots' `hermes.turn.number` per session, in turn order |
| `same_session` | every turn has the same `hermes.session_id` |
| `sessions_distinct` | at least this many distinct session ids |
| `traces_distinct` | every turn is its own trace |
| `distinct_root_attr_min` | `{key: n}` distinct values on the turn roots (`hermes.profile: 2`) |
| `no_cross_session_logs` | no correlated log record carries another turn's session id |
| `log_attribution_in` | every record's `hermes.log.attribution` is one of these |

A source that does not answer is skipped and named in the report, never
failed. A cell reads `spans/tools/logs`; `–` means the source serves no logs.

## Hook coverage

The plugin subscribes to 34 hooks (`hermes_otel/plugin.yaml`). **Asserted** means
a scenario's expectations fail if the hook stops producing its telemetry;
**fires** means the scenarios drive it but check nothing specific to it.

| hook | scenario | evidence checked |
|---|---|---|
| `on_session_start` | every scenario | asserted: the `agent` / `cron` turn root (`root_attrs`, `status`) |
| `on_session_end` | every scenario | asserted: the root ends with its status and turn summary (`hermes.turn.final_status` in `api-error-fatal`) |
| `on_session_finalize` | every cli turn (process exit), each gateway stop | fires |
| `on_session_reset` | none | not covered: needs `/new` or `/reset` on a messaging platform |
| `pre_llm_call` / `post_llm_call` | every scenario | asserted: `hermes.turn.number` (`resume-three-turns`, `gateway-five-sessions`), the `llm.*` span |
| `pre_api_request` / `post_api_request` | every scenario | asserted: `api.*` spans (`llm_calls_min`), content modes (`content-*`) |
| `api_request_error` | `api-error-fatal` | asserted: root status `error` |
| `pre_tool_call` / `post_tool_call` | `shell-echo` and every tool scenario | asserted: `tool.*` spans, names, call ids, outcomes; `cli-git-commit`: `hermes.tool.git_commit`, `hermes.commit.count`, the `commit` event |
| `transform_terminal_output` | every terminal scenario | asserted in `shell-echo`: `hermes.terminal.output.chars` on the tool span |
| `transform_llm_output` | every scenario | asserted in `shell-echo`: `hermes.llm.output.chars` on the root |
| `pre_auxiliary_call` / `post_auxiliary_call` | every first turn (title generation); `moa-turn` | asserted in `moa-turn` (`hermes.auxiliary.task`) |
| `subagent_start` / `subagent_stop` | `delegate-one`, `delegation-*` | asserted: `subagent.*` span, rejoined child root, depth |
| `pre_approval_request` / `post_approval_response` | `approval-granted`, `-denied`, `-timeout` | asserted: `approval.*` span, `hermes.approval.choice` / `timed_out` |
| `on_skill_lifecycle` | none asserted | only a metric (`skill_lifecycle_count`) when a skill is created, patched or used; the `skill.*` span `skill-load` asserts comes from the tool hooks |
| `kanban_task_claimed`, `kanban_task_completed`, `on_kanban_worker_spawned`, `on_kanban_worker_exited`, `on_kanban_dispatch_tick`, `on_kanban_task_updated` | `kanban-two-profiles` | asserted: `kanban.*` spans, one trace per run, two profiles |
| `kanban_task_blocked`, `on_kanban_worker_stale_claim` | none here | covered by `scripts/kanban_demo` (block, crash, runaway cards) |
| `agent_loop_stopped` | none | not covered: needs `/stop` on a running gateway turn |
| `pre_verify` | none | not covered: fires only when Hermes's verification nudge triggers |
| `pre_command` | none | not covered: slash commands on a messaging platform |
| `on_human_input_request` / `on_human_input_resolved` | none | not covered: needs the `clarify` tool answered by a human |
| `pre_transcription` | none | not covered: needs a voice message |

## Adding a scenario

1. Add an entry to `scenarios.yaml` with an `id`, a `why`, the `mode` and
   `suite`, a prompt that reliably produces the shape on a small model, and
   hard expectations. Put `{marker}` in text you expect to find back.
2. `make scenarios ONLY=<id>` (or `run.py --only <id>` then `check.py`).
3. A scenario is merged once it passes on the live store and one backend.
4. New expectation keys go in `check.py` (`evaluate` / `evaluate_run`), the
   tables above, and `tests/unit/test_scenarios.py`.
