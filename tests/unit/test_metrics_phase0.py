"""Metrics phase 0 (#233): bucket boundaries, the label allow-list and cap,
per-backend temporality, exponential histograms, exemplars and one-shot export."""

from __future__ import annotations

import dataclasses
import math
from typing import List

import pytest
from opentelemetry.sdk.metrics import (
    Counter,
    Histogram,
    MeterProvider,
    ObservableGauge,
    UpDownCounter,
)
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    InMemoryMetricReader,
    MetricExporter,
    MetricExportResult,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.metrics.view import ExponentialBucketHistogramAggregation

from hermes_otel import tracer as tracer_mod
from hermes_otel.backends import resolve
from hermes_otel.plugin_config import (
    BackendConfig,
    _reconcile_metrics_settings,
    normalize_temporality,
)
from hermes_otel.tracer import (
    _B_OP_SECONDS,
    _B_TOKENS,
    _B_TOOL_MS,
    _INSTRUMENTS,
    metric_preferred_aggregation,
    metric_temporality_map,
    metric_views,
)


def _points(reader, name):
    data = reader.get_metrics_data()
    out = []
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    out.extend(m.data.data_points)
    return out


def _bucket_index(boundaries, value):
    for i, b in enumerate(boundaries):
        if value <= b:
            return i
    return len(boundaries)


# ── buckets ───────────────────────────────────────────────────────────────


class TestBuckets:
    def test_every_histogram_declares_boundaries(self):
        for key, spec in _INSTRUMENTS.items():
            if spec.kind == "histogram":
                assert spec.boundaries, f"{key} has no bucket boundaries"
                assert list(spec.boundaries) == sorted(spec.boundaries)

    def test_seconds_histogram_uses_the_spec_boundaries(self, inmemory_otel_with_metrics):
        _, reader, plugin = inmemory_otel_with_metrics
        plugin.record_metric(
            "gen_ai.client.operation.duration", 4.2, {"gen_ai.operation.name": "chat"}
        )
        (point,) = _points(reader, "gen_ai.client.operation.duration")
        assert list(point.explicit_bounds) == list(_B_OP_SECONDS)
        # 4.2 s lands in the (2.56, 5.12] bucket, not in the SDK's default [0, 5] one
        idx = _bucket_index(_B_OP_SECONDS, 4.2)
        assert point.bucket_counts[idx] == 1 and sum(point.bucket_counts) == 1

    def test_token_histogram_does_not_overflow_at_ten_thousand(self, inmemory_otel_with_metrics):
        _, reader, plugin = inmemory_otel_with_metrics
        plugin.record_metric(
            "gen_ai.client.token.usage",
            12_000,
            {"gen_ai.token.type": "input", "gen_ai.operation.name": "chat"},
        )
        (point,) = _points(reader, "gen_ai.client.token.usage")
        assert list(point.explicit_bounds) == list(_B_TOKENS)
        assert point.bucket_counts[_bucket_index(_B_TOKENS, 12_000)] == 1
        assert point.bucket_counts[-1] == 0  # nothing in +Inf

    def test_deprecated_ms_histograms_keep_ms_buckets_next_to_seconds(
        self, inmemory_otel_with_metrics
    ):
        _, reader, plugin = inmemory_otel_with_metrics
        plugin.record_metric(
            "tool_duration", 350.0, {"tool_name": "bash", "gen_ai.tool.name": "bash"}
        )
        plugin.record_metric("tool_duration_s", 0.35, {"gen_ai.tool.name": "bash"})
        (ms,) = _points(reader, "hermes.tool.duration")
        (s,) = _points(reader, "gen_ai.execute_tool.duration")
        assert list(ms.explicit_bounds) == list(_B_TOOL_MS)
        assert list(s.explicit_bounds) == list(_B_OP_SECONDS)
        assert (
            _INSTRUMENTS["tool_duration"].deprecated
            and not _INSTRUMENTS["tool_duration_s"].deprecated
        )

    def test_exponential_mode_leaves_boundaries_to_the_reader(self):
        explicit = metric_views("explicit")
        exponential = metric_views("exponential")
        assert len(explicit) == len(exponential) == len(_INSTRUMENTS)
        from opentelemetry.sdk.metrics.view import (
            DefaultAggregation,
            ExplicitBucketHistogramAggregation,
        )

        assert any(isinstance(v._aggregation, ExplicitBucketHistogramAggregation) for v in explicit)
        assert all(isinstance(v._aggregation, DefaultAggregation) for v in exponential)
        agg = metric_preferred_aggregation("exponential")
        assert isinstance(agg[Histogram], ExponentialBucketHistogramAggregation)
        assert metric_preferred_aggregation("explicit") is None

    def test_exponential_histogram_end_to_end(self):
        reader = InMemoryMetricReader(
            preferred_aggregation=metric_preferred_aggregation("exponential")
        )
        provider = MeterProvider(metric_readers=[reader], views=metric_views("exponential"))
        h = provider.get_meter("t").create_histogram("gen_ai.client.operation.duration", unit="s")
        h.record(4.2, {"gen_ai.operation.name": "chat"})
        (point,) = _points(reader, "gen_ai.client.operation.duration")
        assert hasattr(point, "positive") and point.count == 1
        provider.shutdown()


