"""Integration: a config whose every ``backends:`` entry is invalid still
registers the hooks and fills the live store (#610), like an empty config."""

from unittest.mock import patch

import pytest
from _helpers import clear_backend_env

import hermes_otel
from hermes_otel import hooks
from hermes_otel.backend_config import BackendConfig
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin


class _Ctx:
    """The slice of Hermes' plugin context ``register()`` uses."""

    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback


@pytest.fixture
def invalid_only_plugin(monkeypatch, tmp_path):
    import hermes_otel.live_store as live_store_mod
    import hermes_otel.live_store_registry as registry
    import hermes_otel.tracer as tracer_mod

    clear_backend_env(monkeypatch)
    monkeypatch.setattr(live_store_mod, "_default_db_path", lambda: str(tmp_path / "live.db"))
    registry._LIVE_STORE = None
    config = HermesOtelConfig(
        backends=[
            BackendConfig(type="nope", endpoint="http://localhost:4318/v1/traces"),
            BackendConfig(type="langfuse"),  # missing its keys
        ],
        dashboard_live=True,
    )
    plugin = HermesOTelPlugin(config=config)
    monkeypatch.setattr(tracer_mod, "_tracer", plugin)
    with patch("hermes_otel.pipeline.trace.set_tracer_provider"):
        yield plugin
    plugin.shutdown()
    if registry._LIVE_STORE is not None:
        registry._LIVE_STORE.close()
    registry._LIVE_STORE = None


def test_hooks_register_and_the_live_store_fills(invalid_only_plugin):
    from hermes_otel.live_store_registry import get_live_store

    ctx = _Ctx()
    hermes_otel.register(ctx)

    assert invalid_only_plugin.is_enabled is True
    assert invalid_only_plugin.pipeline.live_active is True
    assert {name for name, _ in hooks.HOOKS} <= set(ctx.hooks)

    ctx.hooks["on_session_start"](session_id="s1", model="gpt-4", platform="cli")
    ctx.hooks["pre_tool_call"](
        tool_name="bash", args={"command": "ls"}, task_id="t1", session_id="s1"
    )
    ctx.hooks["post_tool_call"](
        tool_name="bash", args={}, result="ok", task_id="t1", session_id="s1"
    )
    ctx.hooks["on_session_end"](
        session_id="s1", completed=True, interrupted=False, model="gpt-4", platform="cli"
    )

    names = {s["name"] for s in get_live_store().spans()}
    assert {"agent", "tool.bash"} <= names
