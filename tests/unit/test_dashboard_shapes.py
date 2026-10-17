"""The typed span row and the two algorithms built on it (#513): the card an
adapter's search picks for a trace and the detail its ``get_trace`` answers.
The adapters' own tests and goldens cover the decoders; this pins the shared
rules each knob of ``card_from_trace`` stands for."""

from __future__ import annotations

from typing import Any, Dict, Optional

from hermes_otel.dashboard.backends.base import StructuredFilter, otlp_status
from hermes_otel.dashboard.backends.shapes import (
    SpanRow,
    attach_root,
    card_from_trace,
    coerce_tag,
    dedupe_spans,
    otlp_kind,
    otlp_span,
    trace_detail,
)

NS = 1_000_000_000


def _row(
    span_id: str,
    name: str,
    start_ns: int,
    dur_ns: int,
    parent: Optional[str] = None,
    *,
    error: bool = False,
    service: str = "svc",
    **attrs: Any,
) -> SpanRow:
    return SpanRow(
        "t1",
        span_id,
        parent,
        name,
        start_ns=start_ns,
        end_ns=start_ns + dur_ns,
        attrs=attrs,
        service=service,
        status=otlp_status("error" if error else "ok"),
    )


ROOT = _row("r", "agent", 10 * NS, 5 * NS, **{"hermes.session_id": "s1", "input.value": "hi"})
TOOL = _row("c1", "tool.terminal", 11 * NS, NS, "r", **{"tool.name": "terminal"})
READ = _row("c2", "tool.read_file", 12 * NS, NS, "r", error=True, **{"tool.name": "read_file"})
TRACE = [TOOL, READ, ROOT]  # not in start order: the root is found, not assumed


def _card_name(card: Optional[Dict[str, Any]]) -> Optional[str]:
    return card["rootTraceName"] if card else None


class TestOtlpKind:
    def test_every_spelling_lands_on_the_otlp_number(self):
        for value in (2, "2", "SERVER", "Server", "server", "SPAN_KIND_SERVER"):
            assert otlp_kind(value) == 2, value
        assert otlp_kind("consumer") == 5 and otlp_kind("SPAN_KIND_PRODUCER") == 4

    def test_unknown_or_missing_is_internal(self):
        for value in (None, "", 0, "0", "LLM", True, 2.5):
            assert otlp_kind(value) == 1, value


class TestSpanRow:
    def test_error_reads_the_status_and_the_card_carries_it(self):
        assert READ.error and not ROOT.error
        card = READ.card(3)
        assert card["spanCount"] == 3 and card["durationMs"] == 1000
        attrs = {a["key"]: a["value"] for a in card["spanSets"][0]["spans"][0]["attributes"]}
        assert attrs["status"] == {"stringValue": "error"}
        assert attrs["tool.name"] == {"stringValue": "read_file"}

    def test_duration_never_negative(self):
        assert SpanRow("t", "s", None, "x", start_ns=5, end_ns=3, attrs={}).duration_ns == 0


