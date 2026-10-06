"""The turn-end flush exports every metric reader concurrently (#300 follow-up).

``MeterProvider.force_flush`` walks the readers in order and each export can
block for the exporter's deadline when its backend is down; a one-shot run
with one dead backend then exits before the reachable ones export.
"""

from __future__ import annotations

import time

from hermes_otel.tracer import HermesOTelPlugin


class _Reader:
    def __init__(self, delay: float, calls: list) -> None:
        self.delay = delay
        self.calls = calls

    def force_flush(self, timeout_millis: int = 2000) -> bool:
        self.calls.append((self.delay, time.monotonic()))
        time.sleep(self.delay)
        return True


class _Provider:
    def __init__(self) -> None:
        self.flushed = False

    def force_flush(self, timeout_millis: int = 2000) -> bool:
        self.flushed = True
        return True


def test_each_metric_reader_flushes_on_its_own_thread():
    plugin = HermesOTelPlugin.__new__(HermesOTelPlugin)
    calls: list = []
    slow, fast = _Reader(1.5, calls), _Reader(0.0, calls)
    plugin._metric_readers = [slow, fast]
    plugin._meter_provider = _Provider()
    plugin._logger_provider = None
    t0 = time.monotonic()
    plugin._force_flush_providers(timeout_millis=300)
    elapsed = time.monotonic() - t0
    # both readers were asked to flush right away, the slow one did not hold the fast one back
    assert sorted(d for d, _ in calls) == [0.0, 1.5]
    started = [t for _, t in calls]
    assert max(started) - min(started) < 0.2
    # and the caller waited for the budget, not for the slow backend
    assert elapsed < 1.0
    assert (
        plugin._meter_provider.flushed is False
    )  # per-reader path, not the sequential provider walk


def test_a_single_reader_still_uses_the_provider_flush():
    plugin = HermesOTelPlugin.__new__(HermesOTelPlugin)
    plugin._metric_readers = [_Reader(0.0, [])]
    plugin._meter_provider = _Provider()
    plugin._logger_provider = None
    plugin._force_flush_providers(timeout_millis=100)
    assert plugin._meter_provider.flushed is True


def test_logger_provider_is_flushed_too():
    plugin = HermesOTelPlugin.__new__(HermesOTelPlugin)
    plugin._metric_readers = []
    plugin._meter_provider = None
    plugin._logger_provider = _Provider()
    plugin._force_flush_providers(timeout_millis=100)
    assert plugin._logger_provider.flushed is True