# ── labels ────────────────────────────────────────────────────────────────


class TestLabels:
    def test_views_cover_every_instrument_and_keep_profile(self):
        views = metric_views()
        names = {v._instrument_name for v in views}
        assert names == {spec.name for spec in _INSTRUMENTS.values()}
        for v in views:
            assert "profile" in v._attribute_keys

    def test_unknown_labels_are_dropped(self, inmemory_otel_with_metrics):
        _, reader, plugin = inmemory_otel_with_metrics
        plugin.record_metric(
            "session_count", 1, {"platform": "cli", "session_id": "abc123", "user.email": "x@y"}
        )
        (point,) = _points(reader, "hermes.session.count")
        assert dict(point.attributes) == {"platform": "cli", "profile": plugin.profile_name}

    def test_label_cap_folds_extra_values_into_other(self, inmemory_otel_with_metrics):
        _, reader, plugin = inmemory_otel_with_metrics
        plugin.config = dataclasses.replace(plugin.config, metrics_label_limit=2)
        plugin._label_values = {}
        for m in ("m-a", "m-b", "m-c", "m-a", "m-d"):
            plugin.record_metric("token_usage", 1, {"model": m, "token_type": "input"})
        models = sorted(dict(p.attributes)["model"] for p in _points(reader, "hermes.token.usage"))
        assert models == ["m-a", "m-b", "other"]
        by_model = {
            dict(p.attributes)["model"]: p.value for p in _points(reader, "hermes.token.usage")
        }
        assert by_model == {"m-a": 2, "m-b": 1, "other": 2}

    def test_label_cap_can_be_disabled(self, inmemory_otel_with_metrics):
        _, reader, plugin = inmemory_otel_with_metrics
        plugin.config = dataclasses.replace(plugin.config, metrics_label_limit=0)
        plugin._label_values = {}
        for m in ("a", "b", "c"):
            plugin.record_metric("token_usage", 1, {"model": m, "token_type": "input"})
        assert sorted(
            dict(p.attributes)["model"] for p in _points(reader, "hermes.token.usage")
        ) == ["a", "b", "c"]


# ── temporality ───────────────────────────────────────────────────────────


