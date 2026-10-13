"""Cost and tokens per agent in the live store's trace rows (#447).

The parent's root keeps only the parent's own calls; each ``subagent.*`` span
carries its child's whole subtree. A row therefore reports the root's figures
plus the top-level delegations, and never sums a nested orchestrator's
grandchildren twice.
"""

from __future__ import annotations

from helpers.live import span

from hermes_otel.live_store import subagent_totals, summarize_trace, top_level_subagents

ROOT = {"hermes.session_id": "p", "gen_ai.usage.total_tokens": 100, "hermes.cost.usage": 0.01}


def _sub(
    sid,
    parent_span,
    parent_session,
    child_session,
    tokens=None,
    calls=None,
    cost=None,
    role="researcher",
):
    attrs = {
        "hermes.subagent.parent_session_id": parent_session,
        "hermes.subagent.child_session_id": child_session,
        "hermes.subagent.role": role,
    }
    if tokens is not None:
        attrs["hermes.subagent.usage.total_tokens"] = tokens
    if calls is not None:
        attrs["hermes.subagent.api_call_count"] = calls
    if cost is not None:
        attrs["hermes.subagent.cost.usage"] = cost
    return span("t", f"subagent.{role}", sid, parent_span, attrs)


def _delegating_trace():
    return [
        span("t", "agent", "r", None, ROOT),
        span("t", "llm.m", "l", "r", {"gen_ai.usage.total_tokens": 100}),
        span("t", "api.m", "a1", "l", {"gen_ai.usage.total_tokens": 100}),
        # an orchestrator child that delegated to a grandchild: its roll-up includes the grandchild
        _sub("s1", "l", "p", "c1", tokens=500, calls=4, cost=0.05, role="planner"),
        span(
            "t", "agent", "cr1", "s1", {"hermes.session_id": "c1", "gen_ai.usage.total_tokens": 300}
        ),
        _sub("s11", "cr1", "c1", "g1", tokens=200, calls=2, cost=0.02, role="coder"),
        _sub("s2", "l", "p", "c2", tokens=50, calls=1, cost=0.005),
    ]


class TestTopLevel:
    def test_nested_delegations_are_excluded(self):
        names = sorted(s["span_id"] for s in top_level_subagents(_delegating_trace()))
        assert names == ["s1", "s2"]

    def test_no_delegation(self):
        assert top_level_subagents([span("t", "agent", "r", None, ROOT)]) == []
        assert subagent_totals([span("t", "agent", "r", None, ROOT)]) is None


class TestTotals:
    def test_sums_top_level_only(self):
        assert subagent_totals(_delegating_trace()) == {
            "count": 2,
            "tokens": 550,
            "calls": 5,
            "cost": 0.055,
        }

    def test_cost_is_none_unless_every_delegation_is_priced(self):
        spans = [
            span("t", "agent", "r", None, ROOT),
            _sub("s1", "r", "p", "c1", tokens=10, cost=0.01),
            _sub("s2", "r", "p", "c2", tokens=20),
        ]
        assert subagent_totals(spans) == {"count": 2, "tokens": 30, "calls": None, "cost": None}

    def test_still_running_delegation_counts_without_figures(self):
        spans = [span("t", "agent", "r", None, ROOT), _sub("s1", "r", "p", "c1")]
        assert subagent_totals(spans) == {"count": 1, "tokens": None, "calls": None, "cost": None}


class TestRows:
    def test_row_keeps_own_figures_and_reports_subagents(self):
        row = summarize_trace(_delegating_trace())
        assert row["tokens"] == 100 and row["cost"] == 0.01
        assert row["subagents"] == {"count": 2, "tokens": 550, "calls": 5, "cost": 0.055}

    def test_plain_turn_has_no_subagents(self):
        row = summarize_trace(
            [
                span("t", "agent", "r", None, ROOT),
                span("t", "api.m", "a", "r", {"gen_ai.usage.total_tokens": 100}),
            ]
        )
        assert row["subagents"] is None

    def test_partial_trace_is_not_split(self):
        """No root yet: the totals are the api.* sum (children's calls included), so no split."""
        spans = [s for s in _delegating_trace() if s["span_id"] != "r"]
        row = summarize_trace(spans)
        assert row["partial"] is True and row["subagents"] is None

    def test_kanban_run_reads_the_api_sum_and_is_not_split(self):
        """A run rooted at kanban.claim (#388) has no totals on its root: the card
        reads the worker's api.* calls, and a split would count a delegating
        worker's children twice."""
        spans = [
            span("t", "kanban.claim", "k", None, {"hermes.kanban.task_id": "t1"}),
            span(
                "t", "agent", "w", "k", {"hermes.session_id": "w", "gen_ai.usage.total_tokens": 70}
            ),
            span("t", "api.m", "a1", "w", {"gen_ai.usage.total_tokens": 30}),
            span("t", "api.m", "a2", "w", {"gen_ai.usage.total_tokens": 40}),
            _sub("s1", "w", "w", "c1", tokens=25),
            span(
                "t",
                "api.m",
                "a3",
                "s1",
                {"gen_ai.usage.total_tokens": 25, "hermes.query_source": "subagent"},
            ),
        ]
        row = summarize_trace(spans)
        assert row["tokens"] == 95
        assert row["subagents"] is None


class TestStoreQueries:
    def test_query_traces_and_sessions_carry_the_split(self, store):
        for s in _delegating_trace():
            store.add_span(s)
        row = store.query_traces()["traces"][0]
        assert row["subagents"]["tokens"] == 550
        sess = store.sessions()["sessions"][0]
        assert sess["session"] == "p"
        assert sess["tokens"] == 100 and sess["subagentTokens"] == 550

    def test_session_without_delegation(self, store):
        store.add_span(span("t", "agent", "r", None, ROOT))
        assert store.sessions()["sessions"][0]["subagentTokens"] is None
