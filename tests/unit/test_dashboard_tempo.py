"""Tempo adapter: response parsing and request shape (moved out of the
contract test in #406; the shared invariants stay in test_dashboard_adapter_contract.py)."""

from __future__ import annotations

import json
from urllib import parse as _urlparse

import pytest
from helpers.dashboard import (  # noqa: F401
    CONTRACT_CFG as CFG,
)
from helpers.dashboard import (
    NS,
)

from hermes_otel.dashboard.backends import (
    _loki,
    _prometheus,
    jaeger,
    langfuse,
    openobserve,
    phoenix,
    signoz,
    tempo,
    uptrace,
)
from hermes_otel.dashboard.backends.base import (
    BackendError,
    LogFilter,
    StructuredFilter,
    split_applied_filters,
)
from hermes_otel.dashboard.backends.metrics_math import fold_samples

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


class TestTempo:
    def _adapter(self):
        return tempo.TempoAdapter({"type": "lgtm", "endpoint": "http://localhost:4318/v1/traces"})

    def test_predicates_include_prefix_duration_and_status(self):
        q = self._adapter()._build_traceql(
            StructuredFilter(name_prefix="tool.", min_duration_ms=250, status="ok", service="s")
        )
        assert q.startswith(
            '{ nestedSetParent < 0 && resource.service.name = "s" && name =~ "^tool\\\\..*" && status = ok && duration >= 250ms }'
        )  # Tempo anchors the regex at both ends, so the prefix carries ``.*``
        assert "| select(" in q

    def test_a_4xx_retries_without_select_but_with_every_predicate(self, monkeypatch):
        urls = []

        def fake(url, headers=None, timeout=None):
            urls.append(url)
            if len(urls) == 1:
                raise BackendError(
                    502, "Backend returned 400: parse error at select", http_status=400
                )
            return {"traces": []}

        monkeypatch.setattr(tempo, "http_get_json", fake)
        self._adapter().search(StructuredFilter(status="error", name_prefix="agent"), 0, 10, 5)
        assert len(urls) == 2
        second = _urlparse.unquote_plus(urls[1])
        assert "status = error" in second and 'name =~ "^agent.*"' in second
        assert "select(" not in second

    def test_unreachable_is_not_retried(self, monkeypatch):
        calls = []

        def fake(url, headers=None, timeout=None):
            calls.append(url)
            raise BackendError(502, "Backend unreachable: refused")

        monkeypatch.setattr(tempo, "http_get_json", fake)
        with pytest.raises(BackendError, match="unreachable"):
            self._adapter().search(StructuredFilter(), 0, 10, 5)
        assert len(calls) == 1

    def test_page_cursor_and_service_stats(self, monkeypatch):
        traces = [
            {
                "traceID": t,
                "rootTraceName": "agent",
                "startTimeUnixNano": str(s),
                "serviceStats": {"hermes-agent": {"spanCount": 7}},
                "spanSets": [{"spans": [{"name": "agent"}]}],
            }
            for t, s in (("a", 30 * NS), ("b", 20 * NS), ("c", 10 * NS))
        ]
        seen = {}

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {"traces": traces, "metrics": {"inspectedTraces": 3}}

        monkeypatch.setattr(tempo, "http_get_json", fake)
        page = self._adapter().search(StructuredFilter(before_ns=25 * NS), 0, 100, 1)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert (q["limit"], q["end"]) == (str(tempo._DEFAULT_SEARCH_FETCH), "26")
        # ids come back padded to 32 hex digits (Tempo drops leading zeros)
        assert [t["traceID"] for t in page["traces"]] == ["b".zfill(32)]
        assert page["has_more"] is True and page["next_before_ns"] == 20 * NS
        assert page["traces"][0]["spanCount"] == 7 and page["metrics"] == {"inspectedTraces": 3}
        assert "raw" not in page

    def test_free_text_keeps_the_root_in_its_own_span_set(self, monkeypatch):
        seen = {}

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {"traces": []}

        monkeypatch.setattr(tempo, "http_get_json", fake)
        self._adapter().search(StructuredFilter(free_text="B1-x", status="ok"), 0, 10, 5)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        # the root set carries the structured filters, the text is a second set
        assert q["q"].startswith(
            '{ nestedSetParent < 0 && status = ok } && { (span.input.value =~ ".*B1-x.*" || span.output.value =~ ".*B1-x.*") }'
        )
        assert q["spss"] == "1"
        # widened to every span: one set, no spss cap
        self._adapter().search(StructuredFilter(free_text="B1-x", roots_only=False), 0, 10, 5)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert q["q"].startswith("{ (span.input.value =~") and q["spss"] == "1"

    def test_widened_search_names_the_card_after_the_matched_span(self, monkeypatch):
        trace = {
            "traceID": "ab",
            "rootTraceName": "agent",
            "startTimeUnixNano": "1000000000",
            "spanSets": [
                {
                    "spans": [
                        {"attributes": [{"key": "name", "value": {"stringValue": "tool.terminal"}}]}
                    ]
                }
            ],
        }
        monkeypatch.setattr(tempo, "http_get_json", lambda *a, **k: {"traces": [trace]})
        page = self._adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False), 0, 10, 5
        )
        assert page["traces"][0]["rootTraceName"] == "tool.terminal"
        # Tempo 3 puts the selected ``name`` on the span itself
        trace["spanSets"] = [{"spans": [{"name": "tool.read_file", "attributes": []}]}]
        trace["rootTraceName"] = "agent"
        page = self._adapter().search(
            StructuredFilter(name_prefix="tool.", roots_only=False), 0, 10, 5
        )
        assert page["traces"][0]["rootTraceName"] == "tool.read_file"
        # roots-only keeps the root's name
        trace["rootTraceName"] = "agent"
        page = self._adapter().search(StructuredFilter(name_prefix="agent"), 0, 10, 5)
        assert page["traces"][0]["rootTraceName"] == "agent"
        # widened by a tool filter alone the row stays the root, the span set
        # the matched span (#558)
        f = StructuredFilter(attr_equals={"tool.name": "read_file"}, roots_only=False)
        row = self._adapter().search(f, 0, 10, 5)["traces"][0]
        assert row["rootTraceName"] == "agent"
        assert row["spanSets"][0]["spans"][0]["name"] == "tool.read_file"

    @staticmethod
    def _tool_row(tid: str, root_s: int, tool_s: float, tool_ms: int, **span) -> dict:
        """A Tempo row of a 4 s ``agent`` turn whose span set is its
        ``tool.terminal`` span, ``tool_s`` seconds after epoch for ``tool_ms``."""
        sp = {
            "spanID": "aaaa000000000001",
            "name": "tool.terminal",
            "startTimeUnixNano": str(int(tool_s * NS)),
            "durationNanos": str(tool_ms * 1_000_000),
            "attributes": [{"key": "tool.name", "value": {"stringValue": "terminal"}}],
        }
        sp.update(span)
        return {
            "traceID": tid,
            "rootServiceName": "hermes-agent",
            "rootTraceName": "agent",
            "startTimeUnixNano": str(root_s * NS),
            "durationMs": 4029,
            "spanSets": [{"spans": [{k: v for k, v in sp.items() if v is not None}]}],
        }

    def test_kind_card_is_timed_as_the_matched_span(self, monkeypatch):
        # #598: a kind cards the matched span, its start and duration too, not
        # the root's 4 s under the tool's name.
        rows = [self._tool_row("ab", 1000, 1001.5, 1120)]
        monkeypatch.setattr(
            tempo, "http_get_json", lambda *a, **k: json.loads(json.dumps({"traces": rows}))
        )
        kind = StructuredFilter(name_prefix="tool.", roots_only=False)
        row = self._adapter().search(kind, 0, 2000, 5)["traces"][0]
        assert row["rootTraceName"] == "tool.terminal"
        assert row["startTimeUnixNano"] == str(int(1001.5 * NS))
        assert row["durationMs"] == 1120
        # A tool filter without a kind keeps the root's name and timing (#558).
        tool = StructuredFilter(attr_equals={"tool.name": "terminal"}, roots_only=False)
        row = self._adapter().search(tool, 0, 2000, 5)["traces"][0]
        assert (row["rootTraceName"], row["startTimeUnixNano"], row["durationMs"]) == (
            "agent",
            str(1000 * NS),
            4029,
        )

    def test_kind_card_keeps_the_traces_timing_when_the_span_has_none(self, monkeypatch):
        # A build that leaves the span set's timing out: only the name moves.
        rows = [self._tool_row("ab", 1000, 0, 0, startTimeUnixNano=None, durationNanos=None)]
        monkeypatch.setattr(
            tempo, "http_get_json", lambda *a, **k: json.loads(json.dumps({"traces": rows}))
        )
        kind = StructuredFilter(name_prefix="tool.", roots_only=False)
        row = self._adapter().search(kind, 0, 2000, 5)["traces"][0]
        assert row["rootTraceName"] == "tool.terminal"
        assert (row["startTimeUnixNano"], row["durationMs"]) == (str(1000 * NS), 4029)
        # A zero-length tool is timed as zero, not as the turn.
        rows[:] = [self._tool_row("ab", 1000, 1001.5, 0)]
        row = self._adapter().search(kind, 0, 2000, 5)["traces"][0]
        assert row["durationMs"] == 0

    def test_kind_pages_follow_the_matched_spans_start(self, monkeypatch):
        # Trace turn-a began first but its tool ran last: the newest card is turn-a,
        # the cursor its tool's start, and the next page holds turn-b only.
        rows = [
            self._tool_row("turn-a", 1000, 1003.0, 200),
            self._tool_row("turn-b", 1001, 1001.5, 300),
        ]
        seen = []

        def fake(url, headers=None, timeout=None):
            seen.append(dict(_urlparse.parse_qsl(_urlparse.urlparse(url).query)))
            return json.loads(json.dumps({"traces": rows}))

        monkeypatch.setattr(tempo, "http_get_json", fake)
        kind = StructuredFilter(name_prefix="tool.", roots_only=False)
        first = self._adapter().search(kind, 0, 2000, 1)
        assert [t["traceID"] for t in first["traces"]] == ["turn-a"]
        assert first["has_more"] is True and first["next_before_ns"] == int(1003.0 * NS)
        f2 = StructuredFilter(
            name_prefix="tool.", roots_only=False, before_ns=first["next_before_ns"]
        )
        second = self._adapter().search(f2, 0, 2000, 1)
        assert [t["traceID"] for t in second["traces"]] == ["turn-b"]
        assert second["has_more"] is False
        # The second query's window still reaches back over turn-a's root.
        assert int(seen[1]["end"]) >= 1003

    def test_search_over_fetches_and_cuts_the_newest_rows(self, monkeypatch):
        # Tempo stops at ``limit`` matches in block order, not the newest
        # (Tempo 3.0.3, live): ask for ``search_fetch`` rows and cut here.
        seen = {}
        rows = [
            {"traceID": t, "rootTraceName": "agent", "startTimeUnixNano": str(s)}
            for t, s in (("old", 10 * NS), ("new", 30 * NS), ("mid", 20 * NS))
        ]

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {"traces": rows}

        monkeypatch.setattr(tempo, "http_get_json", fake)
        page = self._adapter().search(StructuredFilter(), 0, 100, 2)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert q["limit"] == str(tempo._DEFAULT_SEARCH_FETCH)
        assert [t["traceID"] for t in page["traces"]] == [
            "new",
            "mid",
        ]  # non-hex ids stay as they are
        assert page["has_more"] is True and page["next_before_ns"] == 20 * NS
        # configurable per entry, never below limit + 1
        a = tempo.TempoAdapter(
            {"type": "tempo", "endpoint": "http://localhost:4318/v1/traces", "search_fetch": 2}
        )
        a.search(StructuredFilter(), 0, 100, 5)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert q["limit"] == "6"

    def test_raw_query_bounds_duration_client_side_and_sends_no_min_duration(self, monkeypatch):
        # Tempo ignores ``minDuration`` next to a TraceQL ``q`` (verified live on
        # grafana/otel-lgtm 0.34): the bound is applied to the rows instead.
        seen = {}
        rows = [
            {
                "traceID": "a",
                "rootTraceName": "agent",
                "startTimeUnixNano": str(30 * NS),
                "durationMs": 12110,
            },
            {
                "traceID": "b",
                "rootTraceName": "agent",
                "startTimeUnixNano": str(20 * NS),
                "durationMs": 1938,
            },
            {"traceID": "c", "rootTraceName": "agent", "startTimeUnixNano": str(10 * NS)},
        ]

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {"traces": rows}

        monkeypatch.setattr(tempo, "http_get_json", fake)
        page = self._adapter().search(StructuredFilter(raw="{}", min_duration_ms=5000), 0, 100, 50)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert q["q"].startswith("{}") and "minDuration" not in q
        assert [t["traceID"] for t in page["traces"]] == ["a".zfill(32)]
        # without a raw query the bound stays a predicate and nothing is dropped here
        page = self._adapter().search(StructuredFilter(min_duration_ms=5000), 0, 100, 50)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert "duration >= 5000ms" in q["q"] and len(page["traces"]) == 3

    def test_raw_query_reports_the_structured_fields_as_ignored(self):

        a = self._adapter()
        f = StructuredFilter(
            raw="{ span.foo = 1 }",
            min_duration_ms=10,
            status="error",
            name_prefix="tool.",
            attr_equals={"tool.name": "terminal"},
            free_text="x",
        )
        applied, ignored = split_applied_filters(a, f)
        assert applied == ["min_duration", "raw"]
        assert ignored == ["name", "tool", "status_error", "free_text", "roots_only"]
        # the declaration itself is unchanged for ordinary searches
        applied, ignored = split_applied_filters(
            a, StructuredFilter(status="error", name_prefix="tool.")
        )
        assert applied == ["name", "status_error", "roots_only"] and ignored == []

    def test_trace_url_is_grafana_explore_only_with_ui_url(self):
        assert self._adapter().trace_url("abc") is None
        a = tempo.TempoAdapter(
            {
                "type": "lgtm",
                "endpoint": "http://localhost:4318/v1/traces",
                "ui_url": "http://localhost:3001/explore/",
            }
        )
        url = a.trace_url("0ae1")
        assert url.startswith("http://localhost:3001/explore?schemaVersion=1&panes=")
        panes = json.loads(dict(_urlparse.parse_qsl(_urlparse.urlparse(url).query))["panes"])
        assert panes["a"]["queries"][0] == {
            "refId": "A",
            "datasource": {"type": "tempo", "uid": "tempo"},
            "queryType": "traceql",
            "query": "0ae1",
        }

    def test_prometheus_last_on_a_counter_is_an_honest_sum(self):
        # ``last`` of a counter's increases is folded and reported as ``sum``,
        # the ask kept in ``agg_requested``; a gauge keeps ``last`` and carries
        # no ``cumulative`` / ``series_start_rule`` key (#515).
        samples = [(NS, 2.0, "s"), (2 * NS, 5.0, "s")]

        def fold(cumulative):
            return fold_samples(
                samples, {"s": "x"}, 0, 3 * NS, 60, "last", name="n", cumulative=cumulative
            )

        counter, gauge = fold(True), fold(False)
        assert (counter["agg"], counter["agg_requested"], counter["cumulative"]) == (
            "sum",
            "last",
            True,
        )
        assert "300s before the window" in counter["series_start_rule"]
        assert (gauge["agg"], gauge["agg_requested"], gauge["series"]) == (
            "last",
            "last",
            {"x": [5.0]},
        )
        assert "cumulative" not in gauge and "series_start_rule" not in gauge
        with pytest.raises(BackendError) as exc:
            _prometheus.promql_for("up) or vector(1", 60, None, "sum")
        assert exc.value.kind == "request"

    def test_loki_windows_are_clamped_to_thirty_days(self, monkeypatch):
        seen = {}

        def fake(url, headers=None, timeout=None):
            seen["url"] = url
            return {"data": {"result": []}}

        monkeypatch.setattr(_loki, "http_get_json", fake)
        year = 365 * 86400
        _loki.loggers("http://l:3100", 0, year)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert f"[{30 * 86400}s]" in q["query"]
        _loki.logs_search("http://l:3100", LogFilter(), 0, year, 10)
        q = dict(_urlparse.parse_qsl(_urlparse.urlparse(seen["url"]).query))
        assert int(q["end"]) - int(q["start"]) == 30 * 86400 * NS
