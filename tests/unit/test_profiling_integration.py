"""Tests for the hook-wrapping machinery that wires profiling into hooks.py.

profiling_integration.py wraps hooks by name and, separately, monkey-patches
HermesOTelPlugin.start_span in place to stamp hermes.turn.number onto LLM
spans. Both mutations are plain attribute assignments (not scoped to a single
call), so an autouse fixture captures/restores the pristine class attribute
around every test in this file to avoid leaking into unrelated test modules.
"""

from unittest.mock import MagicMock

import pytest

import hermes_otel.profiling_integration as pi
import hermes_otel.tool_profiler as tool_profiler_mod
from hermes_otel.tracer import HermesOTelPlugin


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch):
    monkeypatch.setattr(pi, "_INSTALLED", False)
    original_start_span = HermesOTelPlugin.start_span
    yield
    HermesOTelPlugin.start_span = original_start_span


# --------------------------------------------------------------------------- #
# _debug
# --------------------------------------------------------------------------- #
def test_debug_never_raises():
    pi._debug("anything")


# --------------------------------------------------------------------------- #
# _bind / _extra_kwargs
# --------------------------------------------------------------------------- #
class TestBind:
    def test_maps_positional_and_keyword_args(self):
        def hook(session_id, model=None, **kwargs):
            pass

        params = pi._bind(hook, ("s1",), {"model": "gpt-4", "extra_field": 1})
        assert params["session_id"] == "s1"
        assert params["model"] == "gpt-4"
        assert params["kwargs"] == {"extra_field": 1}

    def test_falls_back_to_raw_kwargs_when_signature_unavailable(self):
        params = pi._bind(None, ("a",), {"b": 2})
        assert params == {"b": 2}


class TestExtraKwargs:
    def test_returns_dict_under_kwargs_key(self):
        assert pi._extra_kwargs({"kwargs": {"a": 1}}) == {"a": 1}

    def test_returns_empty_when_missing(self):
        assert pi._extra_kwargs({}) == {}

    def test_returns_empty_when_not_a_dict(self):
        assert pi._extra_kwargs({"kwargs": "not-a-dict"}) == {}


# --------------------------------------------------------------------------- #
# _tracing_enabled
# --------------------------------------------------------------------------- #
class TestTracingEnabled:
    def test_true_when_tracer_enabled(self, monkeypatch):
        import hermes_otel.tracer as tracer_mod

        monkeypatch.setattr(tracer_mod, "get_tracer", lambda: MagicMock(is_enabled=True))
        assert pi._tracing_enabled() is True

    def test_false_when_tracer_disabled(self, monkeypatch):
        import hermes_otel.tracer as tracer_mod

        monkeypatch.setattr(tracer_mod, "get_tracer", lambda: MagicMock(is_enabled=False))
        assert pi._tracing_enabled() is False

    def test_false_when_tracer_unavailable(self, monkeypatch):
        import hermes_otel.tracer as tracer_mod

        def _raise():
            raise RuntimeError("boom")

        monkeypatch.setattr(tracer_mod, "get_tracer", _raise)
        assert pi._tracing_enabled() is False


# --------------------------------------------------------------------------- #
# Per-hook side-effects
# --------------------------------------------------------------------------- #
class TestSessionStartBefore:
    def test_calls_start_session_poller(self, monkeypatch):
        mock = MagicMock()
        monkeypatch.setattr(tool_profiler_mod, "start_session_poller", mock)
        pi._session_start_before({"session_id": "s1", "kwargs": {"extra": 1}})
        mock.assert_called_once_with("s1", extra=1)

    def test_exception_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            tool_profiler_mod, "start_session_poller", MagicMock(side_effect=RuntimeError("boom"))
        )
        pi._session_start_before({"session_id": "s1"})  # must not raise


class TestPreLlmBefore:
    def test_calls_note_turn(self, monkeypatch):
        mock = MagicMock()
        monkeypatch.setattr(tool_profiler_mod, "note_turn", mock)
        pi._pre_llm_before({"session_id": "s1", "is_first_turn": True, "kwargs": {}})
        mock.assert_called_once_with("s1", is_first_turn=True)

    def test_exception_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            tool_profiler_mod, "note_turn", MagicMock(side_effect=RuntimeError("boom"))
        )
        pi._pre_llm_before({"session_id": "s1"})  # must not raise


