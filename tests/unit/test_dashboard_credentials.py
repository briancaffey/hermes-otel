"""The dashboard resolves an entry's settings the way the exporter does (#614):
``${VAR}`` in the entry is expanded, and every credential falls back to the
vendor variables ``backend_specs`` lists for its type."""

from __future__ import annotations

import logging

import pytest
from _helpers import clear_backend_env

from hermes_otel import backends as exporter
from hermes_otel.backend_config import BackendConfig
from hermes_otel.backend_specs import spec_for
from hermes_otel.dashboard import backends as dash
from hermes_otel.dashboard.backends import greptime, openobserve, phoenix, victoria
from hermes_otel.dashboard.backends.base import resolve_env_or_literal


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    clear_backend_env(monkeypatch)
    for name in ("OO_PW", "HERMES_OTEL_PROJECT_NAME", "OTEL_PROJECT_NAME", "HO_UNSET_614"):
        monkeypatch.delenv(name, raising=False)
    dash.clear_caches()
    yield
    dash.clear_caches()


class TestEnvRefExpansion:
    def test_expand_entry_expands_scalars_mappings_and_lists(self, monkeypatch):
        monkeypatch.setenv("OO_PW", "s3cret")
        entry = {
            "type": "openobserve",
            "password": "${OO_PW}",
            "headers": {"X-Key": "k-${OO_PW}"},
            "tags": ["${OO_PW}", 3],
            "query_port": 5080,
        }
        out = dash.expand_entry(entry)
        assert out["password"] == "s3cret"
        assert out["headers"] == {"X-Key": "k-s3cret"}
        assert out["tags"] == ["s3cret", 3]
        assert out["query_port"] == 5080
        # The entry as written is left alone (``/status`` reports it).
        assert entry["password"] == "${OO_PW}"

    def test_unset_variable_stays_literal_and_is_warned_by_name_only(self, caplog):
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            out = dash.expand_entry({"type": "jaeger", "api_key": "${HO_UNSET_614}"})
        assert out["api_key"] == "${HO_UNSET_614}"

    def test_the_adapter_is_built_from_the_expanded_entry(self, monkeypatch):
        monkeypatch.setenv("OO_PW", "s3cret")
        entry = {
            "type": "openobserve",
            "endpoint": "http://localhost:5080/api/default/v1/traces",
            "user": "root@example.com",
            "password": "${OO_PW}",
        }
        adapter = dash._instantiate(entry)
        assert isinstance(adapter, openobserve.OpenObserveAdapter)
        assert adapter.password == "s3cret"

    def test_the_expanded_secret_is_never_logged(self, monkeypatch, caplog):
        monkeypatch.setenv("OO_PW", "s3cret")
        with caplog.at_level(logging.DEBUG):
            dash._instantiate(
                {
                    "type": "openobserve",
                    "endpoint": "http://localhost:5080/api/default/v1/traces",
                    "user": "u",
                    "password": "${OO_PW}",
                }
            )
        assert "s3cret" not in caplog.text


