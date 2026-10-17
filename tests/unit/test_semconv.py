"""``hermes_otel.semconv`` is the one source of the telemetry vocabulary (#404):
the registry is consistent, the readers derive from it, the hooks' literals
are registered, and the generated TypeScript and reference-page tables are
current (#523)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from hermes_otel import live_store, live_summary, log_events, semconv
from hermes_otel.dashboard.backends import _attrs

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hermes_otel" / "hooks"
_NAME = (
    r"(?:gen_ai|llm|hermes|openinference|traceloop|session|user|error|input|output|tool|"
    r"correlation|http|service|process|wandb|weave|exception|host)\.[a-z_][a-z0-9_.]*"
)
QUOTED = re.compile(r'"(' + _NAME + r')"')


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location(
        "gen_semconv", REPO_ROOT / "scripts" / "gen_semconv.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRegistry:
    def test_names_are_unique_and_grouped(self):
        names = [a.name for a in semconv.ATTRIBUTES]
        assert len(names) == len(set(names))
        assert all(a.group and a.doc for a in semconv.ATTRIBUTES), [
            a.name for a in semconv.ATTRIBUTES if not (a.group and a.doc)
        ]
        assert semconv.attribute("tool.name").group == "tool"
        assert semconv.attribute("no.such") is None
        assert set(semconv.groups()) >= {"session", "usage", "tool", "kanban"}

    def test_rows_are_placed_and_the_first_row_is_home(self, gen):
        """Every row lands in a page table (typed) or is off-page; an attribute
        listed in several rows takes its meaning from the first."""
        assert gen.check_rows() == []
        home = semconv.attribute("gen_ai.operation.name")
        assert (home.where, home.doc) == ("agent / cron: start", "`invoke_agent`")
        assert semconv.attribute("hermes.span_kind").where == "skill.*"

    def test_key_chains_are_registered(self):
        for chain in (
            semconv.SESSION_KEYS,
            semconv.MODEL_KEYS,
            semconv.PROVIDER_KEYS,
            semconv.INPUT_TOKEN_KEYS,
            semconv.OUTPUT_TOKEN_KEYS,
            semconv.TOTAL_TOKEN_KEYS,
            semconv.REASONING_TOKEN_KEYS,
            semconv.CACHE_READ_TOKEN_KEYS,
            semconv.FINISH_REASON_KEYS,
            semconv.INPUT_CONTENT_KEYS,
            semconv.OUTPUT_CONTENT_KEYS,
            semconv.COST_KEYS,
            semconv.TURN_TOTAL_KEYS,
        ):
            assert semconv.unknown_names(chain) == [], chain
        assert semconv.first({"a": "", "b": None, "c": 0}, ("a", "b", "c")) == 0
        assert semconv.first({}, ("a",)) is None

    def test_event_names_match_log_events(self):
        assert log_events.TURN_START == semconv.EVENT_TURN_START
        assert log_events.INFERENCE_DETAILS == semconv.EVENT_INFERENCE_DETAILS
        assert set(log_events.EVENTS) == set(semconv.EVENT_NAMES)


class TestSpanKinds:
    @pytest.mark.parametrize(
        "name, attrs, kind",
        [
            ("agent", {}, "agent"),
            ("agent.x", {}, "agent"),
            ("agentic", {}, "other"),
            ("cron", {}, "cron"),
            ("session:1", {}, "session"),
            ("skill.foo", {}, "skill"),
            ("tool.skill_view", {"hermes.span_kind": "skill"}, "skill"),
            ("tool.terminal", {"hermes.span_kind": "approval"}, "approval"),
            ("tool.terminal", {"hermes.span_kind": "bogus"}, "tool"),
            ("approval.rm", {}, "approval"),
            ("subagent.r", {}, "subagent"),
            ("llm.m", {}, "llm"),
            ("api.m", {}, "api"),
            ("kanban.claim", {}, "other"),
            ("", {}, "other"),
        ],
    )
    def test_span_kind(self, name, attrs, kind):
        assert semconv.span_kind(name, attrs) == kind
        # the live store's classifier is the registry's
        assert live_summary.span_kind(name, attrs) == kind

    def test_live_store_tables_derive_from_the_registry(self):
        assert live_store._KIND_PREFIXES == semconv.KIND_PREFIXES
        assert live_summary._SESSION_KEYS == semconv.SESSION_KEYS
        assert live_store._MODEL_KEYS == live_summary._MODEL_KEYS == semconv.MODEL_KEYS
        assert live_summary._TOKEN_KEYS == semconv.TOTAL_TOKEN_KEYS
        assert set(_attrs.KNOWN_ATTRIBUTES) == set(semconv.ATTRIBUTE_NAMES)

    def test_every_kind_has_a_prefix_table(self):
        assert set(semconv.KIND_PREFIXES) == set(semconv.KINDS) - {"other"}
        assert semconv.KIND_PREFIXES["agent"] == ("agent", "agent.")


class TestDrift:
    def test_every_literal_the_hooks_set_is_registered(self):
        """A new attribute goes into the registry (and the reference page), not
        just into a hook."""
        names = set()
        for path in sorted(HOOKS.glob("*.py")) + [
            REPO_ROOT / "hermes_otel" / "tracer.py",
            REPO_ROOT / "hermes_otel" / "session_state.py",
        ]:
            names |= set(QUOTED.findall(path.read_text(encoding="utf-8")))
        # Instrument names are metrics, documented in metrics.md.
        from hermes_otel.tracer import _INSTRUMENTS, HOST_METRIC_NAMES

        names -= {spec.name for spec in _INSTRUMENTS.values()} - {
            "hermes.retry.count",
            "hermes.cost.usage",
        }
        names -= set(HOST_METRIC_NAMES) | {"gen_ai.token.type"}
        assert semconv.unknown_names(names) == []

    def test_reference_page_tables_are_current(self, gen):
        text = gen.DOC.read_text(encoding="utf-8")
        assert text == gen.render_docs(text), "run: python scripts/gen_semconv.py"

    def test_reference_page_ticks_every_span_attribute(self, gen):
        """Names off the page are only the event / log-record / backend-derived ones."""
        text = gen.DOC.read_text(encoding="utf-8")
        off_page = {a.name for a in semconv.ATTRIBUTES if a.where in gen.OFF_PAGE}
        missing = [n for n in semconv.ATTRIBUTE_NAMES if n not in off_page and f"`{n}`" not in text]
        assert missing == []

    def test_generated_typescript_is_current(self, gen):
        current = gen.TS_OUT.read_text(encoding="utf-8")
        assert current == gen.render_ts(), "run: python scripts/gen_semconv.py"
