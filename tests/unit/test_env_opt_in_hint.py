"""Vendor credentials without their OTEL_* opt-in are ignored by env-var mode (#255);
the plugin must say so instead of going quiet (#259)."""

from __future__ import annotations

import logging
from unittest.mock import patch

from _helpers import clear_backend_env

from hermes_otel import backends
from hermes_otel import plugin_config as pc
from hermes_otel import settings_report as sr
from hermes_otel.tracer import HermesOTelPlugin


class TestVendorCredentialsWithoutOptIn:
    def test_nothing_set_means_no_hints(self, monkeypatch):
        clear_backend_env(monkeypatch)
        assert backends.vendor_credentials_without_opt_in() == []
        assert backends.env_opt_in_hints() == []

    def test_langfuse_sdk_keys_alone_produce_one_hint(self, monkeypatch):
        clear_backend_env(monkeypatch)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf")
        found = backends.vendor_credentials_without_opt_in()
        assert [(t, p) for t, p, _ in found] == [
            ("langfuse", ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"])
        ]
        (hint,) = backends.env_opt_in_hints()
        assert "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set" in hint
        assert "OTEL_LANGFUSE_ENDPOINT" in hint and "backends:" in hint
        assert hint.endswith("not exporting.")

    def test_half_a_credential_set_is_not_reported(self, monkeypatch):
        clear_backend_env(monkeypatch)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf")  # no secret
        monkeypatch.setenv("WANDB_API_KEY", "k")  # no routing
        assert backends.env_opt_in_hints() == []

    def test_opt_in_silences_the_hint(self, monkeypatch):
        clear_backend_env(monkeypatch)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf")
        monkeypatch.setenv("OTEL_LANGFUSE_ENDPOINT", "http://localhost:3000")
        assert backends.env_opt_in_hints() == []

    def test_weave_and_honeycomb_sets(self, monkeypatch):
        clear_backend_env(monkeypatch)
        monkeypatch.setenv("WANDB_API_KEY", "k")
        monkeypatch.setenv("DEFAULT_WANDB_ENTITY", "team")
        monkeypatch.setenv("WANDB_PROJECT", "proj")
        monkeypatch.setenv("HONEYCOMB_API_KEY", "hc")
        found = {t: p for t, p, _ in backends.vendor_credentials_without_opt_in()}
        assert found == {
            "weave": ["WANDB_API_KEY", "DEFAULT_WANDB_ENTITY", "WANDB_PROJECT"],
            "honeycomb": ["HONEYCOMB_API_KEY"],
        }
        hints = backends.env_opt_in_hints()
        assert any("OTEL_WEAVE_API_KEY" in h for h in hints)
        assert any("HONEYCOMB_API_KEY is set" in h and "OTEL_HONEYCOMB_API_KEY" in h for h in hints)
        # the resolver still ignores them
        assert backends.resolve_from_env() is None


class TestTracerLogsTheHint:
    def test_env_mode_logs_one_info_line_per_ignored_vendor(self, monkeypatch, caplog):
        clear_backend_env(monkeypatch)
        monkeypatch.setenv("HONEYCOMB_API_KEY", "hc")
        plugin = HermesOTelPlugin()
        with (
            patch.object(plugin, "_init_otlp_pipeline", return_value=True) as mock_otlp,
            caplog.at_level(logging.INFO, logger="hermes_otel"),
        ):
            assert plugin.init() is True
            assert mock_otlp.call_args[0][0] == []  # live-only, nothing exported
        lines = [r.getMessage() for r in caplog.records if "not exporting" in r.getMessage()]
        assert len(lines) == 1
        assert "HONEYCOMB_API_KEY is set" in lines[0] and lines[0].startswith("[hermes-otel] ")

    def test_no_line_when_nothing_is_ignored(self, monkeypatch, caplog):
        clear_backend_env(monkeypatch)
        plugin = HermesOTelPlugin()
        with (
            patch.object(plugin, "_init_otlp_pipeline", return_value=True),
            caplog.at_level(logging.INFO, logger="hermes_otel"),
        ):
            plugin.init()
        assert not [r for r in caplog.records if "not exporting" in r.getMessage()]


def _isolated_home(monkeypatch, tmp_path):
    """Point every config lookup at tmp_path so the developer's own files stay out."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv(pc.CONFIG_PATH_ENV, raising=False)
    monkeypatch.setattr(pc, "DURABLE_CONFIG_PATH", tmp_path / "hermes_otel.yaml")
    monkeypatch.setattr(
        pc, "DEFAULT_CONFIG_PATH", tmp_path / "plugins" / "hermes_otel" / "config.yaml"
    )


class TestSettingsReportCarriesTheNotices:
    def test_env_mode_report_lists_the_hints(self, monkeypatch, tmp_path):
        clear_backend_env(monkeypatch)
        _isolated_home(monkeypatch, tmp_path)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf")
        report = sr.build_settings_report()
        assert len(report["env_notices"]) == 1
        assert "OTEL_LANGFUSE_ENDPOINT" in report["env_notices"][0]

    def test_a_backends_list_bypasses_the_notices(self, monkeypatch, tmp_path):
        clear_backend_env(monkeypatch)
        _isolated_home(monkeypatch, tmp_path)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf")
        (tmp_path / "hermes_otel.yaml").write_text(
            "backends:\n  - type: phoenix\n    endpoint: http://localhost:6006/v1/traces\n"
        )
        assert sr.build_settings_report()["env_notices"] == []