class TestFallbackEnvs:
    def test_openobserve_reads_the_exporters_fallbacks(self, monkeypatch):
        monkeypatch.setenv("OTEL_OPENOBSERVE_USER", "root@example.com")
        monkeypatch.setenv("OTEL_OPENOBSERVE_PASSWORD", "pw")
        a = openobserve.OpenObserveAdapter(
            {"type": "openobserve", "endpoint": "http://localhost:5080/api/default/v1/traces"}
        )
        assert (a.user, a.password) == ("root@example.com", "pw")

    def test_victoria_reads_the_exporters_fallbacks(self, monkeypatch):
        monkeypatch.setenv("OTEL_VICTORIA_USER", "vu")
        monkeypatch.setenv("OTEL_VICTORIA_PASSWORD", "vp")
        a = victoria.VictoriaAdapter({"type": "victoria", "endpoint": "http://localhost:10428"})
        assert (a.user, a.password) == ("vu", "vp")

    def test_inline_and_named_env_still_win_over_the_fallback(self, monkeypatch):
        monkeypatch.setenv("OTEL_OPENOBSERVE_USER", "fallback")
        monkeypatch.setenv("MY_USER", "named")
        cfg = {"type": "openobserve", "user_env": "MY_USER"}
        assert resolve_env_or_literal(cfg, "user", "user_env") == "named"
        cfg["user"] = "inline"
        assert resolve_env_or_literal(cfg, "user", "user_env") == "inline"

    def test_an_alias_type_gets_its_specs_fallbacks(self, monkeypatch):
        monkeypatch.setenv("OPENOBSERVE_USER", "alias-user")
        assert resolve_env_or_literal({"type": "openobserver"}, "user", "user_env") == "alias-user"

    def test_unknown_type_or_field_has_no_fallback(self, monkeypatch):
        monkeypatch.setenv("OTEL_OPENOBSERVE_USER", "x")
        assert resolve_env_or_literal({"type": "nope"}, "user", "user_env") is None
        assert resolve_env_or_literal({}, "user", "user_env") is None

    @pytest.mark.parametrize("backend_type", ["openobserve", "victoria", "greptime", "langfuse"])
    def test_dashboard_and_exporter_pick_the_same_credential(self, monkeypatch, backend_type):
        spec = spec_for(backend_type)
        for cred in spec.credentials:
            monkeypatch.setenv(cred.env[-1], f"{cred.field}-value")
            bc = BackendConfig(type=backend_type)
            assert resolve_env_or_literal(
                {"type": backend_type}, cred.field, f"{cred.field}_env"
            ) == exporter._secret(bc, spec, cred.field)


class TestGreptimeDatabase:
    def _adapter(self, **extra):
        return greptime.GreptimeAdapter(
            {"type": "greptime", "endpoint": "http://localhost:4000", **extra}
        )

    def test_default_is_public(self):
        assert self._adapter().db == "public"

    def test_db_env_and_the_fallback_variable(self, monkeypatch):
        monkeypatch.setenv("OTEL_GREPTIME_DB", "fallback_db")
        assert self._adapter().db == "fallback_db"
        monkeypatch.setenv("MY_DB", "named_db")
        assert self._adapter(db_env="MY_DB").db == "named_db"
        assert self._adapter(db="inline_db", db_env="MY_DB").db == "inline_db"
        assert "db=inline_db" in self._adapter(db="inline_db").sql_url


class TestPhoenixProject:
    def _adapter(self, monkeypatch, top, **extra):
        monkeypatch.setattr("hermes_otel.dashboard.backends.top_level_config", lambda: top)
        return phoenix.PhoenixAdapter(
            {"type": "phoenix", "endpoint": "http://localhost:6006", **extra}
        )

    def test_otel_project_name_is_the_last_fallback(self, monkeypatch):
        monkeypatch.setenv("OTEL_PROJECT_NAME", "from-otel")
        assert self._adapter(monkeypatch, {}).project_name == "from-otel"
        assert self._adapter(monkeypatch, {"project_name": "file"}).project_name == "file"

    def test_the_plugin_env_override_wins_over_the_file(self, monkeypatch):
        monkeypatch.setenv("HERMES_OTEL_PROJECT_NAME", "from-plugin-env")
        top = {"project_name": "file"}
        assert self._adapter(monkeypatch, top).project_name == "from-plugin-env"

    def test_the_entry_wins_over_everything(self, monkeypatch):
        monkeypatch.setenv("HERMES_OTEL_PROJECT_NAME", "env")
        monkeypatch.setenv("OTEL_PROJECT_NAME", "otel")
        assert self._adapter(monkeypatch, {}, project_name="entry").project_name == "entry"

    def test_nothing_set_means_no_project(self, monkeypatch):
        assert self._adapter(monkeypatch, {}).project_name is None
