"""Phoenix adapter: response parsing and request shape (moved out of the
contract test in #406; the shared invariants stay in test_dashboard_adapter_contract.py)."""

from __future__ import annotations

from helpers.dashboard import (  # noqa: F401
    CONTRACT_CFG as CFG,
)

from hermes_otel.dashboard.backends import (
    jaeger,
    langfuse,
    openobserve,
    phoenix,
    signoz,
    tempo,
    uptrace,
)
from hermes_otel.dashboard.backends.base import (
    StructuredFilter,
)

MODULES = {
    "phx": phoenix,
    "lf": langfuse,
    "jg": jaeger,
    "lgtm": tempo,
    "sz": signoz,
    "up": uptrace,
    "oo": openobserve,
}


# ── declarations and helpers ───────────────────────────────────────────


class TestPhoenix:
    def _adapter(self):
        return phoenix.PhoenixAdapter({"type": "phoenix", "endpoint": "http://localhost:6006"})

    def test_filter_condition_uses_the_documented_fields(self):
        fc = self._adapter()._build_filter_condition(
            StructuredFilter(name_prefix="tool.", status="error", min_duration_ms=10, raw="x == 1")
        )
        assert fc == "(x == 1) and 'tool.' in name and latency_ms >= 10 and status_code == 'ERROR'"

    def test_search_enforces_the_prefix_and_pages(self, monkeypatch):
        a = self._adapter()
        a._project_id_cache = "P1"
        seen = {}

        def node(name, tid, start):
            return {
                "spanId": tid,
                "name": name,
                "latencyMs": 1,
                "startTime": start,
                "parentId": None,
                "attributes": "{}",
                "context": {"traceId": tid, "spanId": tid},
                "trace": {"numSpans": 2},
            }

        def fake(url, body, headers=None, timeout=None):
            seen["vars"] = body.get("variables")
            spans = [
                node("tool.x", "t1", "2026-10-05T10:00:03+00:00"),
                node("agent", "t2", "2026-10-05T10:00:02+00:00"),
                node("tool.y", "t3", "2026-10-05T10:00:01+00:00"),
            ]
            return {
                "data": {"node": {"name": "p", "spans": {"edges": [{"node": s} for s in spans]}}}
            }

        monkeypatch.setattr(phoenix, "http_post_json", fake)
        out = a.search(StructuredFilter(name_prefix="tool."), 0, 1_800_000_000, 1)
        assert seen["vars"]["first"] == 2 and "'tool.' in name" in seen["vars"]["filterCondition"]
        assert [t["traceID"] for t in out["traces"]] == ["t1"] and out["has_more"] is True

    # ── Phoenix 20: ``rootSpansOnly`` is gone, roots are a filter predicate ──
    @staticmethod
    def _gql_fake(monkeypatch, spans_args, spans):
        """A Phoenix that answers the project list, the schema introspection
        (``spans_args``: the argument names of ``Project.spans``) and the
        span search (``spans``: node dicts)."""
        seen = {"bodies": []}

        def fake(url, body, headers=None, timeout=None):
            seen["bodies"].append(body)
            q = body.get("query", "")
            if "projects(" in q:
                return {
                    "data": {
                        "projects": {
                            "edges": [
                                {"node": {"id": "P1", "name": "hermes-minimal", "hasTraces": True}}
                            ]
                        }
                    }
                }
            if "__type" in q:
                return {
                    "data": {
                        "__type": {
                            "fields": [{"name": "spans", "args": [{"name": a} for a in spans_args]}]
                        }
                    }
                }
            return {
                "data": {
                    "node": {
                        "name": "hermes-minimal",
                        "spans": {"edges": [{"node": sp} for sp in spans]},
                    }
                }
            }

        monkeypatch.setattr(phoenix, "http_post_json", fake)
        monkeypatch.setattr(phoenix, "_ROOT_ARGS_CACHE", {})
        return seen

    @staticmethod
    def _node(tid, sid, name, start_ms, parent=None):
        return {
            "spanId": sid,
            "name": name,
            "latencyMs": 5,
            "statusCode": "OK",
            "startTime": f"2026-10-09T20:18:{start_ms:02d}.000+00:00",
            "endTime": f"2026-10-09T20:18:{start_ms + 1:02d}.000+00:00",
            "parentId": parent,
            "attributes": "{}",
            "context": {"traceId": tid, "spanId": sid},
            "trace": {"numSpans": 3},
        }

    def test_without_root_args_roots_become_a_parent_id_predicate(self, monkeypatch):
        seen = self._gql_fake(
            monkeypatch,
            ["first", "timeRange", "filterCondition", "sort", "traceFilterCondition"],
            [self._node("t1", "r1", "agent", 10)],
        )
        a = phoenix.PhoenixAdapter(
            {
                "type": "phoenix",
                "endpoint": "http://localhost:6006",
                "project_name": "hermes-minimal",
            }
        )
        out = a.search(StructuredFilter(status="error"), 0, 10, 5)
        search = seen["bodies"][-1]
        assert (
            "rootSpansOnly" not in search["query"] and "orphanSpanAsRootSpan" not in search["query"]
        )
        assert "rootsOnly" not in search["variables"]
        assert (
            search["variables"]["filterCondition"] == "parent_id is None and status_code == 'ERROR'"
        )
        assert [t["traceID"] for t in out["traces"]] == ["t1"]
        # a widened search has no predicate of ours and the schema is not probed again
        a.search(StructuredFilter(name_prefix="tool.", roots_only=False), 0, 10, 5)
        assert seen["bodies"][-1]["variables"]["filterCondition"] == "'tool.' in name"
        assert sum("__type" in b.get("query", "") for b in seen["bodies"]) == 1

    def test_with_root_args_the_legacy_variables_are_sent(self, monkeypatch):
        seen = self._gql_fake(
            monkeypatch,
            ["first", "rootSpansOnly", "orphanSpanAsRootSpan", "filterCondition", "sort"],
            [self._node("t1", "r1", "agent", 10)],
        )
        a = phoenix.PhoenixAdapter(
            {
                "type": "phoenix",
                "endpoint": "http://localhost:6006",
                "project_name": "hermes-minimal",
            }
        )
        a.search(StructuredFilter(status="error"), 0, 10, 5)
        search = seen["bodies"][-1]
        assert "rootSpansOnly: $rootsOnly" in search["query"]
        assert (
            search["variables"]["rootsOnly"] is True
            and search["variables"]["orphanAsRoot"] is False
        )
        assert search["variables"]["filterCondition"] == "status_code == 'ERROR'"

    def test_widened_search_is_one_card_per_trace_and_says_more_when_full(self, monkeypatch):
        # two tool spans of one trace, newest first, plus one of another
        spans = [
            self._node("t1", "s3", "tool.terminal", 30, parent="r1"),
            self._node("t1", "s2", "tool.terminal", 20, parent="r1"),
            self._node("t2", "s9", "tool.terminal", 15, parent="r2"),
            self._node("t1", "s1", "tool.terminal", 10, parent="r1"),
            self._node("t3", "s8", "tool.terminal", 5, parent="r3"),
        ]
        seen = self._gql_fake(monkeypatch, ["first", "filterCondition", "sort"], spans)
        a = phoenix.PhoenixAdapter(
            {
                "type": "phoenix",
                "endpoint": "http://localhost:6006",
                "project_name": "hermes-minimal",
            }
        )
        out = a.search(StructuredFilter(name_prefix="tool.", roots_only=False), 0, 100, 1)
        assert seen["bodies"][-1]["variables"]["first"] == 5  # 1 * 4 + 1
        assert [t["traceID"] for t in out["traces"]] == ["t1"]
        assert out["traces"][0]["spanSets"][0]["spans"][0]["name"] == "tool.terminal"
        assert out["has_more"] is True
        # the cursor sits below t1's OLDEST fetched span (s1 at :10), not the shown one (:30)
        assert out["next_before_ns"] == phoenix._iso_to_ns("2026-10-09T20:18:10.000+00:00")

    def test_detail_reports_truncation_and_the_whole_count(self, monkeypatch):
        a = self._adapter()
        a._project_id_cache = "P1"

        def fake(url, body, headers=None, timeout=None):
            assert body["variables"]["first"] == phoenix._DETAIL_SPAN_CAP
            return {
                "data": {
                    "node": {
                        "name": "p",
                        "trace": {
                            "numSpans": 900,
                            "spans": {
                                "pageInfo": {"hasNextPage": True},
                                "edges": [
                                    {
                                        "node": {
                                            "spanId": "s",
                                            "name": "agent",
                                            "attributes": "{}",
                                            "context": {"traceId": "t", "spanId": "s"},
                                        }
                                    }
                                ],
                            },
                        },
                    }
                }
            }

        monkeypatch.setattr(phoenix, "http_post_json", fake)
        out = a.get_trace("t")
        assert out["truncated"] is True and out["span_count"] == 900
