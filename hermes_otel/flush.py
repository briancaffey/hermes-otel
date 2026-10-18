"""The coalesced background turn-end flush (#91, #233): :class:`Flusher` owns
the worker and the pending flag; the synchronous shutdown flush and the
per-reader provider flush are :meth:`Pipeline.force_flush` and
:meth:`Pipeline.force_flush_providers`.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any

from .debug_utils import debug_log


class Flusher:
    """One background worker that flushes a pipeline on request; requests
    coalesce while one is queued. Held by the plugin for its lifetime; the
    worker is dropped at shutdown and re-created on the next request."""

    def __init__(self) -> None:
        self._executor: Any = None
        self.pending = threading.Event()

    def flush_async(self, pipeline: Any, timeout_millis: int = 500) -> bool:
        """Flush every span processor of ``pipeline`` from a background thread (#91).

        The turn-end flush used to run on the hook thread, inside the agent
        loop, with a 2 s timeout per backend: one unreachable collector stalled
        every turn end by up to 2 s × (backends + 2). Now one worker thread
        flushes the span processors, then the metric and log providers, with a
        short timeout (the providers were left to their periodic readers until
        #233, which left one-shot runs with no metrics at all). Flushes coalesce: a
        request while one is queued is a no-op. Returns whether one was queued.
        """
        if pipeline is None or self.pending.is_set():
            return False
        self.pending.set()

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
                target=pipeline.force_flush_providers,
                args=(timeout_millis,),
                name="hermes-otel-flush-providers",
                daemon=True,
            )
            try:
                providers_thread.start()
                pipeline.force_flush(timeout_millis=timeout_millis, providers=False)
                providers_thread.join(timeout=max(0.05, timeout_millis / 1000.0 * 2))
            finally:
                self.pending.clear()
                debug_log("background flush: done")

        try:
            if self._executor is None:
                from concurrent.futures import ThreadPoolExecutor

                self._executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="hermes-otel-flush"
                )
            self._executor.submit(run)
            return True
        except Exception:
            self.pending.clear()
            return False

    def wait(self, timeout_s: float = 5.0) -> None:
        """Wait for a queued background flush to finish (tests, shutdown)."""
        deadline = time.perf_counter() + timeout_s
        while self.pending.is_set() and time.perf_counter() < deadline:
            time.sleep(0.005)

    def shutdown(self) -> None:
        """Drop the worker without waiting for a flush in flight."""
        executor, self._executor = self._executor, None
        if executor is not None:
            with contextlib.suppress(Exception):
                executor.shutdown(wait=False)


def flush_turn_end(plugin) -> None:
    """The flush at a turn's or a session's end: queue a background flush of
    the plugin's pipeline and wait for it at most ``config.force_flush_wait_ms``.

    From a background thread, waited on for a bounded time: a healthy
    collector gets the turn before the hook returns (a one-shot run may exit
    right after), a stuck one costs at most the wait (#91). ``0`` returns at
    once and leaves the flush to its thread.
    """
    plugin.flusher.flush_async(plugin.pipeline)
    wait_ms = plugin.config.force_flush_wait_ms
    if wait_ms and wait_ms > 0:
        plugin.flusher.wait(timeout_s=wait_ms / 1000.0)
