"""Helpers shared by the test tiers (importable because pytest's ``pythonpath``
includes ``tests/``). Keep them free of fixtures so any file can import them.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

# Every env var the backend resolvers read. One list, so a new backend's vars
# get cleared in every tier (they used to drift between two copies).
BACKEND_ENV_VARS: List[str] = [
    "OTEL_PHOENIX_ENDPOINT",
    "OTEL_PROJECT_NAME",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_WORKSPACE_ID",
    "OTEL_LANGFUSE_PUBLIC_API_KEY",
    "OTEL_LANGFUSE_SECRET_API_KEY",
    "OTEL_LANGFUSE_ENDPOINT",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "OTEL_SIGNOZ_ENDPOINT",
    "OTEL_SIGNOZ_INGESTION_KEY",
    "OTEL_JAEGER_ENDPOINT",
    "OTEL_TEMPO_ENDPOINT",
    "OTEL_UPTRACE_ENDPOINT",
    "OTEL_UPTRACE_DSN",
    "UPTRACE_DSN",
    "OTEL_OPENOBSERVE_ENDPOINT",
    "OTEL_OPENOBSERVE_USER",
    "OTEL_OPENOBSERVE_PASSWORD",
    "OTEL_OPENOBSERVE_STREAM",
    "OPENOBSERVE_USER",
    "OPENOBSERVE_PASSWORD",
    "OTEL_PARSEABLE_ENDPOINT",
    "OTEL_PARSEABLE_API_KEY",
    "PARSEABLE_API_KEY",
    "PARSEABLE_TRACES_DATASET",
    "PARSEABLE_METRICS_DATASET",
    "PARSEABLE_LOGS_DATASET",
    "WANDB_API_KEY",
    "WANDB_ENTITY",
    "WANDB_PROJECT",
    "DEFAULT_WANDB_ENTITY",
    "DEFAULT_WANDB_PROJECT",
    "OTEL_WEAVE_ENDPOINT",
    "OTEL_WEAVE_BASE_URL",
    "WANDB_OTLP_ENDPOINT",
    "HONEYCOMB_API_KEY",
    "OTEL_HONEYCOMB_ENDPOINT",
    "OTEL_HONEYCOMB_API_KEY",
]


def clear_backend_env(monkeypatch) -> None:
    """Remove every backend env var so init() starts from a clean slate."""
    for var in BACKEND_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def spans_by_name(spans: Iterable[Any], name: str) -> List[Any]:
    return [s for s in spans if s.name == name]


def span_by_name(spans: Iterable[Any], name: str) -> Any:
    """First span with this name; ValueError listing what exists otherwise."""
    spans = list(spans)
    matches = spans_by_name(spans, name)
    if not matches:
        raise ValueError(f"No span named {name!r} in {[s.name for s in spans]}")
    return matches[0]


def one(spans: Iterable[Any], name: str) -> Any:
    """Exactly one span with this name, else AssertionError."""
    spans = list(spans)
    matches = spans_by_name(spans, name)
    assert len(matches) == 1, f"expected one {name!r}, got {[s.name for s in spans]}"
    return matches[0]


def span_where(spans: Iterable[Any], name: str, attr: str, value: Any) -> Any:
    for s in spans:
        if s.name == name and dict(s.attributes).get(attr) == value:
            return s
    raise ValueError(f"No {name!r} span with {attr}={value!r}")


def parent_span_id(span: Any) -> Optional[int]:
    return span.parent.span_id if span.parent is not None else None


def metric_points(metric: Any) -> List[Any]:
    """All data points for a metric (histogram or counter); [] for None."""
    return list(metric.data.data_points) if metric is not None else []


def get_metric(metric_reader: Any, name: str) -> Any:
    """Metric by name from an InMemoryMetricReader, or None."""
    data = metric_reader.get_metrics_data()
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name:
                    return metric
    return None


def get_metric_value(metric_reader: Any, name: str) -> Optional[float]:
    """Sum of a counter's data points, or None if absent."""
    metric = get_metric(metric_reader, name)
    if metric is None:
        return None
    return sum(dp.value for dp in metric.data.data_points)
