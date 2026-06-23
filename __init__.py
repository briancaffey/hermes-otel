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
    extra_hooks = 0
    try:
        ctx.register_hook("api_request_error", hooks.on_api_request_error)
        extra_hooks += 1
    except Exception:
        debug_log("api_request_error hook unavailable")

    # Session hooks (available on newer Hermes versions)
    session_hooks = 0
    for hook_name, callback in [
        ("on_session_start", hooks.on_session_start),
        ("on_session_end", hooks.on_session_end),
    ]:
        try:
            ctx.register_hook(hook_name, callback)
            session_hooks += 1
        except Exception:
            debug_log(f"{hook_name} hook unavailable")

    logger.info(f"[hermes-otel] Registered {6 + extra_hooks + session_hooks} hooks")
