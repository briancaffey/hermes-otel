"""``hermes_otel.semconv`` is the one source of the telemetry vocabulary (#404):
the registry is consistent, the readers derive from it, the hooks' literals
are registered, the reference page lists exactly the registry, and the
generated TypeScript is current."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from hermes_otel import live_store, log_events, query_cli, semconv
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
        assert live_store.span_kind(name, attrs) == kind

    def test_live_store_tables_derive_from_the_registry(self):
        assert live_store._KIND_PREFIXES == semconv.KIND_PREFIXES
        assert live_store._SESSION_KEYS == semconv.SESSION_KEYS
        assert live_store._MODEL_KEYS == semconv.MODEL_KEYS
        assert live_store._TOKEN_KEYS == semconv.TOTAL_TOKEN_KEYS
        assert query_cli._MODEL_KEYS == semconv.MODEL_KEYS
        assert query_cli._COST_KEY == semconv.COST_KEY
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

    def test_reference_page_lists_exactly_the_registry(self, gen):
        assert gen.check_docs() == []

    def test_generated_typescript_is_current(self, gen):
        current = gen.TS_OUT.read_text(encoding="utf-8")
        assert current == gen.render_ts(), "run: python scripts/gen_semconv.py"
