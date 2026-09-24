"""The metric instrument table (#95): one source of truth for creation and
recording, consistent value handling, the GenAI gate applied once, and the
OTLP name mirrored into the live store.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.tracer import _INSTRUMENTS, HermesOTelPlugin, metric_otlp_name

EXPECTED_KEYS = {
    "session_count",
    "session_turns",
    "session_duration",
    "token_usage",
    "prompt_cache_tokens",
    "prompt_cache_observations",
    "cost_usage",
    "tool_duration",
    "message_count",
    "model_usage",
    "skill_inferred",
    "subagent_count",
    "subagent_duration",
    "api_error_count",
    "retry_count",
    "approval_count",
    "approval_duration",
    # seconds-based successors (#233)
    "tool_duration_s",
    "approval_wait_s",
    "subagent_run_s",
    "gen_ai.client.token.usage",
    "gen_ai.client.operation.duration",
    "gen_ai.agent.token.usage",
}


class TestTable:
    def test_every_key_the_hooks_record_is_in_the_table(self):
        assert set(_INSTRUMENTS) == EXPECTED_KEYS

    def test_every_spec_is_complete(self):
        for key, spec in _INSTRUMENTS.items():
            assert spec.kind in ("counter", "histogram"), key
            assert spec.value in ("one", "int", "float", "record"), key
            assert spec.unit, key  # every instrument carries a unit
            assert spec.description, key
            assert (spec.kind == "histogram") == (spec.value == "record"), key
            assert spec.genai == spec.name.startswith("gen_ai."), key

    def test_otlp_names_are_unique_and_conventional(self):
        names = [s.name for s in _INSTRUMENTS.values()]
        assert len(names) == len(set(names))
        for n in names:
            assert n.startswith(("hermes.", "gen_ai.")), n

    def test_metric_otlp_name(self):
        assert metric_otlp_name("token_usage") == "hermes.token.usage"
        assert metric_otlp_name("gen_ai.agent.token.usage") == "gen_ai.agent.token.usage"
        assert metric_otlp_name("something.custom") == "something.custom"


@pytest.fixture()
def plugin():
    """A plugin with a fake meter so every instrument is a MagicMock."""
    p = HermesOTelPlugin()
    p.config = HermesOtelConfig()
    p._meter = MagicMock()
    p._meter.create_counter.side_effect = lambda name, **kw: MagicMock(name=name)
    p._meter.create_histogram.side_effect = lambda name, **kw: MagicMock(name=name)
    p._create_metric_instruments()
    return p


class TestCreation:
    def test_creates_one_instrument_per_key_with_unit_and_description(self, plugin):
        assert set(plugin._instruments) == EXPECTED_KEYS
        calls = {
            c.args[0]: c.kwargs
            for c in plugin._meter.create_counter.call_args_list
            + plugin._meter.create_histogram.call_args_list
        }
        assert calls["hermes.token.usage"] == {
            "unit": "{token}",
            "description": "Tokens consumed by type",
        }
        assert calls["hermes.cost.usage"]["unit"] == "USD"
        assert calls["hermes.tool.duration"]["unit"] == "ms"
        assert calls["gen_ai.client.operation.duration"]["unit"] == "s"

    def test_a_failing_instrument_does_not_take_the_others_down(self):
        p = HermesOTelPlugin()
        p.config = HermesOtelConfig()
        p._meter = MagicMock()

        def counter(name, **kw):
            if name == "hermes.cost.usage":
                raise RuntimeError("boom")
            return MagicMock()

        p._meter.create_counter.side_effect = counter
        p._meter.create_histogram.side_effect = lambda name, **kw: MagicMock()
        p._create_metric_instruments()
        assert "cost_usage" not in p._instruments
        assert "token_usage" in p._instruments
        p.record_metric("cost_usage", 1.5, {})  # no AttributeError
        p.record_metric("token_usage", 7, {"token_type": "input"})
        p._instruments["token_usage"].add.assert_called_once_with(
            7, {"profile": "default", "token_type": "input"}
        )


class TestRecording:
    def test_value_modes(self, plugin):
        plugin.record_metric("session_count", 99, {"platform": "cli"})
        plugin._instruments["session_count"].add.assert_called_once_with(
            1, {"profile": "default", "platform": "cli"}
        )
        plugin.record_metric("token_usage", 12.9, {"token_type": "input"})
        plugin._instruments["token_usage"].add.assert_called_once_with(
            12, {"profile": "default", "token_type": "input"}
        )
        plugin.record_metric("cost_usage", 0.0042, {"model": "m"})
        plugin._instruments["cost_usage"].add.assert_called_once_with(
            0.0042, {"profile": "default", "model": "m"}
        )
        plugin.record_metric("tool_duration", 12.5, {"tool_name": "terminal"})
        plugin._instruments["tool_duration"].record.assert_called_once_with(
            12.5, {"profile": "default", "tool_name": "terminal"}
        )
        # Token histograms record whole tokens; other histograms keep the float.
        plugin.record_metric("gen_ai.client.token.usage", 10.7, {"gen_ai.token.type": "input"})
        plugin._instruments["gen_ai.client.token.usage"].record.assert_called_once_with(
            10, {"profile": "default", "gen_ai.token.type": "input"}
        )
        plugin.record_metric("gen_ai.client.operation.duration", 0.25, {})
        plugin._instruments["gen_ai.client.operation.duration"].record.assert_called_once_with(
            0.25,
            {
                "profile": "default",
            },
        )

    def test_unknown_key_is_ignored(self, plugin):
        plugin.record_metric("not_a_metric", 1, {})
        for inst in plugin._instruments.values():
            inst.add.assert_not_called()
            inst.record.assert_not_called()

    def test_genai_gate_applies_once_in_record_metric(self, plugin):
        plugin.config = HermesOtelConfig(emit_genai_metrics=False)
        plugin.record_metric("gen_ai.client.token.usage", 5, {})
        plugin.record_metric("gen_ai.agent.token.usage", 5, {})
        plugin.record_metric("gen_ai.client.operation.duration", 0.5, {})
        for key in ("gen_ai.client.token.usage", "gen_ai.agent.token.usage"):
            plugin._instruments[key].record.assert_not_called()
        plugin.record_metric("token_usage", 5, {})  # hermes.* unaffected
        plugin._instruments["token_usage"].add.assert_called_once()

    def test_no_meter_is_fine(self):
        p = HermesOTelPlugin()
        p.config = HermesOtelConfig()
        p.record_metric("token_usage", 5, {})  # nothing to do, nothing raised

    def test_live_store_gets_the_otlp_name(self, plugin, monkeypatch):
        store = MagicMock()
        monkeypatch.setattr("hermes_otel.live_store.get_live_store", lambda **kw: store)
        plugin._live_active = True
        plugin.record_metric("token_usage", 3, {"token_type": "output"})
        name, value, attrs, ts = store.add_metric.call_args.args
        assert name == "hermes.token.usage" and value == 3
        assert attrs == {"profile": "default", "token_type": "output"}
        assert isinstance(ts, int)

    def test_live_store_respects_the_genai_gate(self, plugin, monkeypatch):
        store = MagicMock()
        monkeypatch.setattr("hermes_otel.live_store.get_live_store", lambda **kw: store)
        plugin._live_active = True
        plugin.config = HermesOtelConfig(emit_genai_metrics=False)
        plugin.record_metric("gen_ai.client.token.usage", 3, {})
        store.add_metric.assert_not_called()