class TestPreToolBefore:
    def test_noop_when_tracing_disabled(self, monkeypatch):
        monkeypatch.setattr(pi, "_tracing_enabled", lambda: False)
        mock = MagicMock()
        monkeypatch.setattr(tool_profiler_mod, "record_tool_start", mock)
        pi._pre_tool_before({"tool_name": "read", "task_id": "t1"})
        mock.assert_not_called()

    def test_records_start_when_enabled(self, monkeypatch):
        monkeypatch.setattr(pi, "_tracing_enabled", lambda: True)
        mock = MagicMock()
        monkeypatch.setattr(tool_profiler_mod, "record_tool_start", mock)
        pi._pre_tool_before({"tool_name": "read", "task_id": "t1", "kwargs": {"session_id": "s1"}})
        mock.assert_called_once_with("read:t1", session_id="s1")

    def test_exception_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(pi, "_tracing_enabled", lambda: True)
        monkeypatch.setattr(
            tool_profiler_mod, "record_tool_start", MagicMock(side_effect=RuntimeError("boom"))
        )
        pi._pre_tool_before({"tool_name": "read", "task_id": "t1"})  # must not raise


class TestPostToolAfter:
    def test_noop_when_tracing_disabled(self, monkeypatch):
        monkeypatch.setattr(pi, "_tracing_enabled", lambda: False)
        mock = MagicMock()
        monkeypatch.setattr(tool_profiler_mod, "record_tool_end", mock)
        pi._post_tool_after({"tool_name": "read", "task_id": "t1"})
        mock.assert_not_called()

    def test_records_end_when_enabled(self, monkeypatch):
        monkeypatch.setattr(pi, "_tracing_enabled", lambda: True)
        mock = MagicMock()
        monkeypatch.setattr(tool_profiler_mod, "record_tool_end", mock)
        params = {
            "tool_name": "read",
            "task_id": "t1",
            "args": {"a": 1},
            "result": {"b": 2},
            "kwargs": {"session_id": "s1"},
        }
        pi._post_tool_after(params)
        mock.assert_called_once_with(
            "read:t1", "read", session_id="s1", args={"a": 1}, result={"b": 2}
        )

    def test_exception_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(pi, "_tracing_enabled", lambda: True)
        monkeypatch.setattr(
            tool_profiler_mod, "record_tool_end", MagicMock(side_effect=RuntimeError("boom"))
        )
        pi._post_tool_after({"tool_name": "read", "task_id": "t1"})  # must not raise


class TestSessionEndAfter:
    def test_noop_when_tracing_disabled(self, monkeypatch):
        monkeypatch.setattr(pi, "_tracing_enabled", lambda: False)
        mock = MagicMock()
        monkeypatch.setattr(tool_profiler_mod, "end_turn", mock)
        pi._session_end_after({"session_id": "s1"})
        mock.assert_not_called()

    def test_calls_end_turn_when_enabled(self, monkeypatch):
        monkeypatch.setattr(pi, "_tracing_enabled", lambda: True)
        mock = MagicMock()
        monkeypatch.setattr(tool_profiler_mod, "end_turn", mock)
        pi._session_end_after({"session_id": "s1", "kwargs": {}})
        mock.assert_called_once_with("s1")

    def test_exception_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(pi, "_tracing_enabled", lambda: True)
        monkeypatch.setattr(
            tool_profiler_mod, "end_turn", MagicMock(side_effect=RuntimeError("boom"))
        )
        pi._session_end_after({"session_id": "s1"})  # must not raise


# --------------------------------------------------------------------------- #
# _wrap / _wrap_hook
# --------------------------------------------------------------------------- #
class TestWrap:
    def test_calls_before_and_after_in_order_and_forwards_result(self):
        calls = []

        def original(x):
            calls.append("original")
            return x * 2

        wrapped = pi._wrap(
            original,
            before=lambda params: calls.append("before"),
            after=lambda params: calls.append("after"),
        )
        assert wrapped(21) == 42
        assert calls == ["before", "original", "after"]

    def test_is_idempotent(self):
        def original(x):
            return x

        once = pi._wrap(original)
        twice = pi._wrap(once)
        assert twice is once

    def test_before_exception_does_not_break_call(self):
        def original():
            return "ok"

        def bad_before(params):
            raise RuntimeError("boom")

        wrapped = pi._wrap(original, before=bad_before)
        assert wrapped() == "ok"

    def test_after_exception_does_not_break_call(self):
        def original():
            return "ok"

        def bad_after(params):
            raise RuntimeError("boom")

        wrapped = pi._wrap(original, after=bad_after)
        assert wrapped() == "ok"


class TestWrapHook:
    def test_wraps_existing_attribute(self):
        class Mod:
            @staticmethod
            def on_session_start(session_id):
                return session_id

        seen = []
        pi._wrap_hook(Mod, "on_session_start", before=lambda params: seen.append(params))
        assert Mod.on_session_start("s1") == "s1"
        assert seen[0]["session_id"] == "s1"

    def test_missing_attribute_is_a_noop(self):
        class Mod:
            pass

        pi._wrap_hook(Mod, "not_a_real_hook")  # must not raise
        assert not hasattr(Mod, "not_a_real_hook")


