"""Per-call cost from Hermes's own pricing (#252).

Hermes's ``post_api_request`` usage is ``asdict(CanonicalUsage)`` and carries
no ``cost``; the plugin prices each call with ``agent.usage_pricing.
estimate_usage_cost``. These tests stand in a fake of that module so the
statuses Hermes can return (actual / estimated / included / unknown) are
driven deterministically.
"""

import sys
import types
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

import pytest

from hermes_otel.hooks import (
    on_post_api_request,
    on_pre_api_request,
    on_session_end,
    on_session_start,
)
from hermes_otel.live_store import trace_totals
from hermes_otel.session_state import PerSession

pytestmark = pytest.mark.integration


@dataclass
class _CanonicalUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    request_count: int = 1
    raw_usage: Optional[dict] = None


@dataclass(frozen=True)
class _CostResult:
    amount_usd: Optional[Decimal]
    status: str
    source: str
    label: str = ""


class _FakePricing:
    """Returns the queued results in order; records every call."""

    def __init__(self, *results: Any):
        self.results = list(results)
        self.calls = []

    def estimate_usage_cost(self, model, usage, *, provider=None, base_url=None, api_key=None):
        self.calls.append(
            {"model": model, "usage": usage, "provider": provider, "base_url": base_url}
        )
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def pricing(monkeypatch):
    """Install a fake ``agent.usage_pricing``; call the returned setter with results."""

    def install(*results):
        fake = _FakePricing(*results)
        mod = types.ModuleType("agent.usage_pricing")
        mod.CanonicalUsage = _CanonicalUsage
        mod.estimate_usage_cost = fake.estimate_usage_cost
        pkg = types.ModuleType("agent")
        pkg.usage_pricing = mod
        monkeypatch.setitem(sys.modules, "agent", pkg)
        monkeypatch.setitem(sys.modules, "agent.usage_pricing", mod)
        return fake

    return install


# The shape Hermes sends: asdict(CanonicalUsage) minus raw_usage, plus the
# prompt_tokens / total_tokens properties (agent/api_request_hooks.py).
_USAGE = {
    "input_tokens": 100,
    "output_tokens": 20,
    "cache_read_tokens": 30,
    "cache_write_tokens": 0,
    "reasoning_tokens": 5,
    "request_count": 1,
    "prompt_tokens": 130,
    "total_tokens": 150,
}


def _api_call(task_id="t1", session_id="s1", usage=None):
    common = dict(
        task_id=task_id,
        session_id=session_id,
        platform="cli",
        model="openai/gpt-4o-mini",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_mode="chat_completions",
        api_call_count=1,
        message_count=1,
    )
    on_pre_api_request(
        **common, tool_count=0, approx_input_tokens=10, request_char_count=10, max_tokens=None
    )
    on_post_api_request(
        **common,
        api_duration=0.1,
        finish_reason="stop",
        response_model="openai/gpt-4o-mini",
        usage=dict(_USAGE if usage is None else usage),
        assistant_content_chars=5,
        assistant_tool_call_count=0,
    )


def _turn(n_calls=1, session_id="s1"):
    on_session_start(session_id=session_id, model="openai/gpt-4o-mini", platform="cli")
    for i in range(n_calls):
        _api_call(task_id=f"t{i}", session_id=session_id)
    on_session_end(
        session_id=session_id,
        completed=True,
        interrupted=False,
        model="openai/gpt-4o-mini",
        platform="cli",
    )


def _span(exporter, prefix):
    return [s for s in exporter.get_finished_spans() if s.name.startswith(prefix)]


def _cost_points(metric_reader):
    data = metric_reader.get_metrics_data()
    if data is None:
        return []
    return [
        (point.value, dict(point.attributes))
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "hermes.cost.usage"
        for point in metric.data.data_points
    ]


