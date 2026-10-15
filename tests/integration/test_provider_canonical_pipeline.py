"""#475: an API call from a provider with a convention spelling lands with it on
the api span, the llm span, the turn root and the metric label; the raw id
stays in ``hermes.provider``."""

from __future__ import annotations

from _helpers import get_metric, metric_points
from helpers.turns import run_turn


def _by_name(spans):
    return {s.name.split(".")[0]: s for s in spans}


class TestPipeline:
    def test_mapped_provider_on_every_span(self, inmemory_otel_with_metrics):
        exporter, reader, _ = inmemory_otel_with_metrics
        run_turn("s1", first=True, provider="xai", model="grok-4")
        spans = _by_name(exporter.get_finished_spans())
        for name in ("api", "llm", "agent"):
            a = spans[name].attributes
            assert a["gen_ai.provider.name"] == a["gen_ai.system"] == "x_ai", name
            assert a["llm.provider"] == "xai", name
            assert a["hermes.provider"] == "xai", name
        points = metric_points(get_metric(reader, "hermes.token.usage"))
        assert points and {p.attributes["provider"] for p in points} == {"x_ai"}

    def test_unmapped_provider_is_unchanged(self, inmemory_otel_with_metrics):
        exporter, reader, _ = inmemory_otel_with_metrics
        run_turn("s1", first=True, provider="openrouter", model="m")
        a = _by_name(exporter.get_finished_spans())["api"].attributes
        assert (
            a["gen_ai.provider.name"] == a["llm.provider"] == a["hermes.provider"] == "openrouter"
        )
        points = metric_points(get_metric(reader, "hermes.token.usage"))
        assert {p.attributes["provider"] for p in points} == {"openrouter"}
