"""#487: numeric settings are range-checked at load; out of range warns and keeps the default.

Before, a ``0`` queue size or a sample rate above 1 reached the SDK, which
raised inside the pipeline init, so every backend logged "traces init
failed" and the run exported nothing.
"""

from __future__ import annotations

import dataclasses
import logging

import pytest

from hermes_otel.plugin_config import NUMERIC_BOUNDS, HermesOtelConfig, load_config

_DEFAULTS = {f.name: f.default for f in dataclasses.fields(HermesOtelConfig)}


def _warned(caplog, name: str) -> bool:
    return any(name in r.getMessage() and "must be" in r.getMessage() for r in caplog.records)


class TestTable:
    def test_every_bounded_field_exists_and_is_numeric(self):
        kinds = {f.name: f for f in dataclasses.fields(HermesOtelConfig)}
        for name in NUMERIC_BOUNDS:
            assert name in kinds, name
            default = kinds[name].default
            assert default is None or isinstance(default, (int, float)), name

    def test_every_numeric_field_is_bounded(self):
        """A new numeric knob must declare its bounds (or be listed here as exempt)."""
        numeric = {
            f.name
            for f in dataclasses.fields(HermesOtelConfig)
            if isinstance(f.default, (int, float)) and not isinstance(f.default, bool)
        }
        optional_numeric = {
            f.name
            for f in dataclasses.fields(HermesOtelConfig)
            if f.default is None and str(f.type) in ("Optional[int]", "Optional[float]")
        }
        assert numeric | optional_numeric <= set(NUMERIC_BOUNDS)

    def test_defaults_are_within_bounds(self):
        for name, (lo, hi) in NUMERIC_BOUNDS.items():
            d = _DEFAULTS[name]
            if d is None:
                continue
            assert d >= lo and (hi is None or d <= hi), name


class TestDirectConstruction:
    @pytest.mark.parametrize("name", sorted(NUMERIC_BOUNDS))
    def test_below_minimum_warns_and_keeps_default(self, name, caplog):
        lo, _hi = NUMERIC_BOUNDS[name]
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = HermesOtelConfig(**{name: lo - 1})
        assert getattr(cfg, name) == _DEFAULTS[name]
        assert _warned(caplog, name)

    @pytest.mark.parametrize("name", sorted(NUMERIC_BOUNDS))
    def test_minimum_itself_is_kept(self, name, caplog):
        lo, _hi = NUMERIC_BOUNDS[name]
        if name.endswith("max_export_batch_size"):
            pytest.skip("clamped to the queue size, covered below")
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = HermesOtelConfig(**{name: lo})
        assert getattr(cfg, name) == lo
        assert not _warned(caplog, name)

    def test_above_maximum_warns_and_keeps_default(self, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = HermesOtelConfig(sample_rate=1.5)
        assert cfg.sample_rate is None
        assert _warned(caplog, "sample_rate")

    def test_optional_none_stays_none(self, caplog):
        cfg = HermesOtelConfig(span_batch_max_export_batch_size=None, sample_rate=None)
        assert cfg.span_batch_max_export_batch_size is None and cfg.sample_rate is None
        assert not caplog.records

    def test_bool_is_not_a_number(self, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = HermesOtelConfig(flush_interval_ms=True)  # type: ignore[arg-type]
        assert cfg.flush_interval_ms == _DEFAULTS["flush_interval_ms"]
        assert _warned(caplog, "flush_interval_ms")

    def test_zero_is_allowed_where_documented(self, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = HermesOtelConfig(force_flush_wait_ms=0, metrics_label_limit=0)
        assert cfg.force_flush_wait_ms == 0 and cfg.metrics_label_limit == 0
        assert not caplog.records

    @pytest.mark.parametrize(
        "batch,queue",
        [
            ("span_batch_max_export_batch_size", "span_batch_max_queue_size"),
            ("log_batch_max_export_batch_size", "log_batch_max_queue_size"),
        ],
    )
    def test_export_batch_is_clamped_to_the_queue(self, batch, queue, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = HermesOtelConfig(**{batch: 5000, queue: 100})
        assert getattr(cfg, batch) == 100
        assert any(batch in r.getMessage() and "exceeds" in r.getMessage() for r in caplog.records)


class TestLoadPaths:
    def test_yaml_out_of_range_warns_and_keeps_default(self, tmp_path, caplog):
        pytest.importorskip("yaml")
        path = tmp_path / "hermes_otel.yaml"
        path.write_text("span_batch_max_queue_size: 0\nsample_rate: 2\nroot_span_ttl_ms: -5\n")
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = load_config(path=path)
        assert cfg.span_batch_max_queue_size == _DEFAULTS["span_batch_max_queue_size"]
        assert cfg.sample_rate is None
        assert cfg.root_span_ttl_ms == _DEFAULTS["root_span_ttl_ms"]
        for name in ("span_batch_max_queue_size", "sample_rate", "root_span_ttl_ms"):
            assert _warned(caplog, name)

    def test_env_out_of_range_warns_and_keeps_default(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv("HERMES_OTEL_FLUSH_INTERVAL_MS", "0")
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.flush_interval_ms == _DEFAULTS["flush_interval_ms"]
        assert _warned(caplog, "flush_interval_ms")

    def test_pipeline_init_survives_a_zero_queue_size(self, tmp_path, monkeypatch):
        """The SDK used to raise on max_queue_size=0 and every backend failed."""
        from unittest.mock import patch

        from _helpers import clear_backend_env

        from hermes_otel.backend_config import BackendConfig
        from hermes_otel.tracer import HermesOTelPlugin

        clear_backend_env(monkeypatch)
        cfg = HermesOtelConfig(
            span_batch_max_queue_size=0,
            backends=(BackendConfig(type="jaeger", endpoint="http://jaeger:4318/v1/traces"),),
        )
        plugin = HermesOTelPlugin(config=cfg)
        with (
            patch("hermes_otel.pipeline.OTLPSpanExporter"),
            patch("hermes_otel.pipeline.trace.set_tracer_provider"),
            patch("hermes_otel.pipeline.metrics.set_meter_provider"),
        ):
            assert plugin.init() is True
        assert len(plugin._span_processors) == 1
