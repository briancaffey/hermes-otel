"""Wire the CPU/GPU/tool profiling into the hermes-otel hook lifecycle.

The profiling features run side-effects at specific points in the plugin's hook
lifecycle: session start/end, each tool call, and each LLM call. This module
attaches them by wrapping the relevant hook callbacks at import time instead of
interleaving the calls into each hook body, which keeps the profiling logic in
its own module and decoupled from the internals of ``hooks.py``.

Hooks are wrapped by name and forward ``*args``/``**kwargs`` unchanged, so the
integration is unaffected by parameter reordering or additions as long as the
hook names are stable. Profiling state, turn accounting and interrupt handling
live in ``tool_profiler.py``; this module only decides when each call fires:

  * on_session_start  -> start the CPU/GPU poller                (before, always)
  * on_pre_llm_call   -> advance the per-turn counter             (before, always)
  * <llm span create> -> stamp ``hermes.turn.number`` on the span (at creation)
  * on_pre_tool_call  -> record the tool's wall-clock start        (before, if tracing on)
  * on_post_tool_call -> slice CPU/GPU timelines for the tool      (after, if tracing on)
  * on_session_end    -> arm the next turn                         (after, if tracing on)

The "always" side-effects run regardless of whether tracing is enabled; the
tool-call and session-end side-effects run only when the tracer is enabled, to
match the ``if not tracer.is_enabled: return`` guard in each corresponding hook.
The turn number is applied by wrapping ``start_span`` so the attribute is added
at span creation and works for every backend (OTLP, LangSmith).
"""

import functools
import inspect

_INSTALLED = False


def _debug(msg):
    try:
        from .debug_utils import debug_log

        debug_log(msg)
    except Exception:
        pass


def _tracing_enabled():
    """Return whether the tracer is enabled, used to gate the tool-call and
    session-end side-effects so they match the guard in the corresponding hook."""
    try:
        from .tracer import get_tracer

        return get_tracer().is_enabled
    except Exception:
        return False


def _bind(func, args, kwargs):
    """Map a wrapped call's ``*args`` / ``**kwargs`` onto ``func``'s parameter
    names and return a name->value dict. The hook's ``**kwargs`` overflow appears
    under the key of the variadic-keyword parameter (conventionally ``"kwargs"``).
    Tolerant of positional-or-keyword dispatch and of parameters being added or
    removed in a future Hermes release (unknown names are simply absent)."""
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)
    except Exception:
        return dict(kwargs)


def _extra_kwargs(params):
    """Return the dict captured by the wrapped hook's ``**kwargs``, forwarded
    verbatim to the profiling helpers."""
    extra = params.get("kwargs")
    return extra if isinstance(extra, dict) else {}


# --------------------------------------------------------------------------- #
# Per-hook side-effects (each receives the bound-argument dict of the hook).
# --------------------------------------------------------------------------- #
def _session_start_before(params):
    session_id = params.get("session_id")
    extra = _extra_kwargs(params)
    try:
        from . import tool_profiler

        tool_profiler.start_session_poller(session_id, **extra)
    except Exception as e:
        _debug(f"Tool profiler start failed: {e}")


def _pre_llm_before(params):
    session_id = params.get("session_id")
    is_first_turn = params.get("is_first_turn", False)
    extra = _extra_kwargs(params)

    # Advance the per-tool-profiling turn counter (also lazily starts the poller
    # on continuation / resumed turns where on_session_start did not fire).
    try:
        from . import tool_profiler

        tool_profiler.note_turn(session_id, is_first_turn=is_first_turn, **extra)
    except Exception as e:
        _debug(f"Tool profiler note_turn failed: {e}")


def _pre_tool_before(params):
    if not _tracing_enabled():
        return
    tool_name = params.get("tool_name")
    task_id = params.get("task_id")
    extra = _extra_kwargs(params)
    key = f"{tool_name}:{task_id}"
    try:
        from . import tool_profiler

        tool_profiler.record_tool_start(key, session_id=extra.get("session_id"))
    except Exception as e:
        _debug(f"Tool profiler record_tool_start failed: {e}")


