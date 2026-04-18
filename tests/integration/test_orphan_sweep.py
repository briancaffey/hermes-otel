"""Integration tests for the orphan-turn sweep (TTL-based cleanup)."""

from unittest.mock import patch

import pytest

import hermes_otel.tracer as tracer_mod
from hermes_otel.hooks import (
    on_pre_api_request,
    on_pre_tool_call,
    on_session_start,
)
from hermes_otel.plugin_config import HermesOtelConfig


def _span_by_name(spans, name):
    for s in spans:
        if s.name == name:
            return s
    raise ValueError(f"No span named '{name}' in {[s.name for s in spans]}")


class TestOrphanSweep:
    def test_session_finalized_with_timed_out_status(self, inmemory_otel_setup):
        """A session older than root_span_ttl_ms gets finalized as timed_out."""
        exporter, plugin = inmemory_otel_setup
        # Shrink TTL to 0 so any positive age expires.
        plugin.config = HermesOtelConfig(root_span_ttl_ms=0)

        on_session_start(session_id="s_dead", model="gpt-4", platform="cli")
        # Don't call on_session_end — simulate a dropped end hook.

        # Now fire an unrelated pre_* hook on a different session — this is
        # what the PRD expects to trigger the sweep.
        on_session_start(session_id="s_live", model="gpt-4", platform="cli")

        spans = exporter.get_finished_spans()
        # The dead session must be finalized (exported).
        timed_out_spans = [s for s in spans if s.attributes.get("hermes.turn.final_status") == "timed_out"]
        assert len(timed_out_spans) == 1
        # Must be status OK, not ERROR, so error dashboards aren't polluted.
        from opentelemetry.trace import StatusCode
        assert timed_out_spans[0].status.status_code == StatusCode.OK

    def test_sweep_finalizes_sub_spans_for_same_session(self, inmemory_otel_setup):
        """A session with active llm/api/tool sub-spans all get finalized."""
        import time

        exporter, plugin = inmemory_otel_setup
        # Use a realistic TTL + manually backdate the registry entry so we
        # don't accidentally finalize the session we just started.
        plugin.config = HermesOtelConfig(root_span_ttl_ms=1_000)

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_api_request(
            task_id="t1", session_id="s1", platform="cli", model="gpt-4",
            provider="openai", base_url="", api_mode="chat",
            api_call_count=1, message_count=5, tool_count=0,
            approx_input_tokens=500, request_char_count=2000, max_tokens=0,
        )

        # Backdate the registry — simulate 10s passed without a post hook.
        plugin._turn_started_at["s1"] = time.perf_counter() - 10.0

        # An unrelated hook fires → sweep runs → s1 is finalized.
        on_pre_tool_call(tool_name="bash", args={}, task_id="t_other", session_id="other")

        spans = exporter.get_finished_spans()
        names = [s.name for s in spans]
        # Both the agent (session) span and the orphaned api span must have
        # been finalized by the sweep.
        assert "agent" in names
        assert "api.gpt-4" in names
        # And the session span carries the timed_out status.
        agent = _span_by_name(spans, "agent")
        assert agent.attributes.get("hermes.turn.final_status") == "timed_out"

    def test_sweep_does_not_expire_young_sessions(self, inmemory_otel_setup):
        """With a large TTL, the sweep is a no-op."""
        exporter, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(root_span_ttl_ms=3_600_000)

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_session_start(session_id="s2", model="gpt-4", platform="cli")

        # Two session-starts of different sessions should NOT finalize each other.
        spans = exporter.get_finished_spans()
        assert len(spans) == 0

    def test_normal_end_path_unregisters(self, inmemory_otel_setup):
        """on_session_end removes the TTL entry so it can't be swept later."""
        from hermes_otel.hooks import on_session_end

        exporter, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(root_span_ttl_ms=1)

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_session_end(session_id="s1", completed=True, interrupted=False,
                       model="gpt-4", platform="cli")

        assert "s1" not in plugin._turn_started_at
        # A subsequent sweep is a no-op.
        assert plugin.sweep_expired_turns() == []


class TestSweepSessionIsolation:
    def test_one_expired_one_alive(self, inmemory_otel_setup):
        """Only the expired session is finalized; the live one keeps running."""
        exporter, plugin = inmemory_otel_setup

        # Start a session with a huge TTL, then shrink the TTL artificially
        # to finalize only the old one.
        plugin.config = HermesOtelConfig(root_span_ttl_ms=3_600_000)
        on_session_start(session_id="old", model="gpt-4", platform="cli")

        # Now swap in a tiny TTL and manipulate the timestamp via the
        # registry so only "old" is past it.
        import time
        plugin.config = HermesOtelConfig(root_span_ttl_ms=100)
        plugin._turn_started_at["old"] = time.perf_counter() - 10.0  # 10s ago

        # Start a new session — its pre_* will trigger the sweep.
        on_session_start(session_id="new", model="gpt-4", platform="cli")

        # Old session must be finalized.
        assert "old" not in plugin._turn_started_at
        # New session must still be live.
        assert "new" in plugin._turn_started_at

        spans = exporter.get_finished_spans()
        timed_out = [s for s in spans if s.attributes.get("hermes.turn.final_status") == "timed_out"]
        assert len(timed_out) == 1
