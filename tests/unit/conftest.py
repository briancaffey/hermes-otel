"""Unit tier: fast, no network. Every test here is marked ``unit`` and the
OTLP exporters are stubbed so a real ``init()`` never opens a socket.
"""

from unittest.mock import MagicMock, patch

import pytest
from helpers.dashboard import client, isolated_backends  # noqa: F401
from helpers.live import store  # noqa: F401
from helpers.tracer import disabled_tracer, mock_tracer  # noqa: F401


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    for item in items:
        if "/tests/unit/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.unit)


@pytest.fixture(autouse=True)
def _stub_otlp_exporters():
    """Real exporters retry against localhost with exponential backoff from a
    daemon thread — the "Connection refused" noise — and were never shut down.
    Tests that need a specific exporter behaviour patch it themselves (nested
    patches win)."""
    from hermes_otel import log_handler, pipeline

    with (
        patch.object(pipeline, "OTLPSpanExporter", new=MagicMock()),
        patch.object(pipeline, "OTLPMetricExporter", new=MagicMock()),
        patch.object(log_handler, "OTLPLogExporter", new=MagicMock()),
    ):
        yield
