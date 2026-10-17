"""``common_attributes`` (#480): the validator, the span processor, the log
enrich step, the metric label merge and the View allow-list, each on its own."""

import logging

import pytest

from hermes_otel import plugin_config as pc
from hermes_otel.plugin_config import HermesOtelConfig, validate_common_attributes


class TestValidation:
    def test_scalars_kept_as_given(self):
        assert validate_common_attributes(
            {"team": "otel", "tier": 2, "ratio": 0.5, "canary": True}
        ) == {"team": "otel", "tier": 2, "ratio": 0.5, "canary": True}

    @pytest.mark.parametrize("key", ["hermes.team", "gen_ai.team", "service.team"])
    def test_reserved_prefixes_dropped_with_warning(self, key, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            kept = validate_common_attributes({key: "x", "team": "otel"})
        assert kept == {"team": "otel"}
        assert any(key in r.getMessage() and "reserved" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("value", [[1, 2], {"a": 1}, None, object()])
    def test_non_scalar_values_dropped_with_warning(self, value, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            kept = validate_common_attributes({"bad": value, "team": "otel"})
        assert kept == {"team": "otel"}
        assert any("'bad'" in r.getMessage() for r in caplog.records)

    def test_key_length_and_empty_keys(self, caplog):
        long_key = "k" * (pc.COMMON_ATTRIBUTE_KEY_MAX_LENGTH + 1)
        edge_key = "k" * pc.COMMON_ATTRIBUTE_KEY_MAX_LENGTH
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            kept = validate_common_attributes({long_key: "x", "": "y", "  ": "z", edge_key: "ok"})
        assert kept == {edge_key: "ok"}
        assert sum("must be 1 to" in r.getMessage() for r in caplog.records) == 3

    def test_nothing_left_is_none(self):
        assert validate_common_attributes({"hermes.x": 1}) is None
        assert validate_common_attributes({}) is None

    def test_not_a_mapping(self, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            assert validate_common_attributes(["team=otel"]) is None
        assert any("must be a mapping" in r.getMessage() for r in caplog.records)
        assert validate_common_attributes(None) is None

    def test_dataclass_runs_the_validator(self):
        cfg = HermesOtelConfig(common_attributes={"team": "otel", "gen_ai.x": "no", "l": [1]})
        assert cfg.common_attributes == {"team": "otel"}
        assert HermesOtelConfig().common_attributes is None
        assert HermesOtelConfig(common_attributes={"hermes.x": 1}).common_attributes is None

    def test_yaml_load_expands_env_refs_and_validates(self, tmp_path, monkeypatch, caplog):
        pytest.importorskip("yaml")
        monkeypatch.setenv("COMMON_TEAM", "otel")
        path = tmp_path / "config.yaml"
        path.write_text(
            "common_attributes:\n"
            "  team: ${COMMON_TEAM}\n"
            "  env: prod\n"
            "  hermes.profile: nope\n"
            "  tags: [a, b]\n"
        )
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = pc.load_config(path=path)
        assert cfg.common_attributes == {"team": "otel", "env": "prod"}
        messages = [r.getMessage() for r in caplog.records]
        assert any("'hermes.profile'" in m for m in messages)
        assert any("'tags'" in m for m in messages)

    def test_is_a_yaml_only_map_field(self):
        assert pc.field_kinds()["common_attributes"] == "map"
        assert "common_attributes" in pc.FIELD_DOCS


class TestSpanProcessor:
    def _plugin(self, **common):
        from hermes_otel.testing import build_inmemory_plugin, install_singleton

        pipe = build_inmemory_plugin(config=HermesOtelConfig(common_attributes=common))
        install_singleton(pipe.plugin)
        return pipe

    def test_stamps_every_span_at_start(self):
        pipe = self._plugin(team="otel", tier=2)
        try:
            tracer = pipe.plugin.pipeline.tracer
            with tracer.start_as_current_span("outer"):
                with tracer.start_as_current_span("inner"):
                    pass
            spans = pipe.span_exporter.get_finished_spans()
            assert [s.name for s in spans] == ["inner", "outer"]
            for span in spans:
                assert span.attributes["team"] == "otel"
                assert span.attributes["tier"] == 2
        finally:
            pipe.shutdown()

    def test_plugin_attribute_of_the_same_name_wins(self):
        pipe = self._plugin(team="otel")
        try:
            tracer = pipe.plugin.pipeline.tracer
            with tracer.start_as_current_span("hook", attributes={"team": "from-hook"}):
                pass
            (span,) = pipe.span_exporter.get_finished_spans()
            assert span.attributes["team"] == "from-hook"
        finally:
            pipe.shutdown()

    def test_hook_spans_carry_the_keys(self):
        from hermes_otel.hooks import on_post_tool_call, on_pre_tool_call

        pipe = self._plugin(team="otel")
        try:
            on_pre_tool_call(tool_name="bash", args={"cmd": "ls"}, task_id="t1")
            on_post_tool_call(tool_name="bash", args={"cmd": "ls"}, result="ok", task_id="t1")
            (span,) = pipe.span_exporter.get_finished_spans()
            assert span.name == "tool.bash"
            assert span.attributes["team"] == "otel"
            assert span.attributes["tool.name"] == "bash"
        finally:
            pipe.shutdown()

    def test_processor_never_raises(self):
        from hermes_otel.pipeline import CommonAttributesProcessor

        proc = CommonAttributesProcessor({"team": "otel"})

        class Broken:
            @property
            def attributes(self):
                raise RuntimeError("no attributes")

        proc.on_start(Broken())  # swallowed
        proc.on_end(None)
        proc.shutdown()
        assert proc.force_flush() is True

    def test_default_config_attaches_no_processor(self):
        from hermes_otel.pipeline import CommonAttributesProcessor

        pipe = self._plugin()
        try:
            provider = pipe.tracer_provider
            procs = provider._active_span_processor._span_processors
            assert not any(isinstance(p, CommonAttributesProcessor) for p in procs)
        finally:
            pipe.shutdown()


class TestLogRecords:
    def test_enrich_step_adds_keys_without_overriding(self):
        from hermes_otel.log_handler import HermesLogProcessor

        class Rec:
            def __init__(self):
                self.attributes = {"team": "from-record", "code.lineno": 3}
                self.body = "hello"
                self.trace_id = 0
                self.span_id = 0

        proc = HermesLogProcessor(redact=False, common_attributes={"team": "otel", "env": "prod"})
        rec = Rec()
        proc.on_emit(rec)
        assert rec.attributes["team"] == "from-record"
        assert rec.attributes["env"] == "prod"

    def test_redaction_and_length_cap_cover_the_keys(self):
        from hermes_otel.log_handler import HermesLogProcessor

        class Rec:
            def __init__(self):
                self.attributes = {}
                self.body = "hello"
                self.trace_id = 0
                self.span_id = 0

        proc = HermesLogProcessor(
            redact=False, max_attribute_length=4, common_attributes={"owner": "longer-than-four"}
        )
        rec = Rec()
        proc.on_emit(rec)
        assert rec.attributes["owner"] == "long…"

    def test_default_processor_adds_nothing(self):
        from hermes_otel.log_handler import HermesLogProcessor

        class Rec:
            def __init__(self):
                self.attributes = {}
                self.body = "hello"
                self.trace_id = 0
                self.span_id = 0

        rec = Rec()
        HermesLogProcessor(redact=False).on_emit(rec)
        assert rec.attributes == {}


class TestMetricViews:
    def test_every_view_admits_the_common_keys(self):
        from hermes_otel.instruments import metric_views

        for view in metric_views("explicit", common=("team", "env")):
            assert {"profile", "team", "env"} <= set(view._attribute_keys)

    def test_default_views_unchanged(self):
        from hermes_otel.instruments import _INSTRUMENTS, metric_views

        views = metric_views()
        assert len(views) == len(_INSTRUMENTS)
        for view in views:
            assert "team" not in view._attribute_keys
            assert "profile" in view._attribute_keys


class TestRecordMetric:
    def test_points_carry_the_keys_and_hook_labels_win(self):
        from hermes_otel.testing import build_inmemory_plugin, install_singleton

        pipe = build_inmemory_plugin(
            metrics=True, config=HermesOtelConfig(common_attributes={"team": "otel", "model": "x"})
        )
        install_singleton(pipe.plugin)
        try:
            pipe.plugin.record_metric("message_count", 1, {"model": "gpt-4", "provider": "openai"})
            points = [
                (m.name, dict(pt.attributes))
                for rm in pipe.metric_reader.get_metrics_data().resource_metrics
                for sm in rm.scope_metrics
                for m in sm.metrics
                for pt in m.data.data_points
            ]
            (attrs,) = [a for n, a in points if n == "hermes.message.count"]
            assert attrs == {
                "profile": "default",
                "team": "otel",
                "model": "gpt-4",
                "provider": "openai",
            }
        finally:
            pipe.shutdown()

    def test_live_store_points_carry_the_keys(self):
        from hermes_otel.testing import attach_pipeline

        plugin = __import__("hermes_otel.tracer", fromlist=["HermesOTelPlugin"]).HermesOTelPlugin(
            config=HermesOtelConfig(common_attributes={"team": "otel"})
        )
        attach_pipeline(plugin, live_active=True)
        seen = []

        class Store:
            def add_metric(self, name, value, attributes, ts):
                seen.append((name, dict(attributes)))

        from unittest.mock import patch

        with patch("hermes_otel.live_store_registry.get_live_store", return_value=Store()):
            plugin.record_metric("session_count", 1, {"platform": "cli"})
        assert seen == [
            ("hermes.session.count", {"profile": "default", "team": "otel", "platform": "cli"})
        ]


class TestSettingsReport:
    def test_shows_as_a_map_in_the_general_group(self, tmp_path, monkeypatch):
        pytest.importorskip("yaml")
        from hermes_otel.settings_report import build_settings_report

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv(pc.CONFIG_PATH_ENV, raising=False)
        monkeypatch.setattr(pc, "DURABLE_CONFIG_PATH", tmp_path / "hermes_otel.yaml")
        monkeypatch.setattr(pc, "DEFAULT_CONFIG_PATH", tmp_path / "plugins" / "config.yaml")
        (tmp_path / "hermes_otel.yaml").write_text("common_attributes:\n  team: otel\n  tier: 2\n")
        by_key = {f["key"]: f for f in build_settings_report()["fields"]}
        field = by_key["common_attributes"]
        assert field["value"] == {"team": "otel", "tier": 2}
        assert field["source"] == "file"
        assert field["group"] == "General"
        assert field["kind"] == "map"
