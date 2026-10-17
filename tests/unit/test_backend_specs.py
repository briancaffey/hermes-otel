"""The backend manifest (#402): the one per-type table, and the pieces that
reference a type (resolvers, adapters, env vars, docs) agree with it."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from hermes_otel import backend_specs as specs
from hermes_otel import backends, mcp_config, settings_report
from hermes_otel.dashboard import backends as dash
from hermes_otel.plugin_config import BackendConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "website" / "docs" / "backends"


class TestManifestShape:
    def test_types_and_aliases_are_unique_and_lowercase(self):
        names = [s.type for s in specs.SPECS] + [a for s in specs.SPECS for a in s.aliases]
        assert len(names) == len(set(names))
        assert all(n == n.lower() for n in names)
        assert specs.spec_for("HyperDX") is specs.BY_TYPE["clickstack"]
        assert specs.canonical_type("qryn") == "gigapipe" and specs.canonical_type("nope") == "nope"

    def test_every_bespoke_resolver_is_a_canonical_type(self):
        # The other types resolve from the spec alone (``_resolve_plain``), so a
        # plain type must declare where its endpoint comes from.
        assert set(backends._RESOLVERS) < set(specs.BY_TYPE)
        for t in set(specs.BY_TYPE) - set(backends._RESOLVERS):
            spec = specs.BY_TYPE[t]
            assert spec.endpoint_env or spec.default_endpoint or t in ("otlp", "lgtm"), t
        assert backends.KNOWN_TYPES == specs.KNOWN_TYPES

    def test_env_priority_names_only_known_types_once(self):
        assert len(specs.ENV_PRIORITY) == len(set(specs.ENV_PRIORITY))
        assert set(specs.ENV_PRIORITY) <= set(specs.BY_TYPE)
        for t in specs.ENV_PRIORITY:
            spec = specs.BY_TYPE[t]
            # a type without a plugin-namespaced variable could never be selected
            assert spec.env_opt_in or spec.endpoint_env, t

    def test_every_spec_has_a_docs_page(self):
        missing = [s.type for s in specs.SPECS if not (DOCS / f"{s.type}.md").exists()]
        assert missing == []


class TestViews:
    def test_settings_helpers_read_the_manifest(self):
        """``display_name`` / ``signal_support`` (the Settings tab's two lookups)
        answer from the spec, for aliases too."""
        for spec in specs.SPECS:
            assert backends.signal_support(spec.type) == {
                "traces": True,
                "metrics": spec.metrics,
                "logs": spec.logs,
            }
            assert backends.display_name(spec.type) == spec.display_name
            for alias in spec.aliases:
                assert backends.signal_support(alias) == backends.signal_support(spec.type)
                assert backends.display_name(alias) == spec.display_name

    def test_settings_tables_are_views(self):
        for spec in specs.SPECS:
            want = {c.field: c.env for c in spec.credentials}
            assert settings_report._TYPE_CREDENTIALS.get(spec.type, {}) == want, spec.type
        inventory = {name for name, _g, _d in settings_report.KNOWN_ENV_VARS}
        for spec in specs.SPECS:
            for name, _d in spec.env_vars:
                assert name in inventory, name
            for c in spec.credentials:
                assert set(c.env) <= inventory, (spec.type, c.field)
            assert set(spec.env_opt_in) <= inventory, spec.type
            assert all(set(g) <= inventory for g in spec.vendor_credentials), spec.type
            endpoint_vars = {
                spec.endpoint_env,
                *spec.endpoint_env_fallbacks,
                spec.metrics_endpoint_env,
                spec.logs_endpoint_env,
            } - {None}
            assert endpoint_vars <= inventory, spec.type

    def test_auth_header_names_a_declared_credential(self):
        for spec in specs.SPECS:
            if spec.auth is None:
                continue
            fields = {c.field for c in spec.credentials}
            want = {"user", "password"} if spec.auth.scheme == "Basic" else {spec.auth.field}
            assert want <= fields, spec.type

    def test_ui_facts_match_the_ui_rule(self):
        """The Settings card derives a link from ``ui_*`` with no type literal
        (#504), so each fact must sit on a type whose rule reads it."""
        for spec in specs.SPECS:
            if spec.ui_by_region:
                assert spec.ui_rule == "fixed" and "us" in spec.ui_by_region, spec.type
            if spec.ui_project_page is not None:
                assert spec.ui_rule == "fixed" and spec.ui_url, spec.type
            if spec.ui_rule == "fixed":
                assert spec.ui_by_region or spec.ui_url, spec.type
            if spec.ui_on_query_port:
                assert spec.ui_rule == "proxied", spec.type
            if spec.ui_url:
                assert spec.ui_rule in ("fixed", "origin"), spec.type
            for key in spec.required_resource_attributes:
                # the resolver can supply them from a credential field
                assert key.split(".")[-1] in {c.field for c in spec.credentials}, (spec.type, key)

    def test_mcp_query_api_names_a_server_backend_type(self):
        for spec in specs.SPECS:
            if spec.query_api is not None:
                assert spec.query_api.server in ("jaeger", "tempo"), spec.type
                if not spec.query_api.unverified:
                    assert spec.default_query_port is not None, spec.type
        target = mcp_config.query_target({"type": "lgtm", "endpoint": "http://l:4318/v1/traces"})
        assert (target.server_backend_type, target.backend_url) == ("tempo", "http://l:3200")

    def test_adapter_modules_import_and_handle_their_types(self):
        for spec in specs.SPECS:
            if spec.adapter_module is None:
                continue
            mod = importlib.import_module(f"hermes_otel.dashboard.backends.{spec.adapter_module}")
            cls = dash.find_adapter_class(spec.type)
            assert cls is not None and cls.__module__ == mod.__name__, spec.type
            for alias in spec.aliases:
                assert dash.find_adapter_class(alias) is cls, alias
        registered = {cls.__module__.rsplit(".", 1)[1] for cls in dash.adapters()}
        assert registered == {s.adapter_module for s in specs.SPECS if s.adapter_module}


class TestEnvVarsAreDeclared:
    def test_every_env_var_the_resolvers_read_is_in_a_spec(self):
        pattern = re.compile(r'os\.(?:environ\.get|getenv)\(\s*"([A-Z][A-Z0-9_]+)"')
        read = set(pattern.findall((REPO_ROOT / "hermes_otel" / "backends.py").read_text()))
        declared = {name for s in specs.SPECS for name, _d in s.env_vars}
        plugin = {name for name, group, _d in settings_report.KNOWN_ENV_VARS if group != "backend"}
        assert sorted(read - declared - plugin) == []


def test_query_keys_survive_the_loader_and_unknown_keys_warn(caplog):
    from hermes_otel.plugin_config import coerce_backends

    (bc,) = coerce_backends(
        [{"type": "lgtm", "endpoint": "http://l:4318/v1/traces", "prometheus_url": "http://l:9090"}]
    )
    assert dict(bc.extra) == {"prometheus_url": "http://l:9090"}
    import logging

    with caplog.at_level(logging.WARNING, logger="hermes_otel"):
        (bc2,) = coerce_backends([{"type": "phoenix", "endpoint": "http://p:6006", "apikey": "x"}])
    assert bc2.extra == ()
    assert any("unknown key 'apikey'" in r.getMessage() for r in caplog.records)


def test_alias_entries_resolve_as_their_canonical_type():
    rb = backends.resolve(
        BackendConfig(type="hyperdx", endpoint="http://c:4318/v1/traces", api_key="k-test")
    )
    assert rb.type == "clickstack" and rb.display_name == "ClickStack"
    with pytest.raises(ValueError, match="unknown backend type"):
        backends.resolve(BackendConfig(type="nope"))


def test_traces_url_normalises_every_spelling():
    for given in (
        "http://h:4318",
        "http://h:4318/",
        "http://h:4318/v1/traces",
        "http://h:4318/v1/logs",
    ):
        assert backends.traces_url(given) == "http://h:4318/v1/traces"
