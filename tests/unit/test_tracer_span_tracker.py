"""Tests for the SpanTracker class in tracer.py."""

from unittest.mock import MagicMock

from opentelemetry.trace import Status, StatusCode

from hermes_otel.tracer import SpanTracker


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


class TestSpanTrackerThreadIsolation:
    """Verify parent stack is isolated per thread.

    Concurrent sessions (e.g. cron + chat) must not corrupt each other's
    span hierarchy via a shared parent stack.
    """

    def test_parent_stack_is_thread_local(self):
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

        # Main thread pushes its parent
        tracker.push_parent(main_parent)
        assert tracker.get_current_parent() is main_parent

        t = threading.Thread(target=_other_thread)
        t.start()
        t.join()

        # Other thread saw its own isolated stack, not main's
        assert seen_in_other_thread[0] == ("initial", None)
        assert seen_in_other_thread[1][1] is other_parent

        # Main thread's stack is untouched by the other thread
        assert tracker.get_current_parent() is main_parent
