"""Pure helpers and per-session accounting for auxiliary LLM calls (#389)."""

from __future__ import annotations

from hermes_otel.helpers import (
    QUERY_SOURCE_AUXILIARY,
    QUERY_SOURCE_MAIN,
    QUERY_SOURCE_SUBAGENT,
    auxiliary_span_key,
    auxiliary_task_name,
    query_source_for,
)
from hermes_otel.session_state import PerSession


class TestSpanKey:
    def test_request_id_wins(self):
        assert auxiliary_span_key("req-1", "titling", 12.5) == "aux:req-1"

    def test_falls_back_to_task_and_started_at(self):
        assert auxiliary_span_key("", "compression", 12.5) == "aux:compression:12.5"
        assert auxiliary_span_key(None, "", None) == "aux:unknown:"

    def test_pre_and_post_agree(self):
        # Hermes builds one base payload per attempt, so both hooks see the
        # same api_request_id / aux_task / started_at.
        base = {"api_request_id": "", "aux_task": "moa_reference", "started_at": 99.0}
        assert auxiliary_span_key(**base) == auxiliary_span_key(**base)


class TestTaskName:
    def test_lowercased_and_bounded(self):
        assert auxiliary_task_name(" MoA_Reference ") == "moa_reference"
        assert len(auxiliary_task_name("x" * 500)) == 83  # 80 chars + the ellipsis

    def test_unknown_when_missing(self):
        assert auxiliary_task_name(None) == "unknown"
        assert auxiliary_task_name("") == "unknown"


class TestQuerySource:
    def test_values(self):
        assert query_source_for(False) == QUERY_SOURCE_MAIN == "main"
        assert query_source_for(True) == QUERY_SOURCE_SUBAGENT == "subagent"
        assert QUERY_SOURCE_AUXILIARY == "auxiliary"


class TestAuxiliaryRollup:
    def test_empty_when_no_call(self):
        assert PerSession().auxiliary_rollup() == {}

    def test_counts_tokens_and_priced_cost(self):
        ps = PerSession()
        ps.add_auxiliary(
            {"prompt_tokens": 10, "completion_tokens": 5},
            {"status": "estimated", "usd": 0.001},
            failed=False,
        )
        ps.add_auxiliary(
            {"prompt_tokens": 20, "completion_tokens": 1},
            {"status": "estimated", "usd": 0.002},
            failed=False,
        )
        assert ps.auxiliary_rollup() == {
            "hermes.turn.auxiliary.api_call_count": 2,
            "hermes.turn.auxiliary.input_tokens": 30,
            "hermes.turn.auxiliary.output_tokens": 6,
            "hermes.turn.auxiliary.cost.status": "estimated",
            "hermes.turn.auxiliary.cost.usage": 0.003,
        }

    def test_partial_pricing_has_no_total(self):
        ps = PerSession()
        ps.add_auxiliary({"prompt_tokens": 1}, {"status": "actual", "usd": 0.5}, failed=False)
        ps.add_auxiliary({"prompt_tokens": 1}, {"status": "unknown", "usd": None}, failed=False)
        rollup = ps.auxiliary_rollup()
        assert rollup["hermes.turn.auxiliary.cost.status"] == "partial"
        assert "hermes.turn.auxiliary.cost.usage" not in rollup

    def test_failed_attempt_without_usage(self):
        ps = PerSession()
        ps.add_auxiliary(None, None, failed=True)
        assert ps.auxiliary_rollup() == {
            "hermes.turn.auxiliary.api_call_count": 1,
            "hermes.turn.auxiliary.error_count": 1,
        }

    def test_kept_apart_from_main_totals(self):
        ps = PerSession()
        ps.add_auxiliary({"prompt_tokens": 10, "completion_tokens": 5}, None, failed=False)
        assert ps.usage["prompt_tokens"] == 0
        assert ps.usage_updated is False
        assert ps.cost_statuses == []
