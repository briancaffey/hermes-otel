---
name: hermes-otel-kanban-demo
description: >-
  Exercise Hermes' kanban board end to end on a scratch board (a card that
  completes, one that blocks, one that runs away and is killed by its runtime
  cap) and read the resulting telemetry back from the plugin's live store and
  from Jaeger: the dispatcher-side kanban.claim / spawn / worker_exit spans,
  the worker-side kanban.complete / block spans inside each worker's trace,
  the hermes.kanban.task.count points and the hermes.kanban.transition events.
  One command, a report with a fixed set of checks. Use it after touching the
  kanban hooks, the resource attributes, or when showing what a kanban run
  looks like in a backend. Pairs with hermes-otel-backends and
  hermes-otel-dashboard-verify.
---

# Kanban telemetry demo

Hermes' kanban is a durable task board worked by **two kinds of process**: a
dispatcher (the gateway's embedded one, or `hermes kanban dispatch`) that
claims a ready card and spawns a worker, and the worker itself, a fresh
`hermes -p <assignee> chat` process that reads the card with `kanban_show`
and ends it with `kanban_complete` or `kanban_block`. The plugin observes six
hooks across those processes ([hooks reference](https://briancaffey.github.io/hermes-otel/reference/hooks#kanban-board-transitions)):

| process | hook | span |
|---|---|---|
| dispatcher | `kanban_task_claimed` | `kanban.claim` (own trace) |
| dispatcher | `on_kanban_worker_spawned` | `kanban.spawn` (own trace) |
| dispatcher | `on_kanban_worker_exited` | `kanban.worker_exit` (own trace; ERROR on crash or kill) |
| dispatcher | `on_kanban_worker_stale_claim` | `kanban.stale_claim` (own trace) |
| worker | `kanban_task_completed` | `kanban.complete` inside the worker's trace; root stamped |
| worker | `kanban_task_blocked` | `kanban.block` inside the worker's trace; root stamped |

The join across processes is `hermes.kanban.task_id` and
`hermes.kanban.run_id`, on every `kanban.*` span and on the worker's
Resource (from `HERMES_KANBAN_TASK` / `_BOARD` / `_RUN_ID`, #357).

## 1. The one command

```bash
# backend running (see hermes-otel-backends); the profile's hermes_otel.yaml
# lists it. The dispatcher AND the workers run under this profile, so one live
# store and one backend receive everything.
uv run --extra dev python scripts/kanban_demo/run_demo.py --profile minimal \
    --jaeger-url http://localhost:16686
```

Output: `scripts/kanban_demo/out/<token>/report.md` (also printed), with the
scratch board's SQLite file next to it. Exit code 1 when a check failed.
About three minutes: two short worker turns plus the runaway card's runtime
cap (`--runaway-seconds`, default 45) and the dispatcher pass that notices.

## 2. What it does

1. Points every `hermes kanban` call at a **scratch board** through
   `HERMES_KANBAN_DB` and `HERMES_KANBAN_WORKSPACES_ROOT` under the output
   directory. The real board (`~/.hermes/kanban.db`) is never opened. The
   gateway's own dispatcher ticks the real board only, so it never sees these
   cards.
2. Creates three cards assigned to the profile, each with `--max-retries 1`:
   `complete` (call `kanban_complete`), `block` (call `kanban_block`), and
   `runaway` (`sleep 900`, with `--max-runtime` so the dispatcher's next pass
   kills the worker and the breaker auto-blocks the card).
3. Runs `hermes -p <profile> kanban dispatch --json` every `--tick-seconds`
   until all three cards are terminal (`done` / `blocked`), exactly what the
   gateway's embedded dispatcher does every 60 s.
4. Reads the profile's live store for everything since the start: the
   `kanban.*` spans (with parent, status, pid, exit kind), each worker trace
   whose root carries a demo card, the `hermes.kanban.task.count` points by
   outcome and board, and the `hermes.kanban.transition` events. With
   `--jaeger-url`, asks Jaeger for every trace tagged with each card's id and
   links them.
5. Scores the checks in the table below and writes the report.

## 3. The checks

| check | why |
|---|---|
| every card ends terminal | the board itself worked (dispatcher spawned, workers ran) |
| `kanban.claim` and `kanban.spawn` roots per card, spawn carries a pid | the dispatcher-side hooks fire in the `dispatch` process and never nest under a session |
| `kanban.complete` has a session and a parent | the worker-side hook landed inside the worker's own trace |
| the `complete` worker's root is stamped `completed` | span-only readers (the dashboard's live store) can find the card's turn |
| `kanban.block` is status OK with the reason | a blocked card is a human hand-off, not an error |
| `kanban.worker_exit` fired for the runaway card, as an ERROR span | the kill after the runtime cap is visible, and counts as a failure |
| `hermes.kanban.task.count` has claimed / completed / blocked points | the counter reaches the live store from both processes |
| `hermes.kanban.transition` events recorded | the events pipeline carries the transitions (needs `capture_logs` on) |

## 4. Reading the report against a backend

The report's worker traces are ordinary `agent → llm → api → tool.kanban_*`
trees with the extra `kanban.complete` / `kanban.block` span, so any backend
shows them like every other turn. Filter on `hermes.kanban.task_id` (a
Resource attribute on the worker, a span attribute on every `kanban.*` span)
to see one card's whole story: the dispatcher's claim and spawn, the worker's
turn, and for the runaway card the dispatcher's `worker_exit`. Jaeger: the
tag search `hermes.kanban.task_id=<id>` returns all of them.

## 5. When it is not clean

- **Cards never leave `ready`:** the profile's worker spawn failed; `hermes
  kanban show <id>` on the scratch board (`HERMES_KANBAN_DB=<out>/board/kanban.db`)
  shows the `spawn_failed` event and its error.
- **No `kanban.*` spans at all:** the profile's plugin dir is not this
  branch; sync it (`rsync -a --delete --exclude __pycache__ hermes_otel/
  ~/.hermes/profiles/<profile>/plugins/hermes_otel/`).
- **`worker_exit` missing for the runaway card:** the cap had not expired
  before the last pass; raise `--max-wait` or lower `--runaway-seconds`.
- **No events:** `capture_logs` is off for the profile; spans and metrics
  still prove the hooks.