class TestTemporality:
    def test_maps(self):
        assert metric_temporality_map(None) is None
        delta = metric_temporality_map("delta")
        assert delta[Counter] is AggregationTemporality.DELTA
        assert delta[Histogram] is AggregationTemporality.DELTA
        assert delta[UpDownCounter] is AggregationTemporality.CUMULATIVE
        assert delta[ObservableGauge] is AggregationTemporality.CUMULATIVE
        cumulative = metric_temporality_map("cumulative")
        assert all(v is AggregationTemporality.CUMULATIVE for v in cumulative.values())

    def test_normalize(self):
        assert normalize_temporality("Delta") == "delta"
        assert normalize_temporality(" CUMULATIVE ") == "cumulative"
        assert normalize_temporality("") is None
        assert normalize_temporality(None) is None
        assert normalize_temporality("bogus") is None

    def test_config_reconcile(self):
        values = {
            "metrics_temporality": "DELTA",
            "metrics_histogram": "nope",
            "metrics_label_limit": "many",
        }
        _reconcile_metrics_settings(values)
        assert values == {
            "metrics_temporality": "delta",
            "metrics_histogram": "explicit",
            "metrics_label_limit": 100,
        }
        values = {"metrics_histogram": "Exponential", "metrics_label_limit": -5}
        _reconcile_metrics_settings(values)
        assert values["metrics_histogram"] == "exponential" and values["metrics_label_limit"] == 0

    @pytest.mark.parametrize(
        "backend_type, expected",
        [
            ("signoz", "delta"),
            ("uptrace", "delta"),
            ("lgtm", None),
            ("openobserve", None),
            ("otlp", None),
        ],
    )
    def test_backend_presets(self, backend_type, expected, monkeypatch):
        monkeypatch.setenv("OPENOBSERVE_PASSWORD", "x")
        rb = resolve(
            BackendConfig(
                type=backend_type,
                endpoint="http://localhost:4318/v1/traces",
                user="root@example.com",
                password_env="OPENOBSERVE_PASSWORD",
                dsn="http://project@localhost:14318/1",
            )
        )
        assert rb.metrics_temporality == expected

    def test_explicit_backend_value_beats_preset_and_bad_values_fall_back(self):
        rb = resolve(
            BackendConfig(
                type="signoz", endpoint="http://x/v1/traces", metrics_temporality="Cumulative"
            )
        )
        assert rb.metrics_temporality == "cumulative"
        rb = resolve(
            BackendConfig(type="signoz", endpoint="http://x/v1/traces", metrics_temporality="weird")
        )
        assert rb.metrics_temporality == "delta"

    def test_two_readers_one_provider_can_differ(self):
        delta_reader = InMemoryMetricReader(preferred_temporality=metric_temporality_map("delta"))
        cumulative_reader = InMemoryMetricReader(
            preferred_temporality=metric_temporality_map("cumulative")
        )
        provider = MeterProvider(
            metric_readers=[delta_reader, cumulative_reader], views=metric_views()
        )
        c = provider.get_meter("t").create_counter("hermes.token.usage", unit="{token}")
        c.add(3, {"token_type": "input"})
        assert _points(delta_reader, "hermes.token.usage")[0].value == 3
        assert _points(cumulative_reader, "hermes.token.usage")[0].value == 3
        c.add(2, {"token_type": "input"})
        assert _points(delta_reader, "hermes.token.usage")[0].value == 2
        assert _points(cumulative_reader, "hermes.token.usage")[0].value == 5
        provider.shutdown()

    def test_exporter_gets_the_backend_temporality(self, monkeypatch, tmp_path):
        """Full init with a delta-preset backend hands ``preferred_temporality`` to the exporter."""
        captured: List[dict] = []

        class FakeMetricExporter:
            def __init__(self, **kwargs):
                captured.append(kwargs)

            def export(self, *a, **k):
                return MetricExportResult.SUCCESS

            def force_flush(self, *a, **k):
                return True

            def shutdown(self, *a, **k):
                return None

            @property
            def _preferred_temporality(self):
                return captured[-1].get("preferred_temporality") or {}

            @property
            def _preferred_aggregation(self):
                return captured[-1].get("preferred_aggregation") or {}

        from hermes_otel.plugin_config import HermesOtelConfig

        class FakeSpanExporter:
            def __init__(self, **kwargs):
                pass

            def export(self, *a, **k):
                from opentelemetry.sdk.trace.export import SpanExportResult

                return SpanExportResult.SUCCESS

            def force_flush(self, *a, **k):
                return True

            def shutdown(self, *a, **k):
                return None

        monkeypatch.setattr(tracer_mod, "OTLPMetricExporter", FakeMetricExporter)
        monkeypatch.setattr(tracer_mod, "OTLPSpanExporter", FakeSpanExporter)
        monkeypatch.setattr(
            tracer_mod,
            "PeriodicExportingMetricReader",
            lambda exporter, **kw: InMemoryMetricReader(
                preferred_temporality=exporter._preferred_temporality,
                preferred_aggregation=exporter._preferred_aggregation,
            ),
        )
        monkeypatch.setattr(
            "hermes_otel.live_store._default_db_path", lambda: str(tmp_path / "l.db")
        )
        cfg = HermesOtelConfig(
            backends=[
                BackendConfig(
                    type="signoz",
                    endpoint="http://127.0.0.1:9/v1/traces",
                    metrics=True,
                )
            ],
            dashboard_live=False,
            host_metrics=False,
        )
        plugin = tracer_mod.HermesOTelPlugin(config=cfg)
        try:
            assert plugin.init() is True
            assert captured, "no metric exporter was created"
            temporality = captured[-1]["preferred_temporality"]
            assert temporality[Counter] is AggregationTemporality.DELTA
            assert captured[-1]["preferred_aggregation"] is None
        finally:
            plugin.shutdown()


