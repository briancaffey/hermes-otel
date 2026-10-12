"""One ``mock_tracer`` for the single-hook unit tests (three files used to
carry their own, each stubbing a different subset of ``spans.*``)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def build_mock_tracer(enabled: bool = True) -> MagicMock:
    """A MagicMock tracer with real ``sessions`` / ``config`` and a dict-backed
    span registry so hooks that look spans up behave as in production.

    ``spans._active_spans`` is the registry; ``spans._roots`` the session roots
    tests may seed for ``get_session_root``. Everything else on ``spans`` is a
    MagicMock method (push/pop_parent, register_skill_span, ...)."""
    from hermes_otel.plugin_config import HermesOtelConfig
    from hermes_otel.session_state import SessionState

    tracer = MagicMock()
    tracer.is_enabled = enabled
    tracer.spans = MagicMock()
    tracer.spans._active_spans = {}
    tracer.spans._roots = {}
    tracer.spans.has_span = lambda key: key in tracer.spans._active_spans
    tracer.spans.get_span = lambda key: tracer.spans._active_spans.get(key)
    tracer.spans.get_session_root = lambda sid=None: tracer.spans._roots.get(sid)
    tracer.spans.get_current_parent = lambda sid=None: None
    tracer.spans.single_active_session = lambda: None
    tracer.spans.get_subagent = lambda sid: None
    tracer.spans.pop_subagent = lambda sid: None
    tracer.sessions = SessionState()
    tracer.config = HermesOtelConfig()
    return tracer


@pytest.fixture()
def mock_tracer():
    """An enabled mock tracer, installed as ``hermes_otel.tracer.get_tracer()``."""
    tracer = build_mock_tracer(enabled=True)
    with patch("hermes_otel.tracer.get_tracer", return_value=tracer):
        yield tracer


@pytest.fixture()
def disabled_tracer():
    tracer = build_mock_tracer(enabled=False)
    with patch("hermes_otel.tracer.get_tracer", return_value=tracer):
        yield tracer
