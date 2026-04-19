"""Tests for the SpanTracker class in tracer.py."""

from unittest.mock import MagicMock

from hermes_otel.tracer import SpanTracker
from opentelemetry.trace import Status, StatusCode


class TestSpanTrackerBasics:
    def test_start_and_get_span(self):
        tracker = SpanTracker()
        span = MagicMock()
        tracker.start_span("tool:t1", span)
        assert tracker.get_span("tool:t1") is span

    def test_get_unknown_key_returns_none(self):
        tracker = SpanTracker()
        assert tracker.get_span("nonexistent") is None

    def test_end_span_calls_end(self):
        tracker = SpanTracker()
        span = MagicMock()
        tracker.start_span("tool:t1", span)
        tracker.end_span("tool:t1")
        span.end.assert_called_once()

    def test_end_span_removes_from_active(self):
        tracker = SpanTracker()
        span = MagicMock()
        tracker.start_span("tool:t1", span)
        tracker.end_span("tool:t1")
        assert tracker.get_span("tool:t1") is None

    def test_end_span_unknown_key_is_noop(self):
        tracker = SpanTracker()
        # Should not raise
        tracker.end_span("nonexistent")


class TestSpanTrackerAttributes:
    def test_end_span_sets_attributes(self):
        tracker = SpanTracker()
        span = MagicMock()
        tracker.start_span("k1", span)
        tracker.end_span("k1", attributes={"foo": "bar", "count": 42})
        span.set_attribute.assert_any_call("foo", "bar")
        span.set_attribute.assert_any_call("count", 42)

    def test_end_span_status_ok(self):
        tracker = SpanTracker()
        span = MagicMock()
        tracker.start_span("k1", span)
        tracker.end_span("k1", status="ok")
        span.set_status.assert_called_once()
        status_arg = span.set_status.call_args[0][0]
        assert status_arg.status_code == StatusCode.OK

    def test_end_span_status_error(self):
        tracker = SpanTracker()
        span = MagicMock()
        tracker.start_span("k1", span)
        tracker.end_span("k1", status="error", error_message="something broke")
        span.set_status.assert_called_once()
        status_arg = span.set_status.call_args[0][0]
        assert status_arg.status_code == StatusCode.ERROR
        assert status_arg.description == "something broke"

    def test_end_span_no_status_skips_set_status(self):
        tracker = SpanTracker()
        span = MagicMock()
        tracker.start_span("k1", span)
        tracker.end_span("k1")
        span.set_status.assert_not_called()


class TestSpanTrackerParentStack:
    def test_push_pop_parent(self):
        tracker = SpanTracker()
        span = MagicMock()
        tracker.push_parent(span)
        assert tracker.get_current_parent() is span
        tracker.pop_parent()
        assert tracker.get_current_parent() is None

    def test_nested_parents(self):
        tracker = SpanTracker()
        span1 = MagicMock(name="session")
        span2 = MagicMock(name="llm")
        tracker.push_parent(span1)
        tracker.push_parent(span2)
        assert tracker.get_current_parent() is span2
        tracker.pop_parent()
        assert tracker.get_current_parent() is span1

    def test_pop_empty_stack_is_noop(self):
        tracker = SpanTracker()
        tracker.pop_parent()  # Should not raise

    def test_get_current_parent_empty_returns_none(self):
        tracker = SpanTracker()
        assert tracker.get_current_parent() is None


class TestSpanTrackerEndAll:
    def test_end_all_ends_and_clears(self):
        tracker = SpanTracker()
        span1 = MagicMock()
        span2 = MagicMock()
        parent = MagicMock()
        tracker.start_span("k1", span1)
        tracker.start_span("k2", span2)
        tracker.push_parent(parent)

        tracker.end_all()

        span1.end.assert_called_once()
        span2.end.assert_called_once()
        assert tracker.get_span("k1") is None
        assert tracker.get_span("k2") is None
        assert tracker.get_current_parent() is None


class TestSpanTrackerConcurrencyIsolation:
    """Verify parent stack is isolated per task/thread.

    Concurrent sessions (e.g. cron + chat, or two chats) must not
    corrupt each other's span hierarchy via a shared parent stack.
    """

    def test_parent_stack_isolated_across_threads(self):
        import threading

        tracker = SpanTracker()
        main_parent = MagicMock(name="main")
        other_parent = MagicMock(name="other")
        seen_in_other_thread = []

        def _other_thread():
            # Other thread starts with empty stack (no leakage from main)
            seen_in_other_thread.append(("initial", tracker.get_current_parent()))
            tracker.push_parent(other_parent)
            seen_in_other_thread.append(("after_push", tracker.get_current_parent()))

        tracker.push_parent(main_parent)
        assert tracker.get_current_parent() is main_parent

        t = threading.Thread(target=_other_thread)
        t.start()
        t.join()

        assert seen_in_other_thread[0] == ("initial", None)
        assert seen_in_other_thread[1][1] is other_parent
        # Main thread's stack is untouched by the other thread
        assert tracker.get_current_parent() is main_parent

    def test_parent_stack_isolated_across_asyncio_tasks(self):
        """Two asyncio tasks interleaving on the same thread stay isolated.

        This is the scenario the old threading.local fix couldn't cover:
        multiple coroutines on the same event loop thread.
        """
        import asyncio

        tracker = SpanTracker()
        task_a_parent = MagicMock(name="task_a")
        task_b_parent = MagicMock(name="task_b")
        observations = {"a": [], "b": []}

        async def _task_a():
            tracker.push_parent(task_a_parent)
            # Yield control so task_b interleaves
            await asyncio.sleep(0)
            observations["a"].append(tracker.get_current_parent())
            await asyncio.sleep(0)
            observations["a"].append(tracker.get_current_parent())

        async def _task_b():
            # Task B starts with empty stack, not task A's
            observations["b"].append(tracker.get_current_parent())
            tracker.push_parent(task_b_parent)
            await asyncio.sleep(0)
            observations["b"].append(tracker.get_current_parent())

        async def _run():
            await asyncio.gather(_task_a(), _task_b())

        asyncio.run(_run())

        # Each task sees only its own parent — no cross-contamination
        assert observations["a"] == [task_a_parent, task_a_parent]
        assert observations["b"] == [None, task_b_parent]
