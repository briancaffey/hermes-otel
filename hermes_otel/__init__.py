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
    # has already wired up its own. Keeps the "✓ <backend> at <endpoint>" banner
    # visible without forcing downstream apps to configure logging.
    configure_default_handler()

    tracer = get_tracer()
    tracer.init()

    if not tracer.is_enabled:
        return

    # Every hook the manifest declares, in one table (hooks.HOOKS). Each is
    # registered defensively so a Hermes that lacks a given hook name doesn't
    # break registration of the rest.
    registered = 0
    for hook_name, callback in hooks.HOOKS:
        try:
            ctx.register_hook(hook_name, callback)
            registered += 1
        except Exception:
            debug_log(f"{hook_name} hook unavailable")

    # Trace-context propagation to MCP servers. `mcp_request_headers` is not a
    # hook in any Hermes release (it was proposed upstream in
    # NousResearch/hermes-agent#52211 and withdrawn because the MCP SDK 2.x
    # propagates trace context in-protocol), so it is not declared in
    # plugin.yaml. Register it ONLY when the running Hermes provably lists it
    # in VALID_HOOKS. When the registry cannot be inspected at all, fail
    # closed: the catalog validator diffs declared vs. registered hooks and an
    # undeclared registration fails admission (#130).
    try:
        from hermes_cli.plugins import VALID_HOOKS as _valid_hooks
    except Exception:
        _valid_hooks = None
    if _valid_hooks is not None and "mcp_request_headers" in _valid_hooks:
        try:
            ctx.register_hook("mcp_request_headers", hooks.on_mcp_request_headers)
            registered += 1
        except Exception:
            debug_log("mcp_request_headers hook unavailable")
    elif _valid_hooks is None:
        debug_log("hermes_cli.plugins.VALID_HOOKS not importable; skipping mcp_request_headers")
    else:
        debug_log("mcp_request_headers hook not supported by this Hermes; skipping")

    logger.info(f"[hermes-otel] Registered {registered} hooks")

    # Bundle a companion Hermes skill that explains how to turn on and read this
    # plugin's telemetry. Resolvable as `hermes_otel:observability` via
    # skill_view — and because the plugin instruments skill loads, invoking it
    # emits its own `skill.observability` span (the feature dogfooding itself).
    # Forward-compatible: older Hermes builds may not expose register_skill.
    skill_registered = False
    try:
        from pathlib import Path

        skill_path = Path(__file__).resolve().parent / "skills" / "observability" / "SKILL.md"
        if skill_path.exists():
            ctx.register_skill(
                "observability",
                skill_path,
                description=(
                    "Use for historical Hermes behavior, skill/tool usage, latency, errors, or cost. "
                    "Configure and query this agent's OpenTelemetry."
                ),
            )
            skill_registered = True
            debug_log("registered bundled skill: hermes_otel:observability")
    except Exception:
        debug_log("register_skill unavailable; skipping bundled observability skill")

    # Plugin skills are explicit-load only and therefore do not appear in
    # Hermes' <available_skills> catalog. Optionally (``discovery_prompt:
    # true``) give the model one bounded discovery hint so it knows telemetry
    # is an available evidence source. Off by default: it is a behavioural
    # directive injected into every session prompt (~80 tokens per turn), and
    # hermes-otel is observer-only unless the user opts in. Only registered
    # when the skill it points at actually registered, and never allowed to
    # raise — Hermes drops the whole plugin if register() fails.
    if skill_registered and tracer.config.discovery_prompt:
        try:
            ctx.register_system_prompt_section(
                "hermes-otel.discovery",
                "This Hermes agent exports OpenTelemetry through hermes-otel. For questions about "
                "past agent behavior—including skill/tool usage, model calls, latency, errors, "
                "retries, or cost—prefer the exported telemetry over logs or session-file inference. "
                "Load `hermes_otel:observability` before querying the configured backend.",
                max_chars=400,
            )
            debug_log("registered hermes-otel telemetry discovery prompt")
        except Exception as exc:  # AttributeError on old Hermes, ValueError on policy
            debug_log(f"skipping telemetry discovery prompt: {exc!r}")
