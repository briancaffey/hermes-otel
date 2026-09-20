"""The hooks package (#104): re-exports, the consolidated builders, and the
guards that keep hand-rolled copies from creeping back."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

import hermes_otel.hooks as hooks
from hermes_otel.hooks import _common, attributes
from hermes_otel.plugin_config import HermesOtelConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG = REPO_ROOT / "hermes_otel" / "hooks"


class TestReExports:
    def test_every_declared_hook_has_a_callback_on_the_package(self):
        manifest = yaml.safe_load((REPO_ROOT / "hermes_otel" / "plugin.yaml").read_text())
        for name in manifest["provides_hooks"]:
            attr = name if name.startswith("on_") else f"on_{name}"
            assert callable(getattr(hooks, attr)), attr
            assert attr in hooks.__all__

    def test_public_helpers_and_test_seams_still_resolve(self):
        for name in (
            "get_current_traceparent",
            "on_mcp_request_headers",
            "get_tracer",
            "_FAIL_OPEN_WARNED",
            "_correlation_attributes",
            "_resolve_tool_outcome",
            "_start_session_span",
        ):
            assert hasattr(hooks, name), name

    def test_fail_open_registry_is_one_object(self):
        assert hooks._FAIL_OPEN_WARNED is _common._FAIL_OPEN_WARNED


class TestNoDriftGuards:
    """Consolidation only holds if nobody re-inlines the old copies."""

    def test_no_inline_preview_limit_expressions(self):
        pattern = re.compile(r"_preview_max_chars\s+or\s+")
        hits = [p.name for p in PKG.glob("*.py") if pattern.search(p.read_text())]
        assert hits == [], f"use _preview_for(tracer, kind, value) instead: {hits}"

    def test_session_identity_literals_live_in_attributes_only(self):
        hits = [
            p.name
            for p in PKG.glob("*.py")
            if p.name != "attributes.py" and '"session.id"' in p.read_text()
        ]
        assert hits == [], f"use _session_identity_attributes(): {hits}"

    def test_model_identity_literals_live_in_attributes_only(self):
        hits = [
            p.name
            for p in PKG.glob("*.py")
            if p.name != "attributes.py" and '"llm.model_name"' in p.read_text()
        ]
        assert hits == [], f"use _model_attributes(): {hits}"


class TestPreviewFor:
    def _tracer(self, **cfg):
        t = MagicMock()
        t.config = HermesOtelConfig(**cfg)
        return t

    def test_kind_override_then_global_fallback(self):
        t = self._tracer(preview_max_chars=10, tool_output_preview_max_chars=5)
        assert _common._preview_for(t, "tool_output", "x" * 20) == "xx..."
        assert _common._preview_for(t, "tool_input", "x" * 20) == "xxxxxxx..."
        assert _common._preview_for(t, None, "x" * 20) == "xxxxxxx..."

    def test_capture_previews_off_is_none_for_every_kind(self):
        t = self._tracer(capture_previews=False)
        for kind in (None, "tool_input", "tool_output", "llm_input", "llm_output"):
            assert _common._preview_for(t, kind, "secret") is None

    def test_unknown_kind_is_a_bug_not_a_silent_fallback(self):
        with pytest.raises(KeyError):
            _common._preview_for(self._tracer(), "nope", "x")


class TestResolveSessionId:
    def test_explicit_wins_then_kwargs_then_turn_id(self):
        assert _common._resolve_session_id({"session_id": "k"}, session_id="e") == "e"
        assert _common._resolve_session_id({"session_id": "k"}, turn_id="t:x:1") == "k"
        assert _common._resolve_session_id({}, turn_id="sess9:task:abcd") == "sess9"

    def test_turn_id_only_when_offered(self):
        # Tool / api hooks never fall back to turn_id (#68): no dispatch path
        # sends a real turn_id without a session_id.
        assert _common._resolve_session_id({"turn_id": "sess9:task:abcd"}) == ""
        assert _common._resolve_session_id({}, turn_id="session:task:abcd") == ""
        assert _common._resolve_session_id({}) == ""


class TestAttributeBuilders:
    def test_session_identity_root_carries_legacy_key(self):
        assert attributes._session_identity_attributes("s") == {
            "session.id": "s",
            "session_id": "s",
        }
        assert attributes._session_identity_attributes("s", root=True)["hermes.session_id"] == "s"
        assert attributes._session_identity_attributes("") == {}
        long = "x" * 300
        assert len(attributes._session_identity_attributes(long)["session.id"]) == 203

    def test_model_attributes_both_conventions_and_truncation(self):
        attrs = attributes._model_attributes("m" * 250, "p" * 150)
        assert attrs["llm.model_name"] == attrs["gen_ai.request.model"] == "m" * 200 + "..."
        assert attrs["llm.provider"] == attrs["gen_ai.provider.name"] == "p" * 120 + "..."
        assert attrs["gen_ai.system"] == attrs["gen_ai.provider.name"]
        assert attributes._model_attributes(None, None) == {}
        # The GenAI pair may name the real provider while llm.provider keeps the platform.
        attrs = attributes._model_attributes("m", "telegram", "openrouter")
        assert attrs["llm.provider"] == "telegram" and attrs["gen_ai.provider.name"] == "openrouter"

    def test_metric_labels_omit_unknowns(self):
        assert attributes._metric_model_labels("m", None) == {"model": "m"}
        assert attributes._metric_model_labels("", "") == {}


class TestToolSpanIdentity:
    def test_tool_span_carries_session_id_like_api_spans(self, inmemory_otel_setup):
        # The one salvaged piece of #68: backend session filters group tool spans.
        exporter, _ = inmemory_otel_setup
        hooks.on_session_start(session_id="s1", model="m", platform="cli")
        hooks.on_pre_tool_call(tool_name="bash", args={}, task_id="t1", session_id="s1")
        hooks.on_post_tool_call(
            tool_name="bash", args={}, result="ok", task_id="t1", session_id="s1"
        )
        hooks.on_session_end(
            session_id="s1", completed=True, interrupted=False, model="m", platform="cli"
        )
        tool = next(s for s in exporter.get_finished_spans() if s.name == "tool.bash")
        assert tool.attributes["session.id"] == "s1"
        assert tool.attributes["gen_ai.conversation.id"] == "s1"


class TestMetricLabelChanges:
    def test_retryable_label_keeps_unknown(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        plugin.record_metric = MagicMock()
        hooks.on_api_request_error(
            task_id="t", session_id="s", model="m", provider="p", retryable=None
        )
        labels = next(
            c.args[2] for c in plugin.record_metric.call_args_list if c.args[0] == "api_error_count"
        )
        assert labels["retryable"] == "unknown"
        hooks.on_api_request_error(
            task_id="t2", session_id="s", model="m", provider="p", retryable="yes"
        )
        labels = [
            c.args[2] for c in plugin.record_metric.call_args_list if c.args[0] == "api_error_count"
        ][-1]
        assert labels["retryable"] == "true"

    def test_subagent_count_carries_reported_status(self, inmemory_otel_setup):
        _, plugin = inmemory_otel_setup
        hooks.on_session_start(session_id="p", model="m", platform="cli")
        hooks.on_subagent_start(
            parent_session_id="p", child_session_id="c", child_role="leaf", child_goal="g"
        )
        plugin.record_metric = MagicMock()
        hooks.on_subagent_stop(parent_session_id="p", child_session_id="c", child_status="Timeout")
        labels = next(
            c.args[2] for c in plugin.record_metric.call_args_list if c.args[0] == "subagent_count"
        )
        assert labels == {"role": "leaf", "status": "error", "child_status": "timeout"}
