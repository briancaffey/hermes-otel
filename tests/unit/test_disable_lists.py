"""``disable_metrics`` and ``disable_spans`` (#479): the spellings the loader
accepts, the warnings for the rest, the ``DropAggregation`` View and the
instrument gate per disabled metric, and the non-recording span a disabled
family yields (children parent to the nearest recording ancestor)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.metrics.view import DropAggregation
from opentelemetry.trace import INVALID_SPAN_CONTEXT

from hermes_otel.instruments import (
    _INSTRUMENTS,
    DISABLE_ALL,
    disabled_names,
    instrument_name,
    metric_spellings,
    metric_views,
)
from hermes_otel.pipeline import create_instruments
from hermes_otel.plugin_config import (
    SPAN_FAMILIES,
    HermesOtelConfig,
    load_config,
    validate_disable_metrics,
    validate_disable_spans,
)
from hermes_otel.testing import build_inmemory_plugin, install_singleton

ALL_NAMES = {spec.name for spec in _INSTRUMENTS.values()}


def _warnings(caplog) -> str:
    return " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


class TestDisableMetricsParsing:
    @pytest.mark.parametrize(
        "spelling",
        ["tool_duration", "tool.duration", "hermes.tool.duration", " Hermes.Tool.Duration "],
    )
    def test_every_spelling_means_the_otlp_name(self, spelling):
        assert instrument_name(spelling) == "hermes.tool.duration"

    def test_names_outside_hermes_are_their_own_spelling(self):
        assert instrument_name("gen_ai.client.token.usage") == "gen_ai.client.token.usage"
        assert instrument_name("claude_code.token.usage") == "claude_code.token.usage"
        assert instrument_name("approval_wait_s") == "hermes.approval.wait.duration"
        assert instrument_name("approval.wait.duration") == "hermes.approval.wait.duration"
        assert instrument_name("all") == DISABLE_ALL
        assert instrument_name("nope") is None
        assert instrument_name("") is None

    def test_spellings_are_unambiguous_and_cover_every_instrument(self):
        table = metric_spellings()
        assert set(table) == ALL_NAMES
        for name, spellings in table.items():
            for spelling in spellings:
                assert instrument_name(spelling) == name, spelling
            assert instrument_name(name) == name

    def test_normalises_dedupes_and_keeps_all(self, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            kept = validate_disable_metrics(
                ["tool.duration", "TOKEN_USAGE", "hermes.tool.duration", "nope", "all", ""]
            )
        assert kept == ("hermes.tool.duration", "hermes.token.usage", "all")
        text = _warnings(caplog)
        assert "'nope'" in text and "disable_metrics" in text
        # the warning lists every valid name
        for name in ("all", "hermes.token.usage", "gen_ai.client.token.usage"):
            assert name in text

    def test_disabled_names_expands_all(self):
        assert disabled_names(("all",)) == frozenset(ALL_NAMES)
        assert disabled_names(("hermes.tool.duration",)) == frozenset({"hermes.tool.duration"})
        assert disabled_names(()) == frozenset()

    def test_dataclass_normalises_on_construction(self):
        cfg = HermesOtelConfig(disable_metrics=["tool_duration", "bogus"])
        assert cfg.disable_metrics == ("hermes.tool.duration",)
        assert HermesOtelConfig().disable_metrics == ()
        assert HermesOtelConfig(disable_metrics="tool.duration, model_usage").disable_metrics == (
            "hermes.tool.duration",
            "hermes.model.usage",
        )

    def test_yaml_list_then_env_override(self, tmp_path, monkeypatch):
        pytest.importorskip("yaml")
        path = tmp_path / "hermes_otel.yaml"
        path.write_text("disable_metrics:\n  - tool.duration\n  - hermes.approval.duration\n")
        assert load_config(path=path).disable_metrics == (
            "hermes.tool.duration",
            "hermes.approval.duration",
        )
        monkeypatch.setenv("HERMES_OTEL_DISABLE_METRICS", "token_usage, cost.usage")
        assert load_config(path=path).disable_metrics == (
            "hermes.token.usage",
            "hermes.cost.usage",
        )


class TestDisableSpansParsing:
    def test_valid_families_lowercased_and_deduped(self):
        assert validate_disable_spans(["Tool", "api", "tool", " approval "]) == (
            "tool",
            "api",
            "approval",
        )
        assert HermesOtelConfig().disable_spans == ()

    def test_all_is_refused_with_a_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            assert validate_disable_spans(["all", "skill"]) == ("skill",)
        text = _warnings(caplog)
        assert "'all' is not allowed" in text and "traces: false" in text

    def test_unknown_family_warns_listing_the_valid_ones(self, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            assert validate_disable_spans(["agent", "transform"]) == ()
        text = _warnings(caplog)
        assert "'agent'" in text and "'transform'" in text
        for family in SPAN_FAMILIES:
            assert family in text

    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_OTEL_DISABLE_SPANS", "approval,human_input")
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.disable_spans == ("approval", "human_input")


class TestDisabledInstruments:
    def test_drop_view_replaces_the_label_view(self):
        views = metric_views(disabled=("hermes.tool.duration",))
        assert len(views) == len(_INSTRUMENTS)
        by_name = {v._instrument_name: v for v in views}
        assert isinstance(by_name["hermes.tool.duration"]._aggregation, DropAggregation)
        assert by_name["hermes.tool.duration"]._attribute_keys is None
        assert "profile" in by_name["hermes.token.usage"]._attribute_keys
        assert not isinstance(by_name["hermes.token.usage"]._aggregation, DropAggregation)

    def test_all_drops_every_view(self):
        for view in metric_views(disabled=("all",)):
            assert isinstance(view._aggregation, DropAggregation), view._instrument_name

    def test_default_views_drop_nothing(self):
        assert not any(isinstance(v._aggregation, DropAggregation) for v in metric_views())

    def test_disabled_instrument_is_neither_created_nor_recorded(self):
        cfg = HermesOtelConfig(disable_metrics=("tool.duration",))
        spec = _INSTRUMENTS["tool_duration"]
        assert not spec.enabled(cfg) and not spec.recorded(cfg)
        assert _INSTRUMENTS["token_usage"].enabled(cfg)
        created = create_instruments(MagicMock(), cfg)
        assert "tool_duration" not in created and "token_usage" in created

    def test_all_creates_nothing(self):
        cfg = HermesOtelConfig(disable_metrics=("all",), metric_name_compat="both")
        assert create_instruments(MagicMock(), cfg) == {}


@pytest.fixture()
def plugin_for():
    built = []

    def build(**config):
        pipe = build_inmemory_plugin(metrics=True, config=HermesOtelConfig(**config))
        install_singleton(pipe.plugin)
        built.append(pipe)
        return pipe

    try:
        yield build
    finally:
        for pipe in built:
            pipe.shutdown()


class TestDisabledSpanFamilies:
    def test_disabled_family_yields_a_tracked_non_recording_span(self, plugin_for):
        pipe = plugin_for(disable_spans=("tool",))
        plugin = pipe.plugin
        span = plugin.start_span("tool.terminal", "terminal:1", kind="tool", session_id="s1")
        assert not span.is_recording()
        assert plugin.spans.get_span("terminal:1") is span
        # the hooks' end path is a no-op on it and forgets it
        plugin.end_span("terminal:1", attributes={"a": 1}, status="error", error_message="x")
        assert plugin.spans.get_span("terminal:1") is None
        assert pipe.span_exporter.get_finished_spans() == ()

    def test_without_a_parent_the_stand_in_has_no_context(self, plugin_for):
        plugin = plugin_for(disable_spans=("approval",)).plugin
        span = plugin.start_span("approval", "approval:s1:c1", session_id="s1")
        assert span.get_span_context() == INVALID_SPAN_CONTEXT

    def test_child_parents_to_the_nearest_recording_ancestor(self, plugin_for):
        pipe = plugin_for(disable_spans=("llm",))
        plugin = pipe.plugin
        root = plugin.start_span("agent", "session:s1", kind="agent", session_id="s1")
        plugin.spans.push_parent(root, session_id="s1")
        llm = plugin.start_span("llm.gpt-4", "llm:s1", kind="llm", session_id="s1")
        plugin.spans.push_parent(llm, session_id="s1")
        assert not llm.is_recording()
        assert llm.get_span_context() == root.get_span_context()
        api = plugin.start_span("api.gpt-4", "api:t1", kind="llm", session_id="s1")
        assert api.is_recording()
        assert api.parent.span_id == root.get_span_context().span_id
        plugin.end_span("api:t1")
        plugin.end_span("llm:s1")
        plugin.end_span("session:s1")
        assert [s.name for s in pipe.span_exporter.get_finished_spans()] == ["api.gpt-4", "agent"]

    def test_explicit_family_overrides_the_name(self, plugin_for):
        plugin = plugin_for(disable_spans=("auxiliary",)).plugin
        aux = plugin.start_span("api.gpt-4o-mini", "aux:1", kind="llm", family="auxiliary")
        main = plugin.start_span("api.gpt-4", "api:1", kind="llm")
        assert not aux.is_recording() and main.is_recording()

    def test_api_leaves_the_auxiliary_span_alone(self, plugin_for):
        plugin = plugin_for(disable_spans=("api",)).plugin
        aux = plugin.start_span("api.gpt-4o-mini", "aux:1", kind="llm", family="auxiliary")
        main = plugin.start_span("api.gpt-4", "api:1", kind="llm")
        assert aux.is_recording() and not main.is_recording()

    def test_the_root_is_never_disabled(self, plugin_for):
        plugin = plugin_for(disable_spans=tuple(SPAN_FAMILIES)).plugin
        assert plugin.start_span(
            "agent", "session:s1", kind="agent", session_id="s1"
        ).is_recording()
        assert plugin.start_span("cron", "session:s2", kind="agent", session_id="s2").is_recording()

    def test_swept_stand_in_is_not_counted(self, plugin_for):
        pipe = plugin_for(disable_spans=("tool",))
        plugin = pipe.plugin
        root = plugin.start_span("agent", "session:s1", kind="agent", session_id="s1")
        plugin.spans.push_parent(root, session_id="s1")
        plugin.register_turn("s1")
        plugin.start_span("tool.terminal", "terminal:1", kind="tool", session_id="s1")
        plugin.start_span("api.gpt-4", "api:t1", kind="llm", session_id="s1")
        assert plugin.end_leftover_children("s1", "turn_end") == 1
        assert plugin.spans.get_span("terminal:1") is None
        assert plugin.spans.get_span("api:t1") is None
        swept = [
            m
            for rm in pipe.metric_reader.get_metrics_data().resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
            if m.name == "hermes.span.swept.count"
        ]
        assert [dict(p.attributes)["span_kind"] for p in swept[0].data.data_points] == ["api"]
