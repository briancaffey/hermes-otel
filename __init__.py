"""Hermes OTel plugin — registration.

Wires hook callbacks to the Hermes plugin system.
"""

from __future__ import annotations


def register(ctx):
    """Initialize OTel tracer and register all hooks."""
    # Imports are deferred so that loading this file outside a package
    # context (e.g. pytest's Package.setup on a rootdir-with-__init__.py
    # project whose directory name is not a valid Python identifier)
    # does not trigger the relative imports.
    from . import hooks
    from .debug_utils import configure_default_handler, debug_log, logger
    from .tracer import get_tracer

    # Install stderr handler on the hermes_otel logger unless the host app
    # has already wired up its own. Keeps the "✓ backend connected" banner
    # visible without forcing downstream apps to configure logging.
    configure_default_handler()

    tracer = get_tracer()
    tracer.init()

    if not tracer.is_enabled:
        return

    # Core hooks (always available)
    ctx.register_hook("pre_tool_call", hooks.on_pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.on_post_tool_call)
    ctx.register_hook("pre_llm_call", hooks.on_pre_llm_call)
    ctx.register_hook("post_llm_call", hooks.on_post_llm_call)
    ctx.register_hook("pre_api_request", hooks.on_pre_api_request)
    ctx.register_hook("post_api_request", hooks.on_post_api_request)

    # Session + sub-agent hooks (available on newer Hermes versions). Each is
    # registered defensively so an older Hermes that lacks a given hook name
    # doesn't break registration of the rest.
    optional_hooks = 0
    for hook_name, callback in [
        ("on_session_start", hooks.on_session_start),
        ("on_session_end", hooks.on_session_end),
        ("subagent_start", hooks.on_subagent_start),
        ("subagent_stop", hooks.on_subagent_stop),
        ("api_request_error", hooks.on_api_request_error),
    ]:
        try:
            ctx.register_hook(hook_name, callback)
            optional_hooks += 1
        except Exception:
            debug_log(f"{hook_name} hook unavailable")

    # Trace-context propagation to MCP servers. Registered only when the host
    # Hermes advertises the `mcp_request_headers` hook, so older Hermes builds
    # don't get an "unknown hook" warning. The hook injects a W3C `traceparent`
    # onto outbound MCP HTTP requests so MCP-server spans link into the agent's
    # trace. No-op (returns {}) when no span is active. See get_current_traceparent.
    mcp_hooks = 0
    try:
        from hermes_cli.plugins import VALID_HOOKS as _valid_hooks
    except Exception:
        _valid_hooks = None
    if _valid_hooks is None or "mcp_request_headers" in _valid_hooks:
        try:
            ctx.register_hook("mcp_request_headers", hooks.on_mcp_request_headers)
            mcp_hooks = 1
        except Exception:
            debug_log("mcp_request_headers hook unavailable")
    else:
        debug_log("mcp_request_headers hook not supported by this Hermes; skipping")

    logger.info(f"[hermes-otel] Registered {6 + optional_hooks + mcp_hooks} hooks")
