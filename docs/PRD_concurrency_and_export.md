# PRD: Concurrency safety & non-blocking export

## Context

Two related problems in the current plugin:

1. **Span cross-contamination under concurrency** (critical bug, partially fixed)
   A cron job running in one thread and a chat request in another were sharing a module-level `SpanTracker._parent_stack`, causing cron's `llm`/`api`/`tool` spans to nest under the chat's span hierarchy. We shipped a `threading.local` fix in tracer.py that covers today's hermes concurrency model (thread-pool executors). But if hermes ever runs multiple conversations as asyncio coroutines on the same thread, or moves to `asyncio.TaskGroup`, the threading.local fix is insufficient — all coroutines on the same thread would share the stack again.

2. **Synchronous export blocks the agent loop** (performance)
   We use `SimpleSpanProcessor` which exports spans synchronously on `span.end()`. Every hook that ends a span (tool call, API request, LLM turn) waits for the HTTP POST to the backend to complete. LangSmith is even worse — it makes a synchronous `urllib` POST in the hook itself. A slow or unreachable backend adds latency to every user interaction. If Phoenix/Langfuse is briefly unreachable, hermes stalls.

## Goals

- **G1**: Span isolation works under any Python concurrency model (threads, asyncio, task groups).
- **G2**: Span export never blocks the hermes agent loop on a healthy system.
- **G3**: Zero span loss on graceful shutdown; best-effort on crash.
- **G4**: No observable changes to span/trace content — only when/how they're exported.

## Non-goals

- gRPC OTLP transport.
- Changes to attribute names, span structure, or hook APIs.
- LangSmith batch ingestion API (its per-run HTTP model is what it is).

## Design

### Part 1: Replace `threading.local` with `contextvars.ContextVar`

`contextvars` is Python's standard mechanism for per-task state. It works across threads **and** asyncio coroutines: each async task gets its own copied context on creation. The OTel SDK already uses contextvars for its own context propagation, so this aligns with the rest of the stack.

**Change**: In `tracer.py::SpanTracker`, replace the `threading.local` parent stack with a `ContextVar[list]`:

```python
import contextvars

_PARENT_STACK: contextvars.ContextVar[list] = contextvars.ContextVar(
    "hermes_otel_parent_stack", default=None
)

class SpanTracker:
    def _parent_stack(self) -> list:
        stack = _PARENT_STACK.get()
        if stack is None:
            stack = []
            _PARENT_STACK.set(stack)
        return stack
```

A ContextVar default of `None` (not `[]`) is important — a mutable default shared across contexts would defeat the purpose.

**Isolation semantics**:
- `threading.Thread(target=...)` — child thread copies parent's context at creation; mutations to the stack in the child don't leak back. ✓
- `asyncio.create_task(coro)` — new task copies parent's context. ✓
- `loop.run_in_executor(...)` — executor threads see main thread's context at submission time. The stack is copy-on-write, so mutations stay in the executor thread. ✓

### Part 2: Switch to `BatchSpanProcessor` for OTLP export

Replace `SimpleSpanProcessor` with `BatchSpanProcessor` in `_init_otlp`. BatchSpanProcessor runs an internal worker thread that drains a queue and exports spans in batches. The agent's `span.end()` call becomes a non-blocking enqueue.

**Tunables** (sane defaults, all env-overridable):
- `max_queue_size=2048` — drops spans if agent outruns exporter.
- `schedule_delay_millis=1000` — flushes at most once per second.
- `max_export_batch_size=512` — one batch per HTTP POST.
- `export_timeout_millis=30000` — per-export HTTP timeout.

**Flush-on-shutdown**: BatchSpanProcessor's worker holds spans in memory until flushed. We already call `provider.force_flush()` in `_force_flush()`, but we need to ensure it runs at the right moments:

1. **Session end** — `on_session_end` hook should call `tracer._force_flush()` so a user who watches Phoenix after a conversation sees their trace promptly. This matches current behavior (SimpleSpanProcessor exports on each span end, so the last span is exported before the session hook returns).
2. **Process shutdown** — register `atexit.register(tracer._force_flush)` so a graceful `hermes gateway stop` doesn't lose pending spans.

### Part 3: Non-blocking LangSmith HTTP

