"""The metric instruments: one table drives instrument creation and
``HermesOTelPlugin.record_metric`` (#95), plus the Views, temporality and
aggregation helpers the metric exporters need (#233).

Split out of ``tracer.py`` in #405; ``tracer`` re-exports every public name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    import opentelemetry.sdk.metrics  # noqa: F401

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover — plugin is unusable without OTel
    _OTEL_AVAILABLE = False


# One table drives both instrument creation and ``record_metric``: the
# hook-side key, the OTLP name every backend and the live store see, the
# instrument kind, the unit, the description, how the value is applied, and
# whether the instrument is part of the OTel GenAI conventions (gated by
# ``emit_genai_metrics``). ``value`` modes: ``one`` adds 1 per call, ``int``
# adds ``int(value)``, ``float`` adds ``value``, ``record`` records a
# histogram point (as an int when the unit is ``{token}``).


@dataclass(frozen=True)
class InstrumentSpec:
    name: str
    kind: str  # counter | histogram
    unit: str
    description: str
    value: str  # one | int | float | record
    genai: bool = False
    # Explicit histogram bucket boundaries (#233). The SDK default is a
    # millisecond-scale list, wrong for every seconds and token histogram.
    boundaries: Optional[Tuple[float, ...]] = None
    # The labels this instrument may carry; anything else is dropped by a
    # View so a stray attribute can never fan a series out. ``profile`` is
    # always allowed (added by record_metric).
    labels: Tuple[str, ...] = ()
    # Superseded by a seconds instrument; documented as deprecated.
    deprecated: bool = False


# Bucket boundaries. The seconds/token lists are the ones the OTel GenAI
# conventions specify; the human-timescale list is the spec's
# ``gen_ai.invoke_workflow.duration`` list reused for sessions and approvals;
# the millisecond lists keep the deprecated ``ms`` histograms readable until
# they are removed (2.0).
_B_OP_SECONDS = (
    0.01,
    0.02,
    0.04,
    0.08,
    0.16,
    0.32,
    0.64,
    1.28,
    2.56,
    5.12,
    10.24,
    20.48,
    40.96,
    81.92,
)
_B_TOKENS = (
    1,
    4,
    16,
    64,
    256,
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
    67108864,
)
_B_HUMAN_SECONDS = (1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200)
_B_SMALL_COUNTS = (1, 2, 4, 8, 16, 32, 64, 128)
_B_TOOL_MS = (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000)
_B_WAIT_MS = (100, 500, 1000, 5000, 10000, 30000, 60000, 300000, 600000)
_B_RUN_MS = (1000, 5000, 10000, 30000, 60000, 120000, 300000, 600000, 1800000)
_MODEL_LABELS = ("model", "provider")
_GENAI_LABELS = (
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
)


_INSTRUMENTS: Dict[str, InstrumentSpec] = {
    "session_count": InstrumentSpec(
        "hermes.session.count",
        "counter",
        "{session}",
        "Sessions created",
        "one",
        labels=("platform",),
    ),
    "session_turns": InstrumentSpec(
        "hermes.session.turns",
        "histogram",
        "{turn}",
        "Turns per session, recorded when the session is finalized",
        "record",
        boundaries=_B_SMALL_COUNTS,
        labels=("platform", "reason"),
    ),
    "session_duration": InstrumentSpec(
        "hermes.session.duration",
        "histogram",
        "s",
        "Session length from first turn to finalize",
        "record",
        boundaries=_B_HUMAN_SECONDS,
        labels=("platform", "reason"),
    ),
    "token_usage": InstrumentSpec(
        "hermes.token.usage",
        "counter",
        "{token}",
        "Tokens consumed by type",
        "int",
        labels=_MODEL_LABELS + ("token_type", "query_source"),
    ),
    "prompt_cache_tokens": InstrumentSpec(
        "hermes.prompt_cache.tokens",
        "counter",
        "{token}",
        "Prompt tokens by cache result",
        "int",
        labels=_MODEL_LABELS + ("api_mode", "cache_result"),
    ),
    "prompt_cache_observations": InstrumentSpec(
        "hermes.prompt_cache.observations",
        "counter",
        "{request}",
        "Requests with provider-reported prompt-cache usage",
        "one",
        labels=_MODEL_LABELS + ("api_mode", "cache_result"),
    ),
    "cost_usage": InstrumentSpec(
        "hermes.cost.usage",
        "counter",
        "USD",
        "USD cost per API call, as priced by Hermes (actual or estimated)",
        "float",
        labels=_MODEL_LABELS + ("cost_status", "query_source"),
    ),
    "tool_duration": InstrumentSpec(
        "hermes.tool.duration",
        "histogram",
        "ms",
        "Tool execution time",
        "record",
        boundaries=_B_TOOL_MS,
        labels=("tool_name", "gen_ai.tool.name"),
        deprecated=True,
    ),
    "message_count": InstrumentSpec(
        "hermes.message.count",
        "counter",
        "{message}",
        "Completed assistant messages",
        "one",
        labels=_MODEL_LABELS,
    ),
    "model_usage": InstrumentSpec(
        "hermes.model.usage",
        "counter",
        "{message}",
        "Messages per model and provider",
        "one",
        labels=_MODEL_LABELS + ("query_source",),
    ),
    "auxiliary_count": InstrumentSpec(
        "hermes.auxiliary.count",
        "counter",
        "{request}",
        "Auxiliary LLM provider attempts (titling, compression, MoA, vision, ...) by task",
        "one",
        labels=_MODEL_LABELS + ("aux_task", "status"),
    ),
    "skill_inferred": InstrumentSpec(
        "hermes.skill.inferred",
        "counter",
        "{hit}",
        "Skill-name inference hits on tool spans",
        "one",
        labels=("skill_name", "source"),
    ),
    "subagent_count": InstrumentSpec(
        "hermes.subagent.count",
        "counter",
        "{run}",
        "Delegated sub-agent runs by role and status",
        "one",
        labels=("role", "status", "child_status"),
    ),
    "subagent_duration": InstrumentSpec(
        "hermes.subagent.duration",
        "histogram",
        "ms",
        "Delegated sub-agent wall-clock duration",
        "record",
        boundaries=_B_RUN_MS,
        labels=("role",),
        deprecated=True,
    ),
    "api_error_count": InstrumentSpec(
        "hermes.api.error.count",
        "counter",
        "{request}",
        "Failed provider API requests by error type / status class",
        "one",
        labels=("error_type", "status_class", "retryable") + _MODEL_LABELS,
    ),
    "retry_count": InstrumentSpec(
        "hermes.retry.count",
        "counter",
        "{attempt}",
        "Provider API retry attempts",
        "int",
        labels=_MODEL_LABELS,
    ),
    "kanban_task_count": InstrumentSpec(
        "hermes.kanban.task.count",
        "counter",
        "{transition}",
        "Kanban board transitions (claimed, spawned, completed, blocked, worker_exited, stale_claim) by board",
        "one",
        labels=("outcome", "board"),
    ),
    "kanban_dispatch_count": InstrumentSpec(
        "hermes.kanban.dispatch.count",
        "counter",
        "{tick}",
        "Kanban dispatcher passes by board and outcome (ok, idle, skipped_locked); dry runs are not counted",
        "one",
        labels=("outcome", "board"),
    ),
    "kanban_dispatch_result": InstrumentSpec(
        "hermes.kanban.dispatch.result",
        "counter",
        "{task}",
        "What a dispatcher pass did, by kind (spawned, reclaimed, crashed, auto_blocked, ...): counts only, never task ids",
        "int",
        labels=("kind", "board"),
    ),
    "kanban_task_updated": InstrumentSpec(
        "hermes.kanban.task.updated",
        "counter",
        "{field}",
        "Kanban task-row edits outside claim/complete/block, one point per changed field name (never the value)",
        "one",
        labels=("field", "board"),
    ),
    "approval_count": InstrumentSpec(
        "hermes.approval.count",
        "counter",
        "{prompt}",
        "Human-in-the-loop approval prompts by choice / pattern",
        "one",
        labels=("choice",),
    ),
    "approval_duration": InstrumentSpec(
        "hermes.approval.duration",
        "histogram",
        "ms",
        "Human-decision wait time on approval prompts",
        "record",
        boundaries=_B_WAIT_MS,
        labels=("choice",),
        deprecated=True,
    ),
    # Seconds-based successors of the ``ms`` histograms above (#233). The
    # ``ms`` ones stay for one minor release, marked deprecated, then go in 2.0.
    "tool_duration_s": InstrumentSpec(
        "gen_ai.execute_tool.duration",
        "histogram",
        "s",
        "Duration of a single tool execution (OTel GenAI convention)",
        "record",
        genai=True,
        boundaries=_B_OP_SECONDS,
        labels=("gen_ai.tool.name", "error.type"),
    ),
    "approval_wait_s": InstrumentSpec(
        "hermes.approval.wait.duration",
        "histogram",
        "s",
        "Human / guardian decision wait time on approval prompts",
        "record",
        boundaries=_B_HUMAN_SECONDS,
        labels=("choice",),
    ),
    "subagent_run_s": InstrumentSpec(
        "hermes.subagent.run.duration",
        "histogram",
        "s",
        "Delegated sub-agent wall-clock duration",
        "record",
        boundaries=_B_HUMAN_SECONDS,
        labels=("role",),
    ),
    # OTel GenAI semantic-convention instruments, emitted alongside the
    # hermes.* ones so generic GenAI dashboards work without per-user config.
    # Units follow the spec: {token} for tokens, s for durations.
    "gen_ai.client.token.usage": InstrumentSpec(
        "gen_ai.client.token.usage",
        "histogram",
        "{token}",
        "Number of tokens used per client (LLM) operation, by type",
        "record",
        genai=True,
        boundaries=_B_TOKENS,
        labels=("gen_ai.token.type",) + _GENAI_LABELS,
    ),
    "gen_ai.client.operation.duration": InstrumentSpec(
        "gen_ai.client.operation.duration",
        "histogram",
        "s",
        "Duration of client (LLM) operations",
        "record",
        genai=True,
        boundaries=_B_OP_SECONDS,
        labels=_GENAI_LABELS + ("error.type",),
    ),
    "gen_ai.agent.token.usage": InstrumentSpec(
        "gen_ai.agent.token.usage",
        "histogram",
        "{token}",
        "Tokens used per agent invocation (session/turn rollup), by type",
        "record",
        genai=True,
        boundaries=_B_TOKENS,
        labels=("gen_ai.token.type",) + _GENAI_LABELS,
    ),
}


def metric_views(histogram: str = "explicit") -> List[Any]:
    """One ``View`` per instrument: the label allow-list and, for histograms
    with ``histogram="explicit"``, the bucket boundaries (#233).

    With ``exponential`` the boundaries are left to the readers' preferred
    aggregation (a View's aggregation would override every reader's).
    """
    if not _OTEL_AVAILABLE:
        return []
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View

    views: List[Any] = []
    for spec in _INSTRUMENTS.values():
        kwargs: Dict[str, Any] = {
            "instrument_name": spec.name,
            "attribute_keys": set(spec.labels) | {"profile"},
        }
        if spec.kind == "histogram" and spec.boundaries and histogram != "exponential":
            kwargs["aggregation"] = ExplicitBucketHistogramAggregation(
                boundaries=list(spec.boundaries)
            )
        views.append(View(**kwargs))
    return views


def metric_temporality_map(temporality: Optional[str]) -> Optional[Dict[Any, Any]]:
    """``preferred_temporality`` for an ``OTLPMetricExporter`` (#233).

    ``delta`` follows the OTLP exporter spec's ``delta`` preference: delta for
    counters, histograms and observable counters, cumulative for up-down
    counters and gauges. ``cumulative`` is cumulative for everything. ``None``
    leaves the SDK default (``OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE``
    or cumulative).
    """
    if not temporality or not _OTEL_AVAILABLE:
        return None
    from opentelemetry.sdk.metrics import (
        Counter,
        Histogram,
        ObservableCounter,
        ObservableGauge,
        ObservableUpDownCounter,
        UpDownCounter,
    )
    from opentelemetry.sdk.metrics.export import AggregationTemporality

    delta = AggregationTemporality.DELTA
    cumulative = AggregationTemporality.CUMULATIVE
    if temporality == "delta":
        return {
            Counter: delta,
            Histogram: delta,
            ObservableCounter: delta,
            UpDownCounter: cumulative,
            ObservableUpDownCounter: cumulative,
            ObservableGauge: cumulative,
        }
    return {
        Counter: cumulative,
        Histogram: cumulative,
        ObservableCounter: cumulative,
        UpDownCounter: cumulative,
        ObservableUpDownCounter: cumulative,
        ObservableGauge: cumulative,
    }


def metric_preferred_aggregation(histogram: str) -> Optional[Dict[Any, Any]]:
    """``preferred_aggregation`` for an exporter: exponential histograms on request."""
    if histogram != "exponential" or not _OTEL_AVAILABLE:
        return None
    from opentelemetry.sdk.metrics import Histogram
    from opentelemetry.sdk.metrics.view import ExponentialBucketHistogramAggregation

    return {Histogram: ExponentialBucketHistogramAggregation()}


# Labels whose value space is open (any model slug, tool or skill name, free
# text reason). record_metric folds values past ``metrics_label_limit`` into
# ``other`` because the Python SDK has no cardinality limit of its own.
_CAPPED_LABELS = frozenset(
    {
        "model",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "tool_name",
        "gen_ai.tool.name",
        "skill_name",
        "reason",
        "role",
        "child_status",
        "error_type",
    }
)


def metric_otlp_name(key: str) -> str:
    """The OTLP instrument name for a hook-side metric key (the key itself when unknown)."""
    spec = _INSTRUMENTS.get(key)
    return spec.name if spec else key


# The observable instruments ``host_bridge.create_host_metric_instruments``
# registers (semantic-convention names); the docs tests read this instead of
# the source.
HOST_METRIC_NAMES = (
    "process.cpu.utilization",
    "system.cpu.utilization",
    "hw.gpu.utilization",
    "hw.gpu.memory.usage",
    "hw.power",
)
