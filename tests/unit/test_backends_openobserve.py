"""OpenObserve endpoint resolution: a bare origin is completed to the org's
traces URL (#582); a full URL is kept as given."""

from __future__ import annotations

import pytest
from _helpers import clear_backend_env

from hermes_otel import backends
from hermes_otel.backend_config import BackendConfig, coerce_backends
from hermes_otel.dashboard.backends.openobserve import OpenObserveAdapter


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    clear_backend_env(monkeypatch)
    monkeypatch.delenv("OTEL_OPENOBSERVE_ORG", raising=False)


def _resolve(**kw):
    return backends.resolve(BackendConfig(type="openobserve", user="u", password="p", **kw))


@pytest.mark.parametrize(
    "given", ["http://localhost:5080", "http://localhost:5080/", " http://localhost:5080 "]
)
def test_bare_origin_completes_to_default_org_traces_url(given):
    rb = _resolve(endpoint=given)
    assert rb.endpoint == "http://localhost:5080/api/default/v1/traces"


def test_bare_origin_uses_the_org_field():
    rb = _resolve(endpoint="https://oo.example.com", org="team-a")
    assert rb.endpoint == "https://oo.example.com/api/team-a/v1/traces"


def test_bare_origin_uses_the_org_env_var(monkeypatch):
    monkeypatch.setenv("OTEL_OPENOBSERVE_ORG", "from-env")
    assert _resolve(endpoint="http://oo:5080").endpoint == "http://oo:5080/api/from-env/v1/traces"
    # The entry's field wins over the variable.
    rb = _resolve(endpoint="http://oo:5080", org="field")
    assert rb.endpoint == "http://oo:5080/api/field/v1/traces"


@pytest.mark.parametrize(
    "given",
    [
        "http://localhost:5080/api/default/v1/traces",
        "http://localhost:5080/api/other/v1/traces",
        "https://proxy.example.com/openobserve/api/default/v1/traces",
    ],
)
def test_full_url_is_kept_even_with_an_org(given):
    assert _resolve(endpoint=given, org="ignored").endpoint == given


def test_env_only_bare_origin_is_completed(monkeypatch):
    monkeypatch.setenv("OTEL_OPENOBSERVE_ENDPOINT", "http://localhost:5080")
    monkeypatch.setenv("OTEL_OPENOBSERVE_USER", "u")
    monkeypatch.setenv("OTEL_OPENOBSERVE_PASSWORD", "p")
    rb = backends.resolve_from_env()
    assert rb is not None and rb.type == "openobserve"
    assert rb.endpoint == "http://localhost:5080/api/default/v1/traces"


def test_metrics_and_logs_follow_the_completed_url():
    rb = _resolve(endpoint="http://localhost:5080", org="o")
    assert rb.target("metrics").endpoint == "http://localhost:5080/api/o/v1/metrics"
    assert rb.target("logs").endpoint == "http://localhost:5080/api/o/v1/logs"


def test_org_is_an_exporter_field_from_yaml():
    (bc,) = coerce_backends(
        [{"type": "openobserve", "endpoint": "http://oo:5080", "org": "team-b"}]
    )
    assert bc.org == "team-b" and bc.extra == ()


def test_adapter_org_follows_the_env_var_for_a_bare_origin(monkeypatch):
    monkeypatch.setenv("OTEL_OPENOBSERVE_ORG", "from-env")
    assert OpenObserveAdapter({"endpoint": "http://oo:5080"}).org == "from-env"
    assert OpenObserveAdapter({"endpoint": "http://oo:5080/api/url/v1/traces"}).org == "url"
    assert OpenObserveAdapter({"endpoint": "http://oo:5080", "org": "cfg"}).org == "cfg"
