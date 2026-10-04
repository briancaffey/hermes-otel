"""The ``logs:`` block (#266): preferred spelling, flat aliases kept, per-backend narrowing."""

from __future__ import annotations

import logging

import pytest

from hermes_otel import backends as be
from hermes_otel import log_handler as lh
from hermes_otel import plugin_config as pc
from hermes_otel.plugin_config import BackendConfig, HermesOtelConfig, load_config


def _write(tmp_path, text):
    p = tmp_path / "hermes_otel.yaml"
    p.write_text(text, encoding="utf-8")
    return p


class TestBlockLoading:
    def test_block_keys_land_on_the_flat_fields(self, tmp_path):
        cfg = load_config(
            _write(
                tmp_path,
                """
logs:
  capture: true
  level: warn
  attach_logger: agent
  exclude_loggers: [noisy, other.lib]
  logger_levels: {gateway.config: WARNING}
  only_in_turn: true
  max_attribute_length: 100
  live_min_level: error
  batch:
    schedule_delay_ms: 250
    max_queue_size: 100
    max_export_batch_size: 10
    export_timeout_ms: 5000
  events:
    enabled: true
    content: off
""",
            )
        )
        assert cfg.capture_logs is True and cfg.log_level == "WARN"
        assert cfg.log_attach_logger == "agent"
        assert cfg.log_exclude_loggers == ("noisy", "other.lib")
        assert cfg.log_logger_levels == {"gateway.config": "WARNING"}
        assert cfg.log_only_in_turn is True and cfg.log_max_attribute_length == 100
        assert cfg.log_live_min_level == "ERROR"
        assert (
            cfg.log_batch_schedule_delay_ms,
            cfg.log_batch_max_queue_size,
            cfg.log_batch_max_export_batch_size,
            cfg.log_batch_export_timeout_ms,
        ) == (250, 100, 10, 5000)
        assert cfg.log_events is True and cfg.log_events_content == "off"

    def test_flat_spellings_still_work_and_get_a_notice(self, tmp_path, caplog):
        with caplog.at_level(logging.INFO, logger="hermes_otel"):
            cfg = load_config(_write(tmp_path, "capture_logs: true\nlog_level: debug\n"))
        assert cfg.capture_logs is True and cfg.log_level == "DEBUG"
        notices = [
            r.getMessage() for r in caplog.records if "logs: block is preferred" in r.getMessage()
        ]
        assert len(notices) == 1 and "capture_logs, log_level" in notices[0]
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_block_wins_over_a_conflicting_flat_alias(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = load_config(_write(tmp_path, "log_level: debug\nlogs:\n  level: error\n"))
        assert cfg.log_level == "ERROR"
        assert any("the logs: block wins" in r.getMessage() for r in caplog.records)

    def test_unknown_block_key_warns_and_is_ignored(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = load_config(
                _write(tmp_path, "logs:\n  capture: true\n  colour: blue\n  batch: 3\n")
            )
        assert cfg.capture_logs is True
        msgs = [r.getMessage() for r in caplog.records]
        assert any("logs.colour: unknown key" in m for m in msgs)
        assert any("logs.batch must be a mapping" in m for m in msgs)

    def test_bad_events_content_warns_and_keeps_default(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = load_config(_write(tmp_path, "logs:\n  events: {content: loud}\n"))
        assert cfg.log_events_content == "inherit"
        assert any("log_events_content" in r.getMessage() for r in caplog.records)

    def test_list_field_from_env_is_comma_separated(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_LOG_EXCLUDE_LOGGERS", "a.b, c ,,")
        cfg = load_config(path=tmp_path / "none.yaml")
        assert cfg.log_exclude_loggers == ("a.b", "c")

    def test_defaults_cover_the_loop_guard_and_hermes_noisy_loggers(self):
        d = HermesOtelConfig().log_exclude_loggers
        assert {
            "opentelemetry",
            "urllib3",
            "httpx",
            "httpcore",
            "requests",
            "openai",
            "asyncio",
        } <= set(d)


class TestPerBackendMapping:
    def test_mapping_turns_the_signal_on_and_keeps_overrides(self, tmp_path):
        cfg = load_config(
            _write(
                tmp_path,
                """
backends:
  - type: openobserve
    endpoint: http://localhost:5080/api/default/v1/traces
    user: u
    password: p
    logs:
      level: WARN
      exclude_loggers: [chatty]
      logger_levels: {gateway: ERROR}
      only_in_turn: true
      events: {enabled: false, content: off}
""",
            )
        )
        (b,) = cfg.backends
        assert b.logs is True
        assert b.log_overrides == {
            "level": "WARN",
            "exclude_loggers": ("chatty",),
            "logger_levels": {"gateway": "ERROR"},
            "only_in_turn": True,
            "events": {"enabled": False, "content": "off"},
        }
        rb = be.resolve(b)
        assert rb.supports_logs is True and rb.log_overrides == b.log_overrides

    def test_bool_form_is_unchanged(self, tmp_path):
        cfg = load_config(
            _write(
                tmp_path,
                "backends:\n  - type: phoenix\n    endpoint: http://x/v1/traces\n    logs: true\n",
            )
        )
        (b,) = cfg.backends
        assert b.logs is True and b.log_overrides is None

    def test_unknown_override_key_warns(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = load_config(
                _write(
                    tmp_path,
                    "backends:\n  - type: phoenix\n    endpoint: http://x/v1/traces\n"
                    "    logs: {level: WARN, redact: false, events: {content: everything}}\n",
                )
            )
        (b,) = cfg.backends
        assert b.log_overrides == {"level": "WARN"}
        msgs = [r.getMessage() for r in caplog.records]
        assert any("backends[0].logs.redact: unknown key" in m for m in msgs)
        assert any("events.content" in m for m in msgs)


class TestRules:
    def test_rules_from_config(self):
        cfg = HermesOtelConfig(
            log_level="INFO",
            log_exclude_loggers=("x",),
            log_logger_levels={"gateway.config": "WARNING"},
            log_only_in_turn=True,
            log_events=True,
            log_events_content="preview",
        )
        r = lh.rules_from_config(cfg)
        assert r.min_severity == 9 and r.exclude_prefixes == ("x",)
        assert r.logger_levels == (("gateway.config", 13),)
        assert r.only_in_turn and r.events_enabled and r.events_content == "preview"

    def test_accepts(self):
        r = lh.LogRules(
            min_severity=9,
            exclude_prefixes=("httpx",),
            logger_levels=(("gateway", 17),),
            only_in_turn=True,
        )
        assert r.accepts("agent.loop", 9, {"hermes.session_id": "s"})
        assert not r.accepts("agent.loop", 5, {"hermes.session_id": "s"})  # below INFO
        assert not r.accepts("httpx.client", 17, {"hermes.session_id": "s"})  # excluded
        assert not r.accepts(
            "gateway.run", 13, {"hermes.session_id": "s"}
        )  # per-logger floor ERROR
        assert r.accepts("gateway.run", 17, {"hermes.session_id": "s"})
        assert not r.accepts("agent.loop", 9, {})  # only_in_turn

    def test_narrow_is_stricter_only(self, caplog):
        base = lh.LogRules(
            min_severity=13, exclude_prefixes=("a",), only_in_turn=False, events_content="inherit"
        )
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            r = lh.narrow_rules(
                base,
                {
                    "level": "DEBUG",  # weaker: refused
                    "exclude_loggers": ("b", "a"),
                    "logger_levels": {"x": "ERROR"},
                    "only_in_turn": True,
                    "events": {"content": "off"},
                },
                where="backends[t]",
                content_mode="full",
            )
        assert r.min_severity == 13 and r.exclude_prefixes == ("a", "b")
        assert r.logger_levels == (("x", 17),) and r.only_in_turn is True
        assert r.events_content == "off"
        assert any("below the global level" in m.getMessage() for m in caplog.records)
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            r2 = lh.narrow_rules(
                lh.LogRules(events_content="preview"),
                {"events": {"content": "full"}, "level": "ERROR"},
                where="backends[t]",
                content_mode="full",
            )
        assert r2.events_content == "preview" and r2.min_severity == 17
        assert any("wider than the global" in m.getMessage() for m in caplog.records)

    def test_no_overrides_returns_the_base(self):
        base = lh.LogRules(min_severity=9)
        assert lh.narrow_rules(base, None, where="b", content_mode="full") is base


def _two_backend_pipeline():
    """One provider, two capturing backends with different rules, like the real wiring."""
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    strict, lax = InMemoryLogExporter(), InMemoryLogExporter()
    provider = LoggerProvider(resource=Resource.create({}))
    provider.add_log_record_processor(lh.HermesLogProcessor(max_attribute_length=12))
    provider.add_log_record_processor(
        lh.BackendLogFilter(
            SimpleLogRecordProcessor(strict),
            lh.LogRules(min_severity=13, exclude_prefixes=("chatty",), only_in_turn=True),
            "strict",
        )
    )
    provider.add_log_record_processor(
        lh.BackendLogFilter(SimpleLogRecordProcessor(lax), lh.LogRules(), "lax")
    )
    handler = LoggingHandler(level=logging.DEBUG, logger_provider=provider)
    return provider, handler, strict, lax


class TestBackendFilter:
    def test_each_backend_gets_its_own_view_of_one_line(self):
        provider, handler, strict, lax = _two_backend_pipeline()
        lg = logging.getLogger("test.block.agent")
        chatty = logging.getLogger("chatty.lib")
        for l in (lg, chatty):
            l.propagate = False
            l.setLevel(logging.DEBUG)
            l.addHandler(handler)
        try:
            lg.info("info without a session")
            lg.warning("warning with a session", extra={"session_tag": " [s-1]"})
            lg.warning("warning without a session")
            chatty.error("excluded for strict")
        finally:
            for l in (lg, chatty):
                l.removeHandler(handler)
        strict_bodies = [r.log_record.body for r in strict.get_finished_logs()]
        lax_bodies = [r.log_record.body for r in lax.get_finished_logs()]
        assert strict_bodies == ["warning with a session"]
        assert lax_bodies == [
            "info without a session",
            "warning with a session",
            "warning without a session",
            "excluded for strict",
        ]

    def test_attribute_length_cap_applies_after_redaction(self):
        provider, handler, strict, lax = _two_backend_pipeline()
        lg = logging.getLogger("test.block.cap")
        lg.propagate = False
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)
        try:
            lg.info("x", extra={"long": "a" * 40, "short": "ok"})
        finally:
            lg.removeHandler(handler)
        (rec,) = lax.get_finished_logs()
        assert rec.log_record.attributes["long"] == "a" * 12 + "…"
        assert rec.log_record.attributes["short"] == "ok"

    def test_filter_delegates_shutdown_and_flush(self):
        class Inner:
            def __init__(self):
                self.calls = []

            def on_emit(self, r):
                self.calls.append(r)

            def shutdown(self):
                self.calls.append("shutdown")

            def force_flush(self, timeout_millis=30000):
                self.calls.append(("flush", timeout_millis))
                return True

        inner = Inner()
        f = lh.BackendLogFilter(inner, lh.LogRules(), "t")
        f.shutdown()
        assert f.force_flush(5) is True
        assert inner.calls == ["shutdown", ("flush", 5)]
        assert f.inner is inner and f.rules == lh.LogRules()


class TestBuildWithKnobs:
    def test_batch_knobs_and_rules_reach_the_processor(self, monkeypatch):
        from unittest.mock import MagicMock

        monkeypatch.setattr(lh, "OTLPLogExporter", MagicMock())
        backend = be._ResolvedBackend(
            type="otlp",
            endpoint="http://x/v1/traces",
            supports_logs=True,
            display_name="X",
            log_overrides={"level": "ERROR"},
        )
        ((proc, b),) = lh.build_log_processors(
            [backend],
            batch={
                "schedule_delay_ms": 250,
                "max_queue_size": 100,
                "max_export_batch_size": 10,
                "export_timeout_ms": 5000,
            },
            rules=lh.LogRules(min_severity=9),
            content_mode="full",
        )
        assert isinstance(proc, lh.BackendLogFilter)
        assert proc.rules.min_severity == 17  # ERROR, stricter than the global INFO
        inner = proc.inner
        bp = getattr(inner, "_batch_processor", inner)  # SDK >= 1.37 wraps a BatchProcessor
        assert bp._max_export_batch_size == 10 and bp._schedule_delay_millis == 250
        assert bp._max_queue_size == 100 and bp._export_timeout_millis == 5000
        inner.shutdown()

    def test_handler_filter_applies_logger_levels(self):
        f = lh._LoggerRulesFilter(
            lh.LogRules(exclude_prefixes=("httpx",), logger_levels=(("gateway", 13),))
        )

        def rec(name, level):
            return logging.LogRecord(name, level, __file__, 1, "m", None, None)

        assert f.filter(rec("agent.loop", logging.DEBUG))
        assert not f.filter(rec("httpx.client", logging.ERROR))
        assert not f.filter(rec("gateway.run", logging.INFO))
        assert f.filter(rec("gateway.run", logging.WARNING))
        assert lh._ExcludeOTelInternal().filter(rec("opentelemetry.sdk", logging.ERROR)) is False

    def test_level_to_severity_spellings(self):
        assert lh._level_to_severity("WARN", 1) == 13 == lh._level_to_severity("warning", 1)
        assert lh._level_to_severity("FATAL", 1) == 21 and lh._level_to_severity("30", 1) == 13
        assert lh._level_to_severity("nope", 7) == 7 and lh._level_to_severity(None, 7) == 7


class TestSeveritySpelling:
    def test_store_level_map_knows_otel_spellings(self):
        from hermes_otel.live_store import _level_no

        assert _level_no("WARN") == 30 == _level_no("WARNING")
        assert _level_no("FATAL") == 50 == _level_no("CRITICAL")

    def test_store_schema_is_v3(self, tmp_path):
        import sqlite3

        from hermes_otel.live_store import SCHEMA_VERSION, LiveStore

        assert SCHEMA_VERSION == 3
        store = LiveStore(db_path=str(tmp_path / "live.db"))
        store.close()
        with sqlite3.connect(str(tmp_path / "live.db")) as c:
            assert c.execute("PRAGMA user_version").fetchone()[0] == 3


class TestSdkHandlerGuard:
    """D8 (#240): the SDK deprecated LoggingHandler; this is where it is imported from."""

    def test_handler_import_path_still_resolves(self):
        from opentelemetry.sdk._logs import LoggingHandler

        assert lh._LOGS_AVAILABLE and lh.LoggingHandler is LoggingHandler


class TestEffectiveYamlBlock:
    def test_effective_yaml_renders_logs_as_a_block(self, tmp_path, monkeypatch):
        from hermes_otel import settings_report as sr

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv(pc.CONFIG_PATH_ENV, raising=False)
        monkeypatch.setattr(pc, "DURABLE_CONFIG_PATH", tmp_path / "hermes_otel.yaml")
        monkeypatch.setattr(
            pc, "DEFAULT_CONFIG_PATH", tmp_path / "plugins" / "hermes_otel" / "config.yaml"
        )
        (tmp_path / "hermes_otel.yaml").write_text(
            "logs:\n  capture: true\n  level: WARN\nbackends:\n  - type: phoenix\n"
            "    endpoint: http://x/v1/traces\n    logs: {level: ERROR}\n",
            encoding="utf-8",
        )
        report = sr.build_settings_report()
        text = report["effective_yaml"]
        assert "\nlogs:" in text and "  capture: true" in text and "  level: WARN" in text
        assert "\ncapture_logs:" not in text
        (b,) = report["fields"][[f["key"] for f in report["fields"]].index("backends")]["value"]
        assert b["log_overrides"] == {"level": "ERROR"}
        assert (
            "logs:\n      level: ERROR" in text
            or "logs: {level: ERROR}" in text
            or "level: ERROR" in text
        )