def _post_tool_after(params):
    if not _tracing_enabled():
        return
    tool_name = params.get("tool_name")
    task_id = params.get("task_id")
    args = params.get("args")
    result = params.get("result")
    extra = _extra_kwargs(params)
    key = f"{tool_name}:{task_id}"
    try:
        from . import tool_profiler

        tool_profiler.record_tool_end(
            key, tool_name, session_id=extra.get("session_id"), args=args, result=result
        )
    except Exception as e:
        _debug(f"Tool profiler record_tool_end failed: {e}")


def _session_end_after(params):
    if not _tracing_enabled():
        return
    session_id = params.get("session_id")
    extra = _extra_kwargs(params)

    # Arm the next turn so the counter advances once per prompt, not once per
    # agent-loop LLM call. The CPU+GPU poller is intentionally left running; it
    # is stopped at process exit via tool_profiler's atexit handler.
    try:
        from . import tool_profiler

        tool_profiler.end_turn(session_id, **extra)
    except Exception as e:
        _debug(f"Tool profiler end_turn failed: {e}")


# --------------------------------------------------------------------------- #
# Wrapping machinery.
# --------------------------------------------------------------------------- #
def _wrap(original, before=None, after=None):
    """Return ``original`` wrapped so ``before(params)`` runs before it and
    ``after(params)`` after it. Both are best-effort; a failure in either never
    breaks the underlying hook. Idempotent."""
    if getattr(original, "_hermes_profiling_wrapped", False):
        return original

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        params = _bind(original, args, kwargs)
        if before is not None:
            try:
                before(params)
            except Exception as e:
                _debug(f"profiling before-hook failed: {e}")
        result = original(*args, **kwargs)
        if after is not None:
            try:
                after(params)
            except Exception as e:
                _debug(f"profiling after-hook failed: {e}")
        return result

    wrapper._hermes_profiling_wrapped = True
    return wrapper


def _wrap_hook(module, name, before=None, after=None):
    original = getattr(module, name, None)
    if not callable(original):
        _debug(f"profiling: hook {name} not found on hooks module; skipping wrap")
        return
    setattr(module, name, _wrap(original, before=before, after=after))


def _install_turn_stamp():
    """Wrap ``HermesOTelPlugin.start_span`` so the LLM span carries
    ``hermes.turn.number`` — the join key the profiler dashboard uses to tie
    tool_execution.csv rows to the trace they belong to. Adding it at span
    creation keeps it backend-agnostic (OTLP and LangSmith both honour the
    attribute dict). Idempotent."""
    try:
        from .tracer import HermesOTelPlugin
    except Exception as e:
        _debug(f"profiling: could not import tracer for turn stamp: {e}")
        return

    original = HermesOTelPlugin.start_span
    if getattr(original, "_hermes_profiling_wrapped", False):
        return
    sig = inspect.signature(original)

    @functools.wraps(original)
    def start_span(*args, **kwargs):
        try:
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            a = bound.arguments
            if a.get("kind") == "llm" and a.get("session_id"):
                from . import tool_profiler

                turn_no = tool_profiler.current_turn(a.get("session_id"))
                if turn_no:
                    attrs = dict(a.get("attributes") or {})
                    attrs["hermes.turn.number"] = turn_no
                    bound.arguments["attributes"] = attrs
                    return original(*bound.args, **bound.kwargs)
        except Exception as e:
            _debug(f"could not stamp turn number on llm span: {e}")
        return original(*args, **kwargs)

    start_span._hermes_profiling_wrapped = True
    HermesOTelPlugin.start_span = start_span


def install(hooks_module):
    """Wrap the hermes-otel hooks so the profiling side-effects fire at the
    right lifecycle points. Safe to call more than once (no-op after the first).

    Args:
        hooks_module: the ``hooks`` module object whose top-level hook functions
            should be wrapped in place (pass ``sys.modules[__name__]`` from
            within hooks.py).
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    _wrap_hook(hooks_module, "on_session_start", before=_session_start_before)
    _wrap_hook(hooks_module, "on_pre_llm_call", before=_pre_llm_before)
    _wrap_hook(hooks_module, "on_pre_tool_call", before=_pre_tool_before)
    _wrap_hook(hooks_module, "on_post_tool_call", after=_post_tool_after)
    _wrap_hook(hooks_module, "on_session_end", after=_session_end_after)
    _install_turn_stamp()
    _debug("advanced profiling hooks installed")
