---
sidebar_position: 1
title: Observing a fleet
description: "Several Hermes agents at once — delegated children, kanban workers on other profiles and models, a swarm, Mixture-of-Agents, cron — and what each one looks like in a trace backend: the attribute that identifies it, the span it hangs from, and how to ask what it cost."
---

# Observing a fleet

Hermes runs more than one agent in several ways: a turn delegates to children, a kanban board hands cards to workers that are separate processes on other profiles and models, a swarm chains workers, a reviewer and a synthesizer, a Mixture-of-Agents turn consults reference models, and cron runs jobs with nobody watching. This page says how each one shows up in your backend, so one query answers "what did this agent do, on which model, and what did it cost".

## The surfaces at a glance

| Surface | What it is | Identified by | Hangs from | Links to its parent by | Not linked yet |
|---|---|---|---|---|---|
| Profile | one agent's home: config, model, skills, plugins | `hermes.profile` on the root and the Resource; `profile` on every metric; `gen_ai.agent.name` on the root; `service.name` with [`service_name_from_profile`](/configuration/profiles#one-service-per-profile) | its own turn roots | — | — |
| `delegate_task` child | a child agent in the same process (background by default) | `subagent.{role}` span; the child root has `hermes.session.is_subagent=true`, `hermes.turn.trigger=subagent` | the parent turn's in-flight span | being nested: one trace for the whole tree, including nested orchestrators | a child in another process gets a span link only |
| Kanban worker | a separate `hermes -p <assignee>` process per card run | `hermes.kanban.task_id` / `run_id` on the root and the Resource, `hermes.turn.trigger=kanban` | the dispatcher's `kanban.claim` (`TRACEPARENT` hand-off) | one trace per run: claim → spawn → worker turn → worker exit | the dependency between two cards (a swarm's worker → verifier) is in the board, not the trace |
| Kanban swarm | workers → verifier → synthesizer, as cards | each card is a kanban run above | each run's own `kanban.claim` | — | the swarm as one trace |
| Mixture-of-Agents | references + an aggregator around the main model | `api.*` spans with `hermes.query_source=auxiliary`, `hermes.auxiliary.task=moa_reference` / `moa_aggregator` | the turn's `llm.*` span | being nested | — |
| Cron | a scheduled job run in-process | root named `cron`, `hermes.cron.job_id`, `hermes.turn.trigger=cron` | its own root | — | — |
| A2A inbound | another agent calling this one | `hermes.turn.trigger=a2a` | its own root | — | the caller's trace (needs an upstream hook, #173) |
| `message_agent` / rooms | bot-to-bot messages | `hermes.turn.trigger=user` today | its own root | — | the sender (needs upstream, #444) |

Every turn root also says **why it ran** (`hermes.turn.trigger`: `user`, `cron`, `kanban`, `subagent`, `a2a`, `api`, `oneshot`, `curator`, `batch`, `system`) and **whether it ran on the profile's default model** (`hermes.agent.model_override`).

## A delegation tree

A parent turn that delegates is one trace:

```text
agent                                  the parent turn (its own calls only)
└── llm.<model>
    ├── tool.delegate_task
    ├── subagent.orchestrator          gen_ai.request.model, hermes.subagent.model_inherited,
    │   │                              hermes.subagent.usage.* / cost.* for its whole subtree
    │   └── agent                      the child (hermes.turn.trigger=subagent)
    │       └── llm.<model>
    │           ├── tool.delegate_task
    │           └── subagent.leaf
    │               └── agent          the grandchild
    └── api.<model>
```

- **What a child cost:** read `hermes.subagent.usage.*` and `hermes.subagent.cost.*` on its `subagent.*` span. For an orchestrator they include every delegation it made, so the whole run costs the parent root's own figures plus its top-level `subagent.*` spans. Never add the parent root to every span below it.
- **Which model a child used:** `gen_ai.request.model` on the `subagent.*` span. `hermes.subagent.model_inherited=false` means `delegation.model` pinned it to another model.
- **Background delegation:** a `delegate_task` that returns at once and delivers its result in a later turn keeps its `subagent.*` span open across those turns. It ends on `subagent_stop`, so in a timeline it runs past the end of the turn root it hangs from. That is the delegation's real duration.

## A kanban run

Each run of a card is one trace across two processes:

```text
kanban.claim                       the dispatcher (the run's root)
├── kanban.spawn
├── agent                          the worker process: its own hermes.profile and gen_ai.request.model
│   └── llm.<model> → api.* / tool.* / kanban.complete
└── kanban.worker_exit             when the same dispatcher process sees it exit
```

Board health is metrics, not spans:
- `hermes.kanban.dispatch.count{outcome}` counts dispatcher passes.
- `hermes.kanban.dispatch.result{kind}` counts what they did: spawned, crashed, timed out, auto-blocked.
- `hermes.kanban.task.updated{field}` counts edits.

## Recipes

**Spend per profile.**
- *Metrics (Prometheus-style backends):* `sum by (profile) (increase(hermes_token_usage_total[1h]))` for tokens, and `hermes_cost_usage_USD_total` for USD where Hermes priced the calls (the unit is part of the Prometheus name, as for `claude_code_cost_usage_USD_total`). Add `query_source` to split main calls from sub-agent and auxiliary calls.
- *Spans:* group turn roots (`agent`, `cron`) by `hermes.profile` and sum their `gen_ai.usage.*`.

**One service per agent.** Set `service_name_from_profile: true` in every profile's `hermes_otel.yaml` and each named profile exports as its own `service.name` under `service.namespace=hermes-agent`. Service pickers (Jaeger, Tempo, SigNoz, Uptrace) then list `coder`, `reviewer` … instead of one `hermes-agent`, and a kanban run's trace crosses services: the dispatcher's profile owns `kanban.claim`, the assignee's profile owns the worker's `agent` root. See [Profiles](/configuration/profiles#one-service-per-profile) for what changes in saved queries.

**Cost per agent.** In the [dashboard](/dashboard#cost-and-tokens-per-agent), open a turn that delegated: the **by agent** table lists the parent's own calls, each top-level delegation (role, model, tokens, API calls, cost) and the whole run. Live cards show `+tokens · N sub-agents` next to the turn's own figures. Elsewhere, read `hermes.subagent.usage.*` / `cost.*` on the top-level `subagent.*` spans and add the root's own `gen_ai.usage.*`; on metrics, `sum by (query_source) (increase(hermes_token_usage_total[1h]))` splits `main` from `subagent` calls.

**Spend per kanban card.**
- *Jaeger:* search tags `hermes.kanban.task_id=<id>`. Each run is one trace, and the worker's `agent` root carries the run's totals.
- *Tempo:* the TraceQL `{ span.hermes.kanban.task_id = "<id>" && name = "agent" }`.

**One turn's delegation tree.** Open the parent's trace. The `subagent.*` spans and the children's `agent` roots are in it, nested. In the [dashboard](/dashboard), the trace detail shows the same tree.

**Models in a mixed fleet.** Group by `gen_ai.request.model` on turn roots, or on `subagent.*` spans for delegated children. `hermes.agent.model_override=true` marks every turn not on its profile's `model.default`: a cheap worker profile's `--model`, a cron job's own model, `-m`, `/model`.

### Join an outer trace from any launcher

A CI job, a shell script or another agent that runs `hermes` can make the turn a child of its own span by exporting `HERMES_OTEL_TRACEPARENT` (and, if it has one, `HERMES_OTEL_TRACESTATE`) before the call; the root then carries `hermes.trace.remote_parent=true` and the whole run shows up inside the launcher's trace (#474). The value is the W3C `traceparent` of the launcher's current span, which every OpenTelemetry SDK can print (`trace_id` and `span_id` of the current span context, version `00`, flags `01`):

```bash
export HERMES_OTEL_TRACEPARENT="00-<32 hex trace id>-<16 hex span id>-01"
hermes -z "Summarise yesterday's failures"
```

Export the launcher's own spans to the same backend and the Hermes `agent` root nests under them. A malformed value is reported once and ignored; `remote_parent: false` switches the feature off. This is separate from the kanban hand-off above, which uses plain `TRACEPARENT` and only inside a worker.

## Run it yourself

`scripts/fleet_demo/run_demo.py` builds a three-profile fleet on a scratch board and reads it back from Jaeger:
1. It clones `minimal` into `fleet-worker` and `fleet-reviewer`, each on its own NVIDIA model.
2. It runs `hermes kanban swarm` (two workers → reviewer → synthesizer) through `hermes kanban dispatch`.
3. It checks that every card's run is one trace, and that each worker root names its own profile and model.
4. It deletes the profiles it created (`--keep-profiles` keeps them).

The swarm's synthesizer card asks for the `humanizer` skill. In a profile without it, that worker exits before its first turn, and the report says so with the worker's last output. Its run still has claim and spawn in one trace, but no turn to join. For delegation and MoA turns, see the multi-agent scenarios in the [scenario harness](/development/scenarios#multi-agent-scenarios).

```bash
docker compose -f docker-compose/jaeger/docker-compose.yaml up -d
uv run --extra dev python scripts/fleet_demo/run_demo.py --jaeger-url http://localhost:16686
```

## What the GenAI conventions call these

| Convention | Here |
|---|---|
| `invoke_agent` | the turn root, a `subagent.*` delegation, a kanban worker's turn; `gen_ai.agent.name` is the named profile on a root, the role on a `subagent.*` span |
| `invoke_workflow` | not emitted: a kanban board, a swarm and a room have no single span that spans them |
| `create_agent` | not applicable: Hermes creates agents in-process, not through a remote agent service |
