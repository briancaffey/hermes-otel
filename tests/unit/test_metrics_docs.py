"""The metrics reference page must list every instrument the tracer creates (#94)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACER = REPO_ROOT / "hermes_otel" / "tracer.py"
DOCS = REPO_ROOT / "website" / "docs"
METRICS_MD = DOCS / "reference" / "metrics.md"

# Names that were documented but never existed; they must not come back.
_PHANTOMS = ("hermes.tokens.", "hermes.api.duration", "hermes.sessions", "hermes.tool.calls")


def _instrument_names() -> set:
    """Every OTLP instrument name the plugin can emit: the instrument table
    plus the host-metrics observables created in ``_create_host_metric_instruments``."""
    from hermes_otel.instruments import _INSTRUMENTS, HOST_METRIC_NAMES

    names = {spec.name for spec in _INSTRUMENTS.values()}
    names.update(HOST_METRIC_NAMES)
    return names


def test_every_instrument_is_documented():
    names = _instrument_names()
    assert len(names) >= 15, names
    text = METRICS_MD.read_text(encoding="utf-8")
    missing = sorted(n for n in names if f"`{n}`" not in text)
    assert missing == [], f"add to website/docs/reference/metrics.md: {missing}"


def test_no_phantom_metric_names_in_docs():
    hits = []
    for md in DOCS.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        for p in _PHANTOMS:
            if p in text:
                hits.append(f"{md.relative_to(REPO_ROOT)}: {p}")
    assert hits == []


def test_host_metric_names_are_what_the_tracer_registers():
    """``HOST_METRIC_NAMES`` is read by the docs tests instead of the source."""
    from unittest.mock import MagicMock

    from hermes_otel.host_bridge import HostMetrics
    from hermes_otel.instruments import HOST_METRIC_NAMES
    from hermes_otel.plugin_config import HermesOtelConfig

    meter = MagicMock()
    HostMetrics(HermesOtelConfig(host_metrics=True, host_metrics_gpu="off"), meter=meter)
    created = {
        call.args[0]
        for method in ("create_observable_gauge", "create_observable_up_down_counter")
        for call in getattr(meter, method).call_args_list
    }
    assert created == set(HOST_METRIC_NAMES)
