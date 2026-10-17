"""Flushing: the synchronous shutdown flush, the per-reader provider flush and
the coalesced background turn-end flush (#91, #233).

Split out of ``tracer.py`` in #405; ``HermesOTelPlugin`` keeps thin methods
that delegate here.
"""

from __future__ import annotations

import threading
import time

from .debug_utils import debug_log


def force_flush(plugin, timeout_millis: int = 2000, providers: bool = True) -> None:
    """Force export of buffered spans (and, with ``providers``, metrics and logs).

    Synchronous. Called on process shutdown via atexit so a graceful exit
    loses nothing; the turn-end flush goes through :func:`flush_async`
    instead. Per-span flushing is deliberately NOT done — it would defeat
    the whole purpose of the BatchSpanProcessor queue.
    """
    for processor in list(plugin._span_processors):
        try:
            processor.force_flush(timeout_millis=timeout_millis)
        except Exception:
            pass
    if providers:
        plugin._force_flush_providers(timeout_millis)


def force_flush_providers(plugin, timeout_millis: int = 2000) -> None:
    """Flush the metric and log providers (their readers export synchronously).

    One thread per metric reader: ``MeterProvider.force_flush`` walks the
    readers in order and each export blocks for up to the exporter's
    deadline (10 s) when its backend is down, so with several backends
    configured and one of them unreachable a one-shot ``hermes -z`` exits
    before the reachable backends get their only export. Verified on
    2026-10-06 with an 11-backend config, four of them down: traces and
    logs landed on the local stacks, no metric point ever did.
    """
    readers = list(plugin._metric_readers)
    if plugin._meter_provider and len(readers) > 1:
        threads = []
        for reader in readers:

            def flush_one(r=reader) -> None:
                try:
                    r.force_flush(timeout_millis=timeout_millis)
                except Exception:
                    pass

            t = threading.Thread(target=flush_one, name="hermes-otel-flush-metrics", daemon=True)
            t.start()
            threads.append(t)
        deadline = time.monotonic() + max(0.05, timeout_millis / 1000.0)
        for t in threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))
    elif plugin._meter_provider:
        try:
            plugin._meter_provider.force_flush(timeout_millis=timeout_millis)
        except Exception:
            pass
    if plugin._logger_provider:
        try:
            plugin._logger_provider.force_flush(timeout_millis=timeout_millis)
        except Exception:
            pass


def flush_async(plugin, timeout_millis: int = 500) -> bool:
    """Flush every span processor from a background thread (#91).

    The turn-end flush used to run on the hook thread, inside the agent
    loop, with a 2 s timeout per backend: one unreachable collector stalled
    every turn end by up to 2 s × (backends + 2). Now one worker thread
    flushes the span processors, then the metric and log providers, with a
    short timeout (the providers were left to their periodic readers until
    #233, which left one-shot runs with no metrics at all). Flushes coalesce: a
    request while one is queued is a no-op. Returns whether one was queued.
    """
    if plugin._flush_pending.is_set():
        return False
    plugin._flush_pending.set()

    def run() -> None:
        debug_log("background flush: start")
        # Spans AND the metric / log providers (#233): a one-shot
        # ``hermes -z`` exits without atexit and never reaches the 60 s
        # metric tick, so the turn-end flush is the only export a
        # short-lived run gets. The providers flush on their own thread,
        # concurrently with the span processors, so the whole flush takes
        # about one export round-trip rather than the sum of them and fits
        # inside the hook's bounded ``force_flush_wait_ms``.
        providers_thread = threading.Thread(
            target=plugin._force_flush_providers,
            args=(timeout_millis,),
            name="hermes-otel-flush-providers",
            daemon=True,
        )
        try:
            providers_thread.start()
            plugin._force_flush(timeout_millis=timeout_millis, providers=False)
            providers_thread.join(timeout=max(0.05, timeout_millis / 1000.0 * 2))
        finally:
            plugin._flush_pending.clear()
            debug_log("background flush: done")

    try:
        executor = plugin._flush_executor
        if executor is None:
            from concurrent.futures import ThreadPoolExecutor

            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hermes-otel-flush")
            plugin._flush_executor = executor
        executor.submit(run)
        return True
    except Exception:
        plugin._flush_pending.clear()
        return False


def flush_wait(plugin, timeout_s: float = 5.0) -> None:
    """Wait for a queued background flush to finish (tests, shutdown)."""
    deadline = time.perf_counter() + timeout_s
    while plugin._flush_pending.is_set() and time.perf_counter() < deadline:
        time.sleep(0.005)


def flush_turn_end(plugin) -> None:
    """The flush at a turn's or a session's end: queue :func:`flush_async` and
    wait for it at most ``config.force_flush_wait_ms``.

    From a background thread, waited on for a bounded time: a healthy
    collector gets the turn before the hook returns (a one-shot run may exit
    right after), a stuck one costs at most the wait (#91). ``0`` returns at
    once and leaves the flush to its thread.
    """
    flush_async(plugin)
    wait_ms = plugin.config.force_flush_wait_ms
    if wait_ms and wait_ms > 0:
        flush_wait(plugin, timeout_s=wait_ms / 1000.0)