class TestCardFromTrace:
    def test_roots_only_cards_the_root_and_recheck_reads_the_root_alone(self):
        assert _card_name(card_from_trace(StructuredFilter(), TRACE)) == "agent"
        # The backend matched any span (tool.name is on a child): the root fails.
        f = StructuredFilter(attr_equals={"tool.name": "terminal"})
        assert card_from_trace(f, TRACE) is None
        assert _card_name(card_from_trace(StructuredFilter(name_prefix="agent"), TRACE)) == "agent"
        assert card_from_trace(StructuredFilter(name_prefix="tool."), TRACE) is None

    def test_equals_replaces_the_filter_attributes(self):
        f = StructuredFilter(attr_equals={"tool.name": "terminal"})
        card = card_from_trace(f, TRACE, equals={"hermes.session_id": "S1"})  # case-insensitive
        assert _card_name(card) == "agent"

    def test_orphans_are_no_root_unless_the_server_answered_roots_only(self):
        orphan = [TOOL, READ]  # the real root is outside the window
        assert card_from_trace(StructuredFilter(), orphan) is None
        card = card_from_trace(StructuredFilter(status="error"), orphan, server_roots=True)
        # Nothing is re-checked: the root is the card as the detail shows it.
        assert _card_name(card) == "tool.terminal" and card["spanCount"] == 2

    def test_widened_cards_the_first_prefixed_span_by_start_else_the_root(self):
        widened = StructuredFilter(name_prefix="tool.", roots_only=False)
        card = card_from_trace(widened, TRACE)
        assert _card_name(card) == "tool.terminal" and card["durationMs"] == 1000
        assert _card_name(card_from_trace(StructuredFilter(roots_only=False), TRACE)) == "agent"
        assert (
            card_from_trace(StructuredFilter(name_prefix="api.", roots_only=False), TRACE) is None
        )

    def test_check_all_applies_status_and_free_text_the_backend_could_not(self):
        # Without it (Jaeger, Zipkin) status and text are the server's business.
        assert _card_name(card_from_trace(StructuredFilter(status="error"), TRACE)) == "agent"
        assert card_from_trace(StructuredFilter(status="error"), TRACE, check_all=True) is None
        assert _card_name(card_from_trace(StructuredFilter(free_text="HI"), TRACE, check_all=True))
        assert card_from_trace(StructuredFilter(free_text="nope"), TRACE, check_all=True) is None
        # Widened, the card is the first span by start satisfying every predicate.
        f = StructuredFilter(status="error", roots_only=False)
        assert _card_name(card_from_trace(f, TRACE, check_all=True)) == "tool.read_file"
        f = StructuredFilter(attr_equals={"tool.name": "terminal"}, roots_only=False)
        assert _card_name(card_from_trace(f, TRACE, check_all=True)) == "tool.terminal"

    def test_no_rows_no_card(self):
        assert card_from_trace(StructuredFilter(), []) is None


class TestTraceDetail:
    def test_one_batch_per_service_in_first_seen_order(self):
        other = _row("x", "api.gpt", 13 * NS, NS, "r", service="gateway")
        d = trace_detail([ROOT, other, TOOL], truncated=True)
        assert d["span_count"] == 3 and d["truncated"] is True
        resources = [b["resource"]["attributes"] for b in d["batches"]]
        assert resources == [
            [{"key": "service.name", "value": {"stringValue": "svc"}}],
            [{"key": "service.name", "value": {"stringValue": "gateway"}}],
        ]
        names = [s["name"] for s in d["batches"][0]["scopeSpans"][0]["spans"]]
        assert names == ["agent", "tool.terminal"]
        span = d["batches"][0]["scopeSpans"][0]["spans"][0]
        assert span["parentSpanId"] is None and span["kind"] == 1 and "events" not in span
        assert span["status"] == {"code": 1, "message": ""}

    def test_unknown_service_is_an_empty_resource(self):
        d = trace_detail([_row("s", "agent", NS, NS, service="")])
        assert d["batches"][0]["resource"] == {"attributes": []}

    def test_a_row_resource_groups_the_batch_instead(self):
        a = SpanRow(
            "t", "a", None, "x", 1, 2, {}, "svc", resource={"service.name": "svc", "pid": 1}
        )
        b = SpanRow("t", "b", "a", "y", 2, 3, {}, "svc", resource={"service.name": "svc", "pid": 2})
        c = SpanRow("t", "c", "a", "z", 3, 4, {}, "svc", resource={"pid": 1, "service.name": "svc"})
        d = trace_detail([a, b, c])
        assert [len(bt["scopeSpans"][0]["spans"]) for bt in d["batches"]] == [2, 1]
        assert d["batches"][0]["resource"]["attributes"][1] == {
            "key": "pid",
            "value": {"intValue": "1"},
        }