# ── exemplars and one-shot export ─────────────────────────────────────────


class TestExemplarsAndExport:
    def test_histogram_recorded_inside_a_span_carries_an_exemplar(self, inmemory_otel_with_metrics):
        _, reader, plugin = inmemory_otel_with_metrics
        with plugin.tracer.start_as_current_span("api.test") as span:
            plugin.record_metric(
                "gen_ai.client.operation.duration", 1.5, {"gen_ai.operation.name": "chat"}
            )
            trace_id = span.get_span_context().trace_id
        (point,) = _points(reader, "gen_ai.client.operation.duration")
        assert point.exemplars, "no exemplar attached inside a sampled span"
        assert point.exemplars[0].trace_id == trace_id

    def test_one_shot_process_exports_on_shutdown(self):
        """A 30 s CLI never reaches the 60 s tick; the flush at shutdown must export what was recorded."""

        class Capture(MetricExporter):
            def __init__(self):
                super().__init__()
                self.batches = []

            def export(self, metrics_data, timeout_millis=10_000, **kwargs):
                self.batches.append(metrics_data)
                return MetricExportResult.SUCCESS

            def force_flush(self, timeout_millis=10_000):
                return True

            def shutdown(self, timeout_millis=30_000, **kwargs):
                return None

        exporter = Capture()
        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=math.inf)
        provider = MeterProvider(metric_readers=[reader], views=metric_views())
        provider.get_meter("t").create_counter("hermes.session.count").add(1, {"platform": "cli"})
        assert exporter.batches == []
        # What the plugin's shutdown() does: force_flush (collect + export) then shutdown.
        provider.force_flush()
        provider.shutdown()
        names = [
            m.name
            for batch in exporter.batches
            for rm in batch.resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
        ]
        assert "hermes.session.count" in names


class TestTurnEndFlush:
    def test_background_flush_covers_metric_and_log_providers(self):
        """The turn-end flush is the only export a one-shot run gets (#233)."""
        from unittest.mock import MagicMock

        plugin = tracer_mod.HermesOTelPlugin()
        plugin._span_processors = [MagicMock()]
        plugin._meter_provider = MagicMock()
        plugin._logger_provider = MagicMock()
        try:
            assert plugin.flush_async(timeout_millis=200) is True
            plugin.flush_wait(timeout_s=5)
            plugin._span_processors[0].force_flush.assert_called_once()
            plugin._meter_provider.force_flush.assert_called_once_with(timeout_millis=200)
            plugin._logger_provider.force_flush.assert_called_once_with(timeout_millis=200)
        finally:
            executor, plugin._flush_executor = plugin._flush_executor, None
            if executor is not None:
                executor.shutdown(wait=True)