# --------------------------------------------------------------------------- #
# _install_turn_stamp
# --------------------------------------------------------------------------- #
def _fake_start_span_factory(captured):
    """Build a plain function (proper descriptor) so ``plugin.start_span(...)``
    correctly binds ``self`` to the calling instance."""

    def fake_start_span(
        self, name, key, kind="general", attributes=None, session_id=None, parent=None, links=None
    ):
        captured["attributes"] = attributes
        return "SPAN"

    return fake_start_span


class TestInstallTurnStamp:
    def test_adds_turn_number_when_current_turn_nonzero(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(HermesOTelPlugin, "start_span", _fake_start_span_factory(captured))
        monkeypatch.setattr(tool_profiler_mod, "current_turn", lambda session_id: 5)

        pi._install_turn_stamp()
        plugin = HermesOTelPlugin.__new__(HermesOTelPlugin)
        result = plugin.start_span(
            "llm.gpt4", "key1", kind="llm", session_id="s1", attributes={"x": 1}
        )

        assert result == "SPAN"
        assert captured["attributes"] == {"x": 1, "hermes.turn.number": 5}

    def test_leaves_span_untouched_when_current_turn_zero(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(HermesOTelPlugin, "start_span", _fake_start_span_factory(captured))
        monkeypatch.setattr(tool_profiler_mod, "current_turn", lambda session_id: 0)

        pi._install_turn_stamp()
        plugin = HermesOTelPlugin.__new__(HermesOTelPlugin)
        plugin.start_span("llm.gpt4", "key1", kind="llm", session_id="s1", attributes={"x": 1})

        assert captured["attributes"] == {"x": 1}

    def test_leaves_non_llm_spans_untouched(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(HermesOTelPlugin, "start_span", _fake_start_span_factory(captured))
        monkeypatch.setattr(tool_profiler_mod, "current_turn", lambda session_id: 5)

        pi._install_turn_stamp()
        plugin = HermesOTelPlugin.__new__(HermesOTelPlugin)
        plugin.start_span("tool.read", "key1", kind="tool", session_id="s1", attributes={"x": 1})

        assert captured["attributes"] == {"x": 1}

    def test_is_idempotent(self, monkeypatch):
        def fake(self, *a, **k):
            return "SPAN"

        monkeypatch.setattr(HermesOTelPlugin, "start_span", fake)
        pi._install_turn_stamp()
        wrapped_once = HermesOTelPlugin.start_span
        pi._install_turn_stamp()
        assert HermesOTelPlugin.start_span is wrapped_once


# --------------------------------------------------------------------------- #
# install()
# --------------------------------------------------------------------------- #
class TestInstall:
    def test_wraps_all_five_hooks_and_fires_side_effects(self, monkeypatch):
        class FakeHooks:
            @staticmethod
            def on_session_start(session_id=None, **kwargs):
                return "start"

            @staticmethod
            def on_pre_llm_call(session_id=None, is_first_turn=False, **kwargs):
                return "pre_llm"

            @staticmethod
            def on_pre_tool_call(tool_name=None, task_id=None, **kwargs):
                return "pre_tool"

            @staticmethod
            def on_post_tool_call(tool_name=None, task_id=None, args=None, result=None, **kwargs):
                return "post_tool"

            @staticmethod
            def on_session_end(session_id=None, **kwargs):
                return "session_end"

        start_mock = MagicMock()
        monkeypatch.setattr(tool_profiler_mod, "start_session_poller", start_mock)
        monkeypatch.setattr(pi, "_install_turn_stamp", MagicMock())

        pi.install(FakeHooks)

        assert FakeHooks.on_session_start(session_id="s1") == "start"
        start_mock.assert_called_once_with("s1")

    def test_second_call_is_a_noop(self, monkeypatch):
        class FakeHooks:
            @staticmethod
            def on_session_start(session_id=None, **kwargs):
                pass

            @staticmethod
            def on_pre_llm_call(session_id=None, **kwargs):
                pass

            @staticmethod
            def on_pre_tool_call(tool_name=None, task_id=None, **kwargs):
                pass

            @staticmethod
            def on_post_tool_call(tool_name=None, task_id=None, **kwargs):
                pass

            @staticmethod
            def on_session_end(session_id=None, **kwargs):
                pass

        monkeypatch.setattr(pi, "_install_turn_stamp", MagicMock())

        pi.install(FakeHooks)
        wrapped_once = FakeHooks.on_session_start
        pi.install(FakeHooks)
        assert FakeHooks.on_session_start is wrapped_once
