"""Hermes OTel plugin — registration.

Wires hook callbacks to the Hermes plugin system.
"""

from __future__ import annotations

import os

try:
    from .debug_utils import debug_log, mask_secret
    from .tracer import get_tracer
    from . import hooks
except ImportError:  # pragma: no cover - flat-module fallback for packaging
    from debug_utils import debug_log, mask_secret
    from tracer import get_tracer
    import hooks

debug_log("__init__.py loaded - starting plugin registration")


def register(ctx):
    """Initialize OTel tracer and register all hooks."""
    otel_endpoint = os.environ.get("OTEL_ENDPOINT", "").strip()
    langfuse_endpoint = os.environ.get("OTEL_LANGFUSE_ENDPOINT", "").strip()
    langfuse_pub = os.environ.get("OTEL_LANGFUSE_PUBLIC_API_KEY", "").strip()
    langfuse_sec = os.environ.get("OTEL_LANGFUSE_SECRET_API_KEY", "").strip()
    langsmith_tracing = os.environ.get("LANGSMITH_TRACING", "").strip()
    langsmith_key = os.environ.get("LANGSMITH_API_KEY", "").strip()
    langsmith_project = os.environ.get("LANGSMITH_PROJECT", "").strip()

    debug_log(f"register() called, OTEL_ENDPOINT={otel_endpoint or 'NOT SET'}")
    debug_log(f"  OTEL_LANGFUSE_ENDPOINT={langfuse_endpoint or 'NOT SET'}")
    debug_log(f"  OTEL_LANGFUSE_PUBLIC_API_KEY={mask_secret(langfuse_pub)}")
    debug_log(f"  OTEL_LANGFUSE_SECRET_API_KEY={mask_secret(langfuse_sec)}")
    debug_log(f"  LANGSMITH_TRACING={langsmith_tracing or 'NOT SET'}")
    debug_log(f"  LANGSMITH_API_KEY={mask_secret(langsmith_key)}")
    debug_log(f"  LANGSMITH_PROJECT={langsmith_project or 'NOT SET'}")

    print(f"[hermes-otel] register() called")
    print(f"[hermes-otel]   OTEL_ENDPOINT={'set' if otel_endpoint else 'not set'}")
    print(f"[hermes-otel]   OTEL_LANGFUSE_ENDPOINT={langfuse_endpoint or 'default'}")
    print(f"[hermes-otel]   OTEL_LANGFUSE_PUBLIC_API_KEY={'set' if langfuse_pub else 'not set'}")
    print(f"[hermes-otel]   OTEL_LANGFUSE_SECRET_API_KEY={'set' if langfuse_sec else 'not set'}")
    print(f"[hermes-otel]   LANGSMITH_TRACING={langsmith_tracing or 'not set'}")
    print(f"[hermes-otel]   LANGSMITH_API_KEY={'set' if langsmith_key else 'not set'}")
    print(f"[hermes-otel]   LANGSMITH_PROJECT={langsmith_project or 'default'}")

    # Initialize tracer (auto-detects Phoenix vs Langfuse)
    tracer = get_tracer()
    result = tracer.init()
    debug_log(f"tracer.init() returned {result}, is_enabled={tracer.is_enabled}")
    print(f"[hermes-otel] tracer.init() returned {result}, is_enabled={tracer.is_enabled}")

    if not tracer.is_enabled:
        debug_log("Tracer not enabled, skipping hook registration")
        print("[hermes-otel] Tracer not enabled, skipping hook registration")
        return

    # Register hook callbacks
    ctx.register_hook("pre_tool_call", hooks.on_pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.on_post_tool_call)
    ctx.register_hook("pre_llm_call", hooks.on_pre_llm_call)
    ctx.register_hook("post_llm_call", hooks.on_post_llm_call)
    ctx.register_hook("pre_api_request", hooks.on_pre_api_request)
    ctx.register_hook("post_api_request", hooks.on_post_api_request)
    debug_log("All 6 hooks registered")
    print("[hermes-otel] All 6 hooks registered")
