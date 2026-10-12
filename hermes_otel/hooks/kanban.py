"""Kanban lifecycle hooks (#31): one short span per board transition.

Hermes' kanban is a durable task board. A dispatcher (inside the gateway, or
``hermes kanban dispatch``) claims a ready card and spawns a worker: a fresh
``hermes -p <assignee> chat`` process that carries the card in its
environment (so #357 already stamps the worker's Resource with
``hermes.kanban.task_id`` / ``board`` / ``run_id``). The worker finishes the
card with ``kanban_complete`` or ``kanban_block``.

The hooks fire in two processes, and no span is ever shared across them:

* **Dispatcher side** (``kanban_task_claimed``, ``on_kanban_worker_spawned``,
  ``on_kanban_worker_exited``, ``on_kanban_worker_stale_claim``): each
  transition is a span of its own, the root of its own trace. The dispatcher
  serves every session, so nesting under whatever happens to be in flight
  would mis-parent it.
* **Worker side** (``kanban_task_completed``, ``kanban_task_blocked``): the
  transition fires from inside the ``kanban_complete`` / ``kanban_block``
  tool, so the span lands in the worker's own trace next to that tool span
  (under the in-flight ``llm`` span, or the turn root), and the worker's turn
  root is stamped with the card identity and outcome.
  Run by a human (``hermes kanban complete``), there is no turn, and the span
  is a root like the dispatcher's.

Correlation across the two is by ``hermes.kanban.task_id`` and
``hermes.kanban.run_id`` on every span (and on the worker's Resource).
Every handler is an observer: Hermes ignores the return value, and every
fire site is best-effort, so nothing here can break a board transition.
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, Optional, Tuple

from .. import log_events as EV
from ..debug_utils import debug_log
from ..helpers import truncate_string
from ._common import _emit_event, _fail_open, _preview_for, get_tracer
from .attributes import _session_identity_attributes

_ID_MAX = 200
_SHORT_MAX = 64
_seq = itertools.count(1)

# A worker that died on its own, or was killed, is a failure. A clean exit, a
# rate-limit back-off and an unknown exit stay OK so error rates only count
# real failures.
_FAILED_EXIT_KINDS = frozenset({"nonzero_exit", "signaled"})


def kanban_identity(
    task_id: Any, board: Any, assignee: Any, run_id: Any, event: str
) -> Dict[str, Any]:
    attrs: Dict[str, Any] = {"hermes.span_kind": "kanban", "hermes.kanban.event": event}
    if task_id:
        attrs["hermes.kanban.task_id"] = truncate_string(str(task_id), _ID_MAX)
    if board:
        attrs["hermes.kanban.board"] = truncate_string(str(board), _ID_MAX)
    if assignee:
        attrs["hermes.kanban.assignee"] = truncate_string(str(assignee), _ID_MAX)
    if run_id is not None and str(run_id) != "":
        attrs["hermes.kanban.run_id"] = truncate_string(str(run_id), _SHORT_MAX)
    return attrs


def _own_root():
    """A parent that forces a new trace (the OTel invalid span)."""
    try:
        from opentelemetry.trace import INVALID_SPAN

        return INVALID_SPAN
    except Exception:  # pragma: no cover - OTel is a hard dependency
        return None


def _worker_turn(tracer) -> Tuple[Optional[str], Any, Any]:
    """``(session_id, parent, root)`` of the one in-flight turn, else ``(None, None, None)``.

    The task hooks carry no session id. A worker runs exactly one session, so
    when exactly one turn is in flight the transition belongs to it; with
    several (a gateway) or none (a human on the CLI) it stays unattributed.
    """
    live = tracer.spans.single_active_session()
    if not live:
        return None, None, None
    session_id, root = live
    return session_id, tracer.spans.get_current_parent(session_id) or root, root


def set_attributes(span: Any, attributes: Dict[str, Any]) -> None:
    if span is None or not hasattr(span, "set_attribute"):
        return
    for key, value in attributes.items():
        if value is None:
            continue
        try:
            span.set_attribute(key, value)
        except Exception:  # pragma: no cover - never let telemetry raise
            pass


def _transition(
    tracer,
    name: str,
    attributes: Dict[str, Any],
    *,
    session_id: Optional[str],
    parent: Any,
    status: str,
    outcome: str,
    severity: str = "INFO",
) -> None:
    """Open and immediately close one transition span, with its event and counter."""
    key = f"kanban:{name}:{attributes.get('hermes.kanban.task_id', '')}:{next(_seq)}"
    span = tracer.start_span(
        name=name,
        key=key,
        kind="general",
        attributes=attributes,
        session_id=session_id,
        parent=parent,
    )
    _emit_event(
        tracer,
        EV.KANBAN_TRANSITION,
        f"kanban {outcome} {attributes.get('hermes.kanban.task_id', '')}".rstrip(),
        {k: v for k, v in attributes.items() if k.startswith(EV.KANBAN_PREFIXES)},
        severity=severity,
        session_id=session_id,
        span=span,
    )
    tracer.end_span(key, attributes={}, status=status)
    board = attributes.get("hermes.kanban.board") or "unknown"
    tracer.record_metric("kanban_task_count", 1, {"outcome": outcome, "board": board})
    debug_log(f"  kanban span: {name} outcome={outcome} status={status}")


# --- dispatcher side ---------------------------------------------------------


@_fail_open
def on_kanban_task_claimed(
    task_id: Any = None,
    board: Any = None,
    assignee: Any = None,
    run_id: Any = None,
    profile_name: Any = None,
    **kwargs: Any,
):
    """The dispatcher claimed a ready card (fires inside the dispatch lock: stay fast)."""
    debug_log(f"kanban_task_claimed fired: task={task_id} run={run_id}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return
    attrs = kanban_identity(task_id, board, assignee, run_id, "claimed")
    _transition(
        tracer,
        "kanban.claim",
        attrs,
        session_id=None,
        parent=_own_root(),
        status="ok",
        outcome="claimed",
    )


@_fail_open
def on_kanban_worker_spawned(
    task_id: Any = None,
    board: Any = None,
    assignee: Any = None,
    run_id: Any = None,
    worker_pid: Any = None,
    **kwargs: Any,
):
    """The dispatcher spawned the worker process for a claimed card.

    ``workspace_path`` is deliberately not recorded: Hermes flags it as a
    filesystem path that can reveal project layout or usernames.
    """
    debug_log(f"on_kanban_worker_spawned fired: task={task_id} pid={worker_pid}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return
    attrs = kanban_identity(task_id, board, assignee, run_id, "spawned")
    if isinstance(worker_pid, int) and not isinstance(worker_pid, bool):
        attrs["hermes.kanban.worker_pid"] = worker_pid
    _transition(
        tracer,
        "kanban.spawn",
        attrs,
        session_id=None,
        parent=_own_root(),
        status="ok",
        outcome="spawned",
    )


@_fail_open
def on_kanban_worker_exited(
    task_id: Any = None,
    board: Any = None,
    assignee: Any = None,
    run_id: Any = None,
    worker_pid: Any = None,
    exit_kind: Any = None,
    exit_code: Any = None,
    outcome: Any = None,
    retry_status: Any = None,
    **kwargs: Any,
):
    """A dispatcher tick found a worker's process gone and reclaimed its run."""
    debug_log(f"on_kanban_worker_exited fired: task={task_id} kind={exit_kind}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return
    attrs = kanban_identity(task_id, board, assignee, run_id, "worker_exited")
    if isinstance(worker_pid, int) and not isinstance(worker_pid, bool):
        attrs["hermes.kanban.worker_pid"] = worker_pid
    kind = truncate_string(str(exit_kind), _SHORT_MAX) if exit_kind else "unknown"
    attrs["hermes.kanban.exit_kind"] = kind
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        attrs["hermes.kanban.exit_code"] = exit_code
    if outcome:
        attrs["hermes.kanban.outcome"] = truncate_string(str(outcome), _SHORT_MAX)
    if retry_status:
        attrs["hermes.kanban.retry_status"] = truncate_string(str(retry_status), _SHORT_MAX)
    failed = kind in _FAILED_EXIT_KINDS
    _transition(
        tracer,
        "kanban.worker_exit",
        attrs,
        session_id=None,
        parent=_own_root(),
        status="error" if failed else "ok",
        outcome="worker_exited",
        severity="ERROR" if failed else "INFO",
    )


@_fail_open
def on_kanban_worker_stale_claim(
    task_id: Any = None,
    board: Any = None,
    assignee: Any = None,
    run_id: Any = None,
    worker_pid: Any = None,
    heartbeat_stale: Any = None,
    retry_status: Any = None,
    **kwargs: Any,
):
    """A claim's TTL expired with no heartbeat and the dispatcher reclaimed the card."""
    debug_log(f"on_kanban_worker_stale_claim fired: task={task_id}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return
    attrs = kanban_identity(task_id, board, assignee, run_id, "stale_claim")
    if isinstance(worker_pid, int) and not isinstance(worker_pid, bool):
        attrs["hermes.kanban.worker_pid"] = worker_pid
    if isinstance(heartbeat_stale, bool):
        attrs["hermes.kanban.heartbeat_stale"] = heartbeat_stale
    if retry_status:
        attrs["hermes.kanban.retry_status"] = truncate_string(str(retry_status), _SHORT_MAX)
    _transition(
        tracer,
        "kanban.stale_claim",
        attrs,
        session_id=None,
        parent=_own_root(),
        status="ok",
        outcome="stale_claim",
        severity="WARN",
    )


# --- worker side -------------------------------------------------------------


def _finish(tracer, name: str, attrs: Dict[str, Any], outcome: str, text_key: str, text: Any):
    preview = _preview_for(tracer, "tool_output", text) if text else None
    if preview is not None:
        attrs[text_key] = preview
    attrs["hermes.kanban.outcome"] = outcome
    session_id, parent, root = _worker_turn(tracer)
    if session_id:
        # session.id on the span itself: the live store and session-keyed
        # readers attach it to the worker's turn by this, not by the parent.
        attrs.update(_session_identity_attributes(session_id))
    if root is not None:
        # The worker's turn root learns which card it worked and how it ended,
        # so span-only readers (the bundled dashboard's live store, a backend
        # that cannot filter on Resource attributes) can find it too.
        set_attributes(
            root,
            {
                "hermes.kanban.task_id": attrs.get("hermes.kanban.task_id"),
                "hermes.kanban.board": attrs.get("hermes.kanban.board"),
                "hermes.kanban.run_id": attrs.get("hermes.kanban.run_id"),
                "hermes.kanban.outcome": outcome,
            },
        )
    _transition(
        tracer,
        name,
        attrs,
        session_id=session_id,
        parent=parent if parent is not None else _own_root(),
        status="ok",
        outcome=outcome,
        severity="WARN" if outcome == "blocked" else "INFO",
    )


@_fail_open
def on_kanban_task_completed(
    task_id: Any = None,
    board: Any = None,
    assignee: Any = None,
    run_id: Any = None,
    summary: Any = None,
    **kwargs: Any,
):
    """The worker (or a human) completed the card."""
    debug_log(f"kanban_task_completed fired: task={task_id} run={run_id}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return
    attrs = kanban_identity(task_id, board, assignee, run_id, "completed")
    _finish(tracer, "kanban.complete", attrs, "completed", "hermes.kanban.summary", summary)


@_fail_open
def on_kanban_task_blocked(
    task_id: Any = None,
    board: Any = None,
    assignee: Any = None,
    run_id: Any = None,
    reason: Any = None,
    **kwargs: Any,
):
    """The worker (or the dispatcher's breaker, or a human) blocked the card.

    Blocked is a legitimate outcome (the card waits for a human), so the span
    status stays OK; ``hermes.kanban.outcome`` carries the fact.
    """
    debug_log(f"kanban_task_blocked fired: task={task_id} run={run_id}")
    tracer = get_tracer()
    if not tracer.is_enabled:
        return
    attrs = kanban_identity(task_id, board, assignee, run_id, "blocked")
    _finish(tracer, "kanban.block", attrs, "blocked", "hermes.kanban.reason", reason)


# Private spellings kept for one release (#411): import the public names.
_identity = kanban_identity
