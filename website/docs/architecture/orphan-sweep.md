---
sidebar_position: 6
title: "Orphan-span sweep"
description: "How the plugin cleans up stale sessions when on_session_end never fires — e.g. after a host crash mid-turn."
---

# Orphan-span sweep

What happens if Hermes crashes mid-turn, or the host is power-cycled, or the session-end hook never fires for some other reason?

Without cleanup, the root `agent` / `cron` span would stay open forever in the plugin's in-memory state, and in the backend UI you'd see a trace that starts but never ends. That's bad both operationally (active-span state leak) and visually (every orphaned turn is a "what's still running??" red herring).

The orphan sweep is a simple TTL-based cleanup that runs at the start of the hooks that begin work: `on_session_start`, `pre_llm_call`, `pre_api_request`, `api_request_error`, `pre_tool_call`, `pre_approval_request`, `subagent_start` and `pre_auxiliary_call`.

## How it works

`SpanTracker` keeps a turn registry: each open turn's session id and the time it started. Each of those hooks calls `sweep_expired_turns()` (in `tracer.py`), which asks the registry for every turn older than `root_span_ttl_ms` and finalizes it:

```python
# tracer.py (simplified)
def sweep_expired_turns(self):
    expired = self.spans.expired_turns(self.config.root_span_ttl_ms / 1000.0)
    for sid in expired:
        self._finalize_orphan(sid)   # ends open children, then the root
    self.sweep_expired_delegations()
    return expired
```

Any turn older than `root_span_ttl_ms` (default: 10 minutes) gets its open children ended first (see [below](#children-the-turn-left-open-477)), then its root gets:

1. `hermes.turn.final_status = "timed_out"` attribute
2. `StatusCode.OK` (not `ERROR` — timeouts shouldn't pollute error dashboards)
3. `span.end()` called, which enqueues it for export

Two exceptions: a turn the gateway stopped (`agent_loop_stopped`) is closed as `interrupted`, and a turn whose last API attempt failed (`api_request_error` with no success after it): Hermes ends that turn without `on_session_end`, so the sweep closes it as `final_status = "failed"` with `StatusCode.ERROR` and the attempt's `error.type` (#467).

Delegation spans (`subagent.*`) are the exception: they are not part of the turn and the turn sweep leaves them open, because Hermes delivers background delegations' results turns later. They have their own TTL, `subagent_span_ttl_ms` (default 24 hours), after which a delegation whose `subagent_stop` never arrived is ended with `hermes.subagent.swept=true` and `StatusCode.OK`.

Finalizing a session also drops everything the plugin buffered for it: its parent stack, open skill spans, delegation record and the per-session aggregator (token totals, previews, turn summary), so a crashed turn cannot pin memory for the life of a gateway process. The sweep never touches the parent stack of the session that happened to trigger it.

The sweep runs on the hot path but is O(n) in the number of currently-open turns, which is typically ~1.

## Configuration

```yaml
# ~/.hermes/hermes_otel.yaml
root_span_ttl_ms: 600000   # 10 minutes (default)
```

Or:

```bash
export HERMES_OTEL_ROOT_SPAN_TTL_MS=300000   # 5 minutes
```

Pick a TTL longer than your longest reasonable turn. A cron job that walks a large codebase might legitimately take 20+ minutes; set the TTL to ~1 hour if your agents have long-running turns.

## Children the turn left open (#477)

A root's children can outlive their own end hook: a tool whose `post_tool_call` never fires (the turn was interrupted mid-tool), an `api.*` span cut by a stream abort, an approval that timed out. Before 1.31.0 such a span stayed open past `on_session_end`, was never exported until the process shut down, and then carried no trace of what had happened. Now every path that closes a root first ends the children still open under it, before the root so the root still contains them, with `StatusCode.OK` (the sweep is not an error of the operation) and two attributes:

| Attribute | Value |
|---|---|
| `hermes.span.ended_by` | `sweep` |
| `hermes.span.sweep_reason` | `turn_end` (open at `on_session_end`) · `session_end` (open at finalize / reset) · `turn_failed` (every API attempt of the turn failed and the next turn closed it, #467) · `ttl` (this page's orphan sweep) · `shutdown` (the `atexit` handler) |

Each swept span adds one to `hermes.span.swept.count` (labels `span_kind` = the key prefix, `tool` / `api` / `llm` / `approval` / `skill`, and `reason`), so a fleet dashboard can alert when turns start leaving work behind. A span that ended normally carries neither attribute. Delegation spans (`subagent.*`) are not children of the turn for this purpose; see below.

## What it's NOT

- **Not a liveness check.** The sweep only triggers when another turn fires a hook. If Hermes is completely idle, a stale span stays open until the next user interaction wakes the sweeper.
- **Not a heartbeat.** There's no background thread polling for expiry. Keeps the plugin simple; the delay is bounded by how often hooks fire.
- **Not a replacement for crash handling.** The open spans live in the process's memory. On a hard crash (SIGKILL, OOM) `atexit` does not run, so the open root and everything still buffered in the `BatchSpanProcessor` queue are lost; a restarted process has no record of them and cannot close them. The sweep only helps a process that keeps running, such as a gateway, when a turn's end hook never arrives.

## Interaction with graceful shutdown

On graceful shutdown (SIGTERM, `hermes gateway stop`), the `atexit` handler:

1. Calls `SpanTracker.end_all()` — ends every still-open span, each marked `hermes.span.ended_by=sweep`, `hermes.span.sweep_reason=shutdown`.
2. Calls `force_flush()` on every `BatchSpanProcessor` so buffered spans get exported.

The orphan sweep isn't involved in graceful shutdown — it only runs on the hook hot path.

## Seeing it in action

In a long-running process (a gateway), a turn whose `on_session_end` never arrives shows up in the backend as a root with `hermes.turn.final_status = "timed_out"` and a duration a little over `root_span_ttl_ms`, once another turn fires one of the hooks above. Its leftover children carry `hermes.span.ended_by=sweep` and `hermes.span.sweep_reason=ttl`.

If it still shows as open:

- No hook has fired since the TTL passed (the sweep waits for the next one).
- The TTL is longer than the time since the turn started.
- With `HERMES_OTEL_DEBUG=true`, the debug log shows `swept <n> leftover span(s) of session=<id>: ttl` when the sweep ended children.