class TestApiSpanCost:
    def test_estimated_price_lands_on_span_and_metric(self, inmemory_otel_with_metrics, pricing):
        exporter, reader, _ = inmemory_otel_with_metrics
        fake = pricing(_CostResult(Decimal("0.000123"), "estimated", "provider_models_api"))
        _turn()

        (api,) = _span(exporter, "api.")
        assert api.attributes["hermes.cost.usage"] == pytest.approx(0.000123)
        assert api.attributes["hermes.cost.status"] == "estimated"
        assert api.attributes["hermes.cost.source"] == "provider_models_api"
        (point,) = _cost_points(reader)
        assert point[0] == pytest.approx(0.000123)
        assert point[1]["cost_status"] == "estimated"
        assert point[1]["model"] == "openai/gpt-4o-mini"
        assert point[1]["provider"] == "openrouter"

        # Hermes's own inputs: the CanonicalUsage fields only, with the route.
        (call,) = fake.calls
        assert call["usage"] == _CanonicalUsage(
            input_tokens=100, output_tokens=20, cache_read_tokens=30, reasoning_tokens=5
        )
        assert call["provider"] == "openrouter"
        assert call["base_url"] == "https://openrouter.ai/api/v1"

    def test_actual_status_is_labelled_actual(self, inmemory_otel_with_metrics, pricing):
        exporter, reader, _ = inmemory_otel_with_metrics
        pricing(_CostResult(Decimal("0.5"), "actual", "provider_cost_api"))
        _turn()
        assert _cost_points(reader)[0][1]["cost_status"] == "actual"
        (root,) = _span(exporter, "agent")
        assert root.attributes["hermes.cost.status"] == "actual"

    @pytest.mark.parametrize(
        "result",
        [
            _CostResult(None, "unknown", "none", "n/a"),
            _CostResult(Decimal("0"), "included", "none", "included"),
        ],
        ids=["unknown", "included"],
    )
    def test_unpriced_call_has_status_but_no_amount(
        self, inmemory_otel_with_metrics, pricing, result
    ):
        """A missing price or an included route never reads as $0 (#252)."""
        exporter, reader, _ = inmemory_otel_with_metrics
        pricing(result)
        _turn()

        (api,) = _span(exporter, "api.")
        assert api.attributes["hermes.cost.status"] == result.status
        assert "hermes.cost.usage" not in api.attributes
        assert _cost_points(reader) == []
        (root,) = _span(exporter, "agent")
        assert root.attributes["hermes.cost.status"] == result.status
        assert "hermes.cost.usage" not in root.attributes

    def test_no_pricing_module_means_no_cost_attributes(
        self, inmemory_otel_with_metrics, monkeypatch
    ):
        exporter, reader, _ = inmemory_otel_with_metrics
        monkeypatch.setitem(sys.modules, "agent.usage_pricing", None)  # ImportError
        _turn()
        (api,) = _span(exporter, "api.")
        assert not [k for k in api.attributes if k.startswith("hermes.cost")]
        assert _cost_points(reader) == []
        (root,) = _span(exporter, "agent")
        assert not [k for k in root.attributes if k.startswith("hermes.cost")]

    def test_estimator_error_fails_open(self, inmemory_otel_with_metrics, pricing):
        exporter, _, _ = inmemory_otel_with_metrics
        pricing(RuntimeError("pricing exploded"))
        _turn()
        (api,) = _span(exporter, "api.")
        assert api.attributes["gen_ai.usage.total_tokens"] == 150
        assert "hermes.cost.status" not in api.attributes

    def test_a_cost_field_in_the_payload_is_not_trusted(self, inmemory_otel_with_metrics, pricing):
        """Hermes never sends ``usage.cost``; the plugin prices the call itself."""
        exporter, reader, _ = inmemory_otel_with_metrics
        pricing(_CostResult(None, "unknown", "none", "n/a"))
        on_session_start(session_id="s1", model="m", platform="cli")
        _api_call(usage={**_USAGE, "cost": 9.99})
        assert _cost_points(reader) == []


class TestTurnRollup:
    def test_every_call_priced_sums_on_root(self, inmemory_otel_with_metrics, pricing):
        exporter, reader, _ = inmemory_otel_with_metrics
        pricing(
            _CostResult(Decimal("0.001"), "estimated", "provider_models_api"),
            _CostResult(Decimal("0.002"), "actual", "provider_cost_api"),
        )
        _turn(n_calls=2)
        (root,) = _span(exporter, "agent")
        assert root.attributes["hermes.cost.usage"] == pytest.approx(0.003)
        assert root.attributes["hermes.cost.status"] == "estimated"
        assert sum(v for v, _ in _cost_points(reader)) == pytest.approx(0.003)

    def test_mixed_turn_is_partial_without_a_total(self, inmemory_otel_with_metrics, pricing):
        exporter, _, _ = inmemory_otel_with_metrics
        pricing(
            _CostResult(Decimal("0.001"), "estimated", "provider_models_api"),
            _CostResult(None, "unknown", "none", "n/a"),
        )
        _turn(n_calls=2)
        (root,) = _span(exporter, "agent")
        assert root.attributes["hermes.cost.status"] == "partial"
        assert "hermes.cost.usage" not in root.attributes


class TestPerSessionRollup:
    def test_empty(self):
        assert PerSession().cost_rollup() == {}

    @pytest.mark.parametrize(
        "calls, expected",
        [
            (
                [("actual", 1.0), ("actual", 2.0)],
                {"hermes.cost.status": "actual", "hermes.cost.usage": 3.0},
            ),
            (
                [("actual", 1.0), ("estimated", 0.0)],
                {"hermes.cost.status": "estimated", "hermes.cost.usage": 1.0},
            ),
            ([("estimated", 1.0), ("included", None)], {"hermes.cost.status": "partial"}),
            ([("included", None), ("included", None)], {"hermes.cost.status": "included"}),
            ([("included", None), ("unknown", None)], {"hermes.cost.status": "unknown"}),
        ],
    )
    def test_rollup(self, calls, expected):
        ps = PerSession()
        for status, usd in calls:
            ps.add_cost(status, usd)
        assert ps.cost_rollup() == expected


class TestLiveTotals:
    def _spans(self, root_attrs):
        return [
            {"span_id": "r", "parent_span_id": None, "name": "agent", "attributes": root_attrs},
            {
                "span_id": "a",
                "parent_span_id": "r",
                "name": "api.m",
                "attributes": {"hermes.cost.usage": 0.25},
            },
        ]

    def test_partial_root_has_no_total(self):
        assert trace_totals(self._spans({"hermes.cost.status": "partial"}))["cost"] is None

    def test_root_total_wins(self):
        attrs = {"hermes.cost.status": "estimated", "hermes.cost.usage": 0.25}
        assert trace_totals(self._spans(attrs))["cost"] == 0.25

    def test_root_without_status_falls_back_to_api_sum(self):
        """Traces recorded before #252 carry no status; keep summing their API spans."""
        assert trace_totals(self._spans({}))["cost"] == 0.25
