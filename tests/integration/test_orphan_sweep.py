"""Integration tests for the orphan-turn sweep (TTL-based cleanup).

Tests inject backdated start times via the public
``plugin.register_turn(session_id, started_at=...)`` seam rather than
writing to ``plugin._turn_started_at`` directly — so the tests stay
robust to internal-state refactors.
"""

import time

from hermes_otel.hooks import (
    on_pre_api_request,
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
        timed_out_spans = [
            s for s in spans if s.attributes.get("hermes.turn.final_status") == "timed_out"
        ]
        assert len(timed_out_spans) == 1
        # Must be status OK, not ERROR, so error dashboards aren't polluted.
        from opentelemetry.trace import StatusCode

        assert timed_out_spans[0].status.status_code == StatusCode.OK

    def test_sweep_finalizes_sub_spans_for_same_session(self, inmemory_otel_setup):
        """A session with active llm/api/tool sub-spans all get finalized."""
        exporter, plugin = inmemory_otel_setup
        # Use a realistic TTL + back-date the registry entry so we don't
        # accidentally finalize the session we just started.
        plugin.config = HermesOtelConfig(root_span_ttl_ms=1_000)

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_pre_api_request(
            task_id="t1",
            session_id="s1",
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=5,
            tool_count=0,
            approx_input_tokens=500,
            request_char_count=2000,
            max_tokens=0,
        )

        # Back-date the turn — simulate 10s passed without a post hook.
        plugin.register_turn("s1", started_at=time.perf_counter() - 10.0)

        # Trigger the sweep directly (bypasses needing an unrelated hook).
        plugin.sweep_expired_turns()

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

        _, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(root_span_ttl_ms=1)

        on_session_start(session_id="s1", model="gpt-4", platform="cli")
        on_session_end(
            session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
        )

        # A subsequent sweep is a no-op — the entry was unregistered.
        assert plugin.sweep_expired_turns() == []


class TestSweepSessionIsolation:
    def test_one_expired_one_alive(self, inmemory_otel_setup):
        """Only the expired session is finalized; the live one keeps running."""
        exporter, plugin = inmemory_otel_setup

        # Start a session with a huge TTL, then shrink the TTL artificially
        # to finalize only the old one.
        plugin.config = HermesOtelConfig(root_span_ttl_ms=3_600_000)
        on_session_start(session_id="old", model="gpt-4", platform="cli")

        # Now swap in a tiny TTL and back-date the "old" turn via the
        # public register_turn seam so only it is past the threshold.
        plugin.config = HermesOtelConfig(root_span_ttl_ms=100)
        plugin.register_turn("old", started_at=time.perf_counter() - 10.0)

        # Start a new session — its pre_* hook will trigger the sweep.
        on_session_start(session_id="new", model="gpt-4", platform="cli")

        # Only "old" gets finalized; "new" stays registered and alive.
        # We verify via the exported spans, not by inspecting internal state.
        spans = exporter.get_finished_spans()
        timed_out = [
            s for s in spans if s.attributes.get("hermes.turn.final_status") == "timed_out"
        ]
        assert len(timed_out) == 1
        # "new" session never finalizes → sweep_expired_turns returns [].
        assert plugin.sweep_expired_turns() == []
