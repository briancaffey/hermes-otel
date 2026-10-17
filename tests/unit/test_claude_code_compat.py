"""Claude Code metric-name compatibility (#485): the ``claude_code.*`` twins
exist only when ``metric_name_compat`` switches them on, carry Claude Code's
units and label names, replace (or sit beside) their ``hermes.*`` twins in
the OTLP export, never reach the live store, and carry ``session.id`` only
on opt-in.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermes_otel.instruments import (
    _INSTRUMENTS,
    CLAUDE_CODE_TOKEN_TYPES,
    claude_code_token_types,
    metric_views,
)
from hermes_otel.plugin_config import HermesOtelConfig, load_config
from hermes_otel.testing import attach_pipeline
from hermes_otel.tracer import HermesOTelPlugin

COMPAT = {k: s for k, s in _INSTRUMENTS.items() if s.compat == "claude_code"}
# The ``hermes.*`` keys the exclusive mode replaces with their twins.
REPLACED = {s.compat_of for s in COMPAT.values()}


def _plugin(**cfg) -> HermesOTelPlugin:
    p = HermesOTelPlugin()
    p.config = HermesOtelConfig(**cfg)
    meter = MagicMock()
    meter.create_counter.side_effect = lambda name, **kw: MagicMock(name=name)
    meter.create_histogram.side_effect = lambda name, **kw: MagicMock(name=name)
    attach_pipeline(p, meter=meter)
    return p


class TestTable:
    def test_the_four_claude_code_instruments_and_their_twins(self):
        assert set(COMPAT) == {
            "claude_code.session.count",
            "claude_code.token.usage",
            "claude_code.cost.usage",
            "claude_code.active_time.total",
        }
        assert REPLACED == {"session_count", "token_usage", "cost_usage", "active_time"}
        for spec in COMPAT.values():
            assert spec.kind == "counter"

    def test_claude_code_units_are_the_literal_ones_prometheus_puts_in_the_name(self):
        # claude_code_token_usage_tokens_total / _cost_usage_USD_total /
        # _active_time_seconds_total / _session_count_total
        assert COMPAT["claude_code.token.usage"].unit == "tokens"
        assert COMPAT["claude_code.cost.usage"].unit == "USD"
        assert COMPAT["claude_code.active_time.total"].unit == "s"
        assert COMPAT["claude_code.session.count"].unit == "{session}"

    def test_claude_code_label_names(self):
        assert COMPAT["claude_code.token.usage"].labels == ("type", "model", "query_source")
        assert COMPAT["claude_code.cost.usage"].labels == ("model", "query_source")
        assert COMPAT["claude_code.active_time.total"].labels == ("type",)
        assert COMPAT["claude_code.session.count"].labels == ("start_type",)

    def test_views_let_session_id_through_only_for_compat_instruments(self):
        pytest.importorskip("opentelemetry.sdk.metrics")
        by_name = {v._instrument_name: v for v in metric_views()}
        assert "session.id" in by_name["claude_code.token.usage"]._attribute_keys
        assert "session.id" not in by_name["hermes.token.usage"]._attribute_keys


class TestTokenTypes:
    def test_input_is_the_uncached_prompt_and_reasoning_is_dropped(self):
        totals = {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "cache_read_tokens": 600,
            "cache_write_tokens": 100,
            "reasoning_tokens": 20,
        }
        assert claude_code_token_types(totals) == {
            "input": 300,
            "output": 50,
            "cacheRead": 600,
            "cacheCreation": 100,
        }
        assert tuple(claude_code_token_types(totals)) == CLAUDE_CODE_TOKEN_TYPES

    def test_the_four_types_sum_to_the_request(self):
        totals = {"prompt_tokens": 1000, "completion_tokens": 50, "cache_read_tokens": 600}
        assert sum(claude_code_token_types(totals).values()) == 1050

    def test_never_negative_when_a_provider_reports_cache_beyond_the_prompt(self):
        totals = {"prompt_tokens": 10, "completion_tokens": 1, "cache_read_tokens": 20}
        assert claude_code_token_types(totals)["input"] == 0

    def test_missing_fields_are_zero(self):
        assert claude_code_token_types({}) == {
            "input": 0,
            "output": 0,
            "cacheRead": 0,
            "cacheCreation": 0,
        }


class TestCreation:
    def test_off_creates_no_compat_instrument(self):
        p = _plugin()
        assert not (set(p.pipeline.instruments) & set(COMPAT))
        assert "token_usage" in p.pipeline.instruments

    @pytest.mark.parametrize("mode", ["claude_code", "both"])
    def test_on_creates_them_with_claude_code_units(self, mode):
        p = _plugin(metric_name_compat=mode)
        assert set(COMPAT) <= set(p.pipeline.instruments)
        calls = {c.args[0]: c.kwargs for c in p.pipeline.meter.create_counter.call_args_list}
        assert calls["claude_code.token.usage"]["unit"] == "tokens"
        assert calls["claude_code.cost.usage"]["unit"] == "USD"
        assert calls["claude_code.active_time.total"]["unit"] == "s"


class TestRecording:
    def test_off_records_nothing_on_a_compat_key_and_everything_on_hermes(self):
        p = _plugin()
        p.record_metric("claude_code.token.usage", 5, {"type": "input"})  # no instrument, no error
        p.record_metric("token_usage", 5, {"token_type": "input"})
        p.pipeline.instruments["token_usage"].add.assert_called_once_with(
            5, {"profile": "default", "token_type": "input"}
        )

    def test_exclusive_mode_exports_the_twin_instead_of_the_hermes_instrument(self):
        p = _plugin(metric_name_compat="claude_code")
        p.record_metric("token_usage", 5, {"token_type": "input", "model": "m"})
        p.record_metric("claude_code.token.usage", 5, {"type": "input", "model": "m"})
        p.record_metric("session_count", 1, {"platform": "cli"})
        p.record_metric("claude_code.session.count", 1, {"start_type": "fresh"})
        p.record_metric("cost_usage", 0.5, {"model": "m", "cost_status": "actual"})
        p.record_metric("claude_code.cost.usage", 0.5, {"model": "m"})
        p.record_metric("active_time", 2.5, {"type": "cli", "platform": "cli"})
        p.record_metric("claude_code.active_time.total", 2.5, {"type": "cli"})
        for key in REPLACED:
            p.pipeline.instruments[key].add.assert_not_called()
        p.pipeline.instruments["claude_code.token.usage"].add.assert_called_once_with(
            5, {"profile": "default", "type": "input", "model": "m"}
        )
        p.pipeline.instruments["claude_code.session.count"].add.assert_called_once_with(
            1, {"profile": "default", "start_type": "fresh"}
        )
        p.pipeline.instruments["claude_code.cost.usage"].add.assert_called_once_with(
            0.5, {"profile": "default", "model": "m"}
        )
        p.pipeline.instruments["claude_code.active_time.total"].add.assert_called_once_with(
            2.5, {"profile": "default", "type": "cli"}
        )

    def test_exclusive_mode_leaves_every_other_hermes_instrument_alone(self):
        p = _plugin(metric_name_compat="claude_code")
        p.record_metric("message_count", 1, {"model": "m"})
        p.record_metric("prompt_cache_tokens", 7, {"cache_result": "hit"})
        p.pipeline.instruments["message_count"].add.assert_called_once()
        p.pipeline.instruments["prompt_cache_tokens"].add.assert_called_once()

    def test_both_mode_exports_both(self):
        p = _plugin(metric_name_compat="both")
        p.record_metric("token_usage", 5, {"token_type": "input"})
        p.record_metric("claude_code.token.usage", 5, {"type": "input"})
        p.pipeline.instruments["token_usage"].add.assert_called_once()
        p.pipeline.instruments["claude_code.token.usage"].add.assert_called_once()

    def test_live_store_keeps_the_hermes_point_in_exclusive_mode_and_never_sees_a_compat_point(
        self, monkeypatch
    ):
        store = MagicMock()
        monkeypatch.setattr("hermes_otel.live_store_registry.get_live_store", lambda **kw: store)
        p = _plugin(metric_name_compat="claude_code")
        p.pipeline.live_active = True
        p.record_metric("token_usage", 3, {"token_type": "output"})
        p.record_metric("claude_code.token.usage", 3, {"type": "output"})
        assert store.add_metric.call_count == 1
        assert store.add_metric.call_args.args[0] == "hermes.token.usage"

    def test_session_id_survives_only_on_compat_instruments_and_only_on_opt_in(self, monkeypatch):
        store = MagicMock()
        monkeypatch.setattr("hermes_otel.live_store_registry.get_live_store", lambda **kw: store)
        p = _plugin(metric_name_compat="both")
        p.pipeline.live_active = True
        p.record_metric("token_usage", 3, {"token_type": "output", "session.id": "s1"})
        p.record_metric("claude_code.token.usage", 3, {"type": "output", "session.id": "s1"})
        p.pipeline.instruments["token_usage"].add.assert_called_once_with(
            3, {"profile": "default", "token_type": "output"}
        )
        assert "session.id" not in store.add_metric.call_args.args[2]
        p.pipeline.instruments["claude_code.token.usage"].add.assert_called_once_with(
            3, {"profile": "default", "type": "output"}
        )

        p = _plugin(metric_name_compat="both", metric_name_compat_session_id=True)
        p.record_metric("token_usage", 3, {"token_type": "output", "session.id": "s1"})
        p.record_metric("claude_code.token.usage", 3, {"type": "output", "session.id": "s1"})
        p.pipeline.instruments["token_usage"].add.assert_called_once_with(
            3, {"profile": "default", "token_type": "output"}
        )
        p.pipeline.instruments["claude_code.token.usage"].add.assert_called_once_with(
            3, {"profile": "default", "type": "output", "session.id": "s1"}
        )


class TestConfig:
    def test_default_is_off(self):
        assert HermesOtelConfig().metric_name_compat == "off"
        assert HermesOtelConfig().metric_name_compat_session_id is False

    @pytest.mark.parametrize(
        "raw,expected",
        [("claude_code", "claude_code"), ("Claude_Code", "claude_code"), ("BOTH", "both")],
    )
    def test_values_are_case_insensitive(self, raw, expected, tmp_path):
        pytest.importorskip("yaml")
        path = tmp_path / "hermes_otel.yaml"
        path.write_text(f"metric_name_compat: {raw}\n")
        assert load_config(path=path).metric_name_compat == expected

    @pytest.mark.parametrize("raw", ["false", "none", "no", "''"])
    def test_falsy_spellings_mean_off(self, raw, tmp_path):
        pytest.importorskip("yaml")
        path = tmp_path / "hermes_otel.yaml"
        path.write_text(f"metric_name_compat: {raw}\n")
        assert load_config(path=path).metric_name_compat == "off"

    def test_unknown_value_warns_and_falls_back_to_off(self, tmp_path, caplog):
        import logging

        pytest.importorskip("yaml")
        path = tmp_path / "hermes_otel.yaml"
        path.write_text("metric_name_compat: datadog\n")
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = load_config(path=path)
        assert cfg.metric_name_compat == "off"
        assert any("metric_name_compat" in r.getMessage() for r in caplog.records)

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_METRIC_NAME_COMPAT", "claude_code")
        monkeypatch.setenv("HERMES_OTEL_METRIC_NAME_COMPAT_SESSION_ID", "true")
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.metric_name_compat == "claude_code"
        assert cfg.metric_name_compat_session_id is True
