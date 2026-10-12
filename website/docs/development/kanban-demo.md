---
sidebar_position: 7
title: "Kanban demo"
description: "One command that works three cards on a scratch kanban board and reads every kanban span, metric point and event back from the live store and Jaeger."
---

# Kanban demo

`scripts/kanban_demo/run_demo.py` exercises Hermes' kanban board end to end and reads the telemetry back, so the [kanban spans](/architecture/span-hierarchy#kanban) can be checked against a real dispatcher and real workers without touching your own board.

```bash
# a backend running (see the backend pages); the profile's hermes_otel.yaml lists it
uv run --extra dev python scripts/kanban_demo/run_demo.py --profile minimal \
    --jaeger-url http://localhost:16686
```

## What happens

1. Every `hermes kanban` call is pointed at a **scratch board** (`HERMES_KANBAN_DB` under `scripts/kanban_demo/out/<token>/board/`). The real `~/.hermes/kanban.db` is never opened, and the gateway's embedded dispatcher, which ticks the real board, never sees these cards.
2. Four cards are created for the profile: **complete** (the worker calls `kanban_complete`), **block** (`kanban_block` with a reason), **crash** (the worker runs `kill -9 $PPID`, so the next dispatch pass finds it dead and `on_kanban_worker_exited` fires) and **runaway** (`sleep 900` under a `--max-runtime` cap, so the dispatcher kills the worker and the breaker auto-blocks the card; Hermes fires no hook for that, which the demo checks).
3. `hermes -p <profile> kanban dispatch --json` runs every few seconds until every card is `done` or `blocked`, which is what the gateway's dispatcher does every 60 s. The dispatcher-side hooks fire in those `dispatch` processes; the worker-side hooks fire in each spawned worker. All run under one profile, so one live store and one backend receive everything.
4. The profile's live store is read for the `kanban.*` spans, each worker trace whose root carries a demo card, the `hermes.kanban.task.count` points and the `hermes.kanban.transition` events. With `--jaeger-url`, every trace tagged with a card id is resolved and linked.
5. A fixed set of checks is scored and `report.md` written next to the board.

| check | what it proves |
|---|---|
| every card ends terminal | the board itself worked |
| `kanban.claim` root per card, `kanban.spawn` under it with a pid | the dispatcher-side hooks fire in the `dispatch` process and never nest under a session |
| the worker's turn is in the claim's trace (complete, block) | the dispatcher handed the run's context to the worker through `TRACEPARENT` (#388) |
| `kanban.complete` has a session and a parent; the worker's root is stamped `completed` | the worker-side hook landed inside the worker's own trace, findable by span-only readers |
| `kanban.block` is status OK with the reason | a blocked card is a hand-off, not an error |
| `kanban.worker_exit` fired for the crash card | a dead worker is noticed on the next dispatch pass (a CLI pass reports `exit_kind=unknown`; the gateway's dispatcher knows the kind and the span is then ERROR) |
| no `kanban.*` span for the runaway card | a runtime-cap kill and a breaker auto-block fire no Hermes hook; the board's event log carries them |
| `hermes.kanban.task.count` has claimed / completed / blocked points | the counter reaches the store from both processes |
| `hermes.kanban.transition` events recorded | the events pipeline carries the transitions (`capture_logs` on) |

## Reading it in a backend

Filter on `hermes.kanban.task_id`: it is a Resource attribute on every worker span and a span attribute on every `kanban.*` span, so one card's story is the dispatcher's claim and spawn, the worker's `agent → llm → api → tool.kanban_*` turn with its `kanban.complete` or `kanban.block`, and, for a killed worker, the dispatcher's `kanban.worker_exit`. A card attempted several times has one `hermes.kanban.run_id` per attempt.

The scratch board's SQLite file stays in the output directory; `HERMES_KANBAN_DB=<out>/board/kanban.db hermes kanban show <id>` reads a card's full event history. Options: `--runaway-seconds` (default 45), `--tick-seconds`, `--max-wait`, `--hermes-home`.
