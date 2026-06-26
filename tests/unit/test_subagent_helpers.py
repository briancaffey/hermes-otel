"""Unit tests for sub-agent (delegation) pure helpers and fail-open handlers."""

import pytest
from hermes_otel.helpers import subagent_span_key, subagent_status_to_span_status


class TestSubagentSpanKey:
    def test_builds_key_from_child_session_id(self):
        assert subagent_span_key("child-123") == "subagent:child-123"

    def test_stringifies_non_string_ids(self):
        assert subagent_span_key(42) == "subagent:42"

    def test_strips_whitespace(self):
        assert subagent_span_key("  child-9  ") == "subagent:child-9"

    @pytest.mark.parametrize("bad", [None, "", "   "])
    def test_returns_none_for_unusable_id(self, bad):
        assert subagent_span_key(bad) is None


class TestSubagentStatusMapping:
    @pytest.mark.parametrize(
        "status", ["error", "errored", "failed", "failure", "cancelled", "timeout", "timed_out"]
    )
    def test_failure_statuses_map_to_error(self, status):
        assert subagent_status_to_span_status(status) == "error"
        # Case-insensitive.
        assert subagent_status_to_span_status(status.upper()) == "error"

    @pytest.mark.parametrize("status", ["ok", "completed", "complete", "success", "done"])
    def test_success_statuses_map_to_ok(self, status):
        assert subagent_status_to_span_status(status) == "ok"

    @pytest.mark.parametrize("status", [None, "", "   ", "weird-unknown-value"])
    def test_unknown_or_empty_defaults_to_ok(self, status):
        # Unknown / missing status must never inflate error rates.
        assert subagent_status_to_span_status(status) == "ok"


class TestHandlersFailOpenWhenDisabled:
    """When the tracer isn't initialized, both handlers must no-op silently."""

    def test_subagent_start_noop_when_disabled(self):
        from hermes_otel.hooks import on_subagent_start

        # _reset_otel_state leaves the singleton uninitialized → is_enabled False.
        on_subagent_start(
            parent_session_id="p1",
            child_session_id="c1",
            child_role="researcher",
            child_goal="do a thing",
        )

    def test_subagent_stop_noop_when_disabled(self):
        from hermes_otel.hooks import on_subagent_stop

        on_subagent_stop(
            parent_session_id="p1",
            child_session_id="c1",
            child_status="completed",
            duration_ms=1234,
        )


class TestHandlersAcceptForwardCompatKwargs:
    """Handlers must accept unknown additive fields without raising."""

    def test_start_accepts_unknown_kwargs(self, inmemory_otel_setup):
        from hermes_otel.hooks import on_subagent_start

        on_subagent_start(
            parent_session_id="p1",
            child_session_id="c1",
            child_role="researcher",
            child_goal="do a thing",
            some_future_field="ignored",
            telemetry_schema_version="hermes.observer.v1",
        )

    def test_stop_accepts_unknown_kwargs(self, inmemory_otel_setup):
        from hermes_otel.hooks import on_subagent_stop

        on_subagent_stop(
            parent_session_id="p1",
            child_session_id="c1",
            child_status="completed",
            duration_ms=10,
            another_future_field=123,
        )


class TestHandlersFailOpenOnMissingChildId:
    def test_start_without_child_session_id_noop(self, inmemory_otel_setup):
        from hermes_otel.hooks import on_subagent_start

        exporter, _ = inmemory_otel_setup
        on_subagent_start(parent_session_id="p1", child_role="x", child_goal="y")
        # Nothing started, nothing finished.
        assert exporter.get_finished_spans() == ()

    def test_stop_without_matching_start_noop(self, inmemory_otel_setup):
        from hermes_otel.hooks import on_subagent_stop

        exporter, _ = inmemory_otel_setup
        # No prior start; should not raise and should produce no span.
        on_subagent_stop(parent_session_id="p1", child_session_id="ghost", child_status="completed")
        assert exporter.get_finished_spans() == ()