LangSmith doesn't use OTLP; it has its own `POST /runs` / `PATCH /runs/{id}` API. Currently the HTTP call happens synchronously inside `start_span` / `end_span`, blocking the agent hook. Each tool call adds two round-trips to api.smith.langchain.com.

**Change**: Move HTTP I/O into a background queue worker thread.

- `LangSmithBackend.__init__` starts a daemon thread that consumes from a `queue.Queue`.
- `start_span` / `end_span` enqueue `("POST", path, payload)` / `("PATCH", path, payload)` tuples and return immediately.
- The worker drains the queue, does the HTTP call, logs errors via `debug_log`.
- `flush()` method waits for the queue to drain (bounded timeout).
- `_force_flush()` in tracer.py calls `self._langsmith.flush()` when LangSmith is active.

**Ordering**: The queue preserves FIFO order, so PATCH-after-POST for the same run_id is guaranteed to arrive in order. Runs are correlated by `id` on the server side, so inter-span ordering doesn't matter.

**Backpressure**: Use a bounded queue (`maxsize=1000`). If full, the hook drops the event and logs a warning — agent keeps running.

## Implementation plan

### Phase 1 — contextvars (foundation)
Low-risk refactor of `SpanTracker`. Replace `threading.local` with `ContextVar`. Update existing thread-isolation test to also cover asyncio isolation. ~1 file change.

### Phase 2 — BatchSpanProcessor
Update `_init_otlp` to use `BatchSpanProcessor`. Add `atexit` handler. Update `on_session_end` in `hooks.py` to call `_force_flush()` so users see their trace promptly (without it, they'd wait up to `schedule_delay_millis`). Update integration tests — they already use `SimpleSpanProcessor` in the fixture which keeps them fast and deterministic, no change needed there. ~2 file changes.

### Phase 3 — Async LangSmith worker
Add queue + daemon thread to `LangSmithBackend`. Add `flush()`. Add unit tests: verify enqueue returns immediately, worker drains in order, flush waits for drain, bounded queue drops on overflow. ~2 file changes (backend + test).

### Phase 4 — Validation
- Run full unit + integration suite.
- Run smoke tests (hermes API server + Phoenix + Langfuse).
- Manually verify: start a long-running cron job, send a chat simultaneously, confirm traces are separate in Phoenix.
- Measure: before/after median hook duration (`pre/post_api_request`) to confirm non-blocking.

## Risks & mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Span loss on crash (BatchSpanProcessor) | Last ~1s of spans missing from UI | Acceptable — matches industry norm. Users can lower `schedule_delay_millis` if critical. |
| Queue overflow drops spans | Missing tool spans under extreme load | Default `maxsize=2048` is ample for hermes. Log warnings when dropping. |
| Tests that assert immediate export | False failures | Integration tests already use their own in-memory exporter setup — no impact. |
| LangSmith worker thread leak on test teardown | Resource warnings | Add `flush(timeout=2)` + thread join in `end_all()` cleanup. |
| Forgetting `force_flush` in session_end | 1s delay before trace visible in UI | Low severity — UI naturally refreshes. Ship anyway to match today's feel. |

## Verification

### Unit tests
- `test_tracer_span_tracker.py`: extend thread-isolation test to cover asyncio tasks via `asyncio.run(gather(task1, task2))`.
- `test_langsmith_backend.py`: add tests for queue ordering, flush behavior, bounded queue drops.

### Integration tests
- Existing tests (InMemorySpanExporter) already pass without change.
- Add a new test: concurrent `asyncio.gather(session_a, session_b)` with separate session_ids, verify their spans have correct parent chains.

### Smoke tests
- Re-run `tests/smoke/test_hermes_phoenix.py` against live hermes.
- Manual test: start a cron job that runs for 30+ seconds, send a chat, confirm the chat's trace in Phoenix has only chat spans, cron's trace has only cron spans.

## Files to modify

- `tracer.py` — `SpanTracker` uses `ContextVar`; `_init_otlp` uses `BatchSpanProcessor`; register `atexit` flush.
- `hooks.py` — `on_session_end` calls `tracer._force_flush()` explicitly.
- `langsmith_backend.py` — add worker thread, queue, `flush()`.
- `tests/unit/test_tracer_span_tracker.py` — add asyncio isolation test.
- `tests/unit/test_langsmith_backend.py` — add async queue tests.
- `tests/integration/test_span_hierarchy.py` — add concurrent-session test.
