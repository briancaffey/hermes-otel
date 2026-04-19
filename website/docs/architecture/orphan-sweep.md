---
sidebar_position: 6
title: "Orphan-span sweep"
description: "How the plugin cleans up stale sessions when on_session_end never fires — e.g. after a host crash mid-turn."
---

# Orphan-span sweep

What happens if Hermes crashes mid-turn, or the host is power-cycled, or the session-end hook never fires for some other reason?

Without cleanup, the root `session.*` span would stay open forever in the plugin's in-memory state, and in the backend UI you'd see a trace that starts but never ends. That's bad both operationally (active-span state leak) and visually (every orphaned turn is a "what's still running??" red herring).

The orphan sweep is a simple TTL-based cleanup that runs at the top of **every** `pre_*` hook.

## How it works

The `SpanTracker` keeps a dict of open root spans, each with its start timestamp. On every `pre_*` hook (there's always at least one per turn), the tracker scans the dict:

```python
for session_id, span_info in list(self._roots.items()):
    age_ms = now_ms() - span_info.started_at_ms
    if age_ms > self.root_span_ttl_ms:
        # Finalize this orphan
        span_info.span.set_attribute("hermes.turn.final_status", "timed_out")
        span_info.span.end()
        del self._roots[session_id]
```

Any root older than `root_span_ttl_ms` (default: 10 minutes) gets:

1. `hermes.turn.final_status = "timed_out"` attribute
2. `StatusCode.OK` (not `ERROR` — timeouts shouldn't pollute error dashboards)
3. `span.end()` called, which enqueues it for export

The sweep runs on the hot path but is O(n) in the number of currently-open sessions, which is typically ~1.

## Configuration

```yaml
# config.yaml
root_span_ttl_ms: 600000   # 10 minutes (default)
```

Or:

```bash
export HERMES_OTEL_ROOT_SPAN_TTL_MS=300000   # 5 minutes
```

Pick a TTL longer than your longest reasonable turn. A cron job that walks a large codebase might legitimately take 20+ minutes; set the TTL to ~1 hour if your agents have long-running turns.

## What it's NOT

- **Not a liveness check.** The sweep only triggers when another turn fires a hook. If Hermes is completely idle, a stale span stays open until the next user interaction wakes the sweeper.
- **Not a heartbeat.** There's no background thread polling for expiry. Keeps the plugin simple; the latency is bounded by the next-hook cadence which is at most a few seconds under real load.
- **Not a replacement for crash handling.** Buffered spans in the `BatchSpanProcessor` queue are lost on a hard crash (SIGKILL, OOM) — `atexit` doesn't run. The orphan sweep finalizes the root *once a later process reboots* and a new hook fires, so if you process-restart, the previous process's orphan gets cleaned up by the new one — but its span is already gone with the queue.

## Interaction with graceful shutdown

On graceful shutdown (SIGTERM, `hermes gateway stop`), the `atexit` handler:

1. Calls `SpanTracker.end_all()` — finalizes any still-open roots with `final_status=incomplete`.
2. Calls `force_flush()` on every `BatchSpanProcessor` so buffered spans get exported.

The orphan sweep isn't involved in graceful shutdown — it only runs on the hook hot path.

## Seeing it in action

The simplest way to verify: start Hermes, begin a turn that does nothing (just sits at a prompt), hard-kill the process, restart Hermes, start a new turn. A few seconds after the new turn's first hook fires, check the backend UI — you should see the previous orphan showing up as `completed`... wait, let me correct that — as `timed_out` with a ~TTL-ish duration.

If it's showing up as indefinitely open, either:

- The new turn hasn't fired a `pre_*` hook yet (wait a moment, do something)
- The TTL is too long (the orphan is still within the grace window)
- Debug log will show the sweep output: `[hermes-otel] swept 1 orphan root(s)`
