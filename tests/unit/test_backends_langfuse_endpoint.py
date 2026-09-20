"""Langfuse endpoints are completed to the OTLP traces path the exporter needs."""

import pytest

from hermes_otel.backends import _langfuse_traces_url, resolve
from hermes_otel.plugin_config import BackendConfig

FULL = "https://cloud.langfuse.com/api/public/otel/v1/traces"


@pytest.mark.parametrize(
    "given",
    [
        "https://cloud.langfuse.com",
        "https://cloud.langfuse.com/",
        "https://cloud.langfuse.com/api/public/otel",
        "https://cloud.langfuse.com/api/public/otel/",
        FULL,
    ],
)
def test_every_written_form_becomes_the_full_traces_url(given):
    assert _langfuse_traces_url(given) == FULL


def test_local_root_endpoint_no_longer_posts_to_the_site_root():
    rb = resolve(
        BackendConfig(
            type="langfuse", endpoint="http://localhost:3000", public_key="pk", secret_key="sk"
        )
    )
    assert rb.endpoint == "http://localhost:3000/api/public/otel/v1/traces"


def test_reverse_proxy_prefix_is_kept():
    assert _langfuse_traces_url("https://proxy.internal/langfuse") == (
        "https://proxy.internal/langfuse/v1/traces"
    )


def test_base_url_path_still_appends():
    rb = resolve(
        BackendConfig(
            type="langfuse", base_url="http://localhost:3000/", public_key="pk", secret_key="sk"
        )
    )
    assert rb.endpoint == "http://localhost:3000/api/public/otel/v1/traces"