class TestAttachRoot:
    """One root per detail tree (#516): the real one is kept and gains the
    trace record's facts, several or none get a marked synthetic root."""

    @staticmethod
    def _span(span_id, parent=None, **attrs):
        return otlp_span(
            "t1", span_id, parent, span_id, start_ns=NS, end_ns=2 * NS, attrs=attrs, status={}
        )

    def test_a_single_root_is_kept_and_gains_only_the_facts_it_lacks(self):
        spans = [self._span("r", "gone", name="agent"), self._span("c", "r")]
        calls = []

        def facts():
            calls.append(1)
            return {"name": "trace", "langfuse.sessionId": "s1"}

        attach_root(
            spans,
            "t1",
            extra_root_attrs=facts,
            synthetic_name="x",
            start_ns=0,
            end_ns=0,
            reason="r",
        )
        assert [sp["spanId"] for sp in spans] == ["r", "c"] and calls == [1]
        root = spans[0]
        assert root["parentSpanId"] is None
        assert root["attributes"] == [
            {"key": "name", "value": {"stringValue": "agent"}},
            {"key": "langfuse.sessionId", "value": {"stringValue": "s1"}},
        ]

    def test_several_roots_hang_off_a_marked_synthetic_root_without_consulting_the_facts(self):
        spans = [self._span("a"), self._span("b", "missing"), self._span("c", "a")]

        def never():
            raise AssertionError("the facts lookup is only paid for a real root")

        attach_root(
            spans,
            "0123456789abcdef0123456789abcdef",
            extra_root_attrs=never,
            synthetic_name="trace",
            start_ns=NS,
            end_ns=3 * NS,
            reason="no single root",
            synthetic_attrs={"backend.trace_id": "t"},
        )
        root = spans[0]
        assert root["spanId"] == "synthetic-0123456789abcdef" and root["name"] == "trace"
        assert (root["startTimeUnixNano"], root["endTimeUnixNano"]) == (str(NS), str(3 * NS))
        assert root["attributes"] == [
            {"key": "backend.trace_id", "value": {"stringValue": "t"}},
            {"key": "synthetic", "value": {"boolValue": True}},
            {"key": "synthetic.reason", "value": {"stringValue": "no single root"}},
        ]
        assert root["status"] == {"code": 1, "message": ""}
        assert [sp["parentSpanId"] for sp in spans[1:]] == [root["spanId"], root["spanId"], "a"]

    def test_no_spans_at_all_still_get_a_root_carrying_the_dict_facts(self):
        spans: list = []
        attach_root(
            spans,
            "t1",
            extra_root_attrs={"langfuse.trace_id": "t1"},
            synthetic_name="trace",
            start_ns=0,
            end_ns=0,
            reason="empty",
        )
        assert len(spans) == 1 and spans[0]["startTimeUnixNano"] == "0"
        assert [a["key"] for a in spans[0]["attributes"]] == [
            "langfuse.trace_id",
            "synthetic",
            "synthetic.reason",
        ]


class TestTagDecoders:
    """Shared by the adapters whose backend stringifies every tag (Zipkin,
    VictoriaTraces, ClickStack, Gigapipe) or stores a span twice (Tempo,
    Gigapipe); none imports another adapter for them (#519)."""

    def test_coerce_numbers_bools_and_ids(self):
        assert coerce_tag("gen_ai.usage.input_tokens", "25744") == 25744
        assert coerce_tag("llm.response.duration_ms", "1442.3") == 1442.3
        assert coerce_tag("hermes.session.completed", "true") is True
        assert coerce_tag("hermes.session.failed", "false") is False
        assert coerce_tag("hermes.session_id", "20261011") == "20261011"
        assert coerce_tag("service.instance.id", "1234") == "1234"
        assert coerce_tag("llm.model_name", "nvidia/x") == "nvidia/x"
        assert coerce_tag("n", 7) == 7

    def test_dedupe_spans_keeps_the_first_copy(self):
        data = {
            "batches": [
                {"scopeSpans": [{"spans": [{"spanId": "a", "n": 1}, {"spanId": "b"}]}]},
                {"scopeSpans": [{"spans": [{"spanId": "a", "n": 2}, {"name": "no id"}]}]},
            ]
        }
        out = dedupe_spans(data)
        assert [
            s.get("spanId") for b in out["batches"] for sc in b["scopeSpans"] for s in sc["spans"]
        ] == [
            "a",
            "b",
            None,
        ]
        assert out["batches"][0]["scopeSpans"][0]["spans"][0]["n"] == 1
        assert dedupe_spans("not a trace") == "not a trace"
