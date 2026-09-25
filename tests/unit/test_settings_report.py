"""The Settings tab's report: every field with its source, secrets masked,
the environment inventory, and the raw / effective YAML renderings.
"""

from __future__ import annotations

import dataclasses

import pytest

from hermes_otel import plugin_config as pc
from hermes_otel.plugin_config import FIELD_DOCS, FIELD_GROUPS, HermesOtelConfig
from hermes_otel.settings_report import (
    MASK,
    build_settings_report,
    capture_summary,
    effective_yaml,
    env_inventory,
    field_reports,
    is_secret_name,
    redact_yaml_text,
)

YAML = """\
project_name: demo
query_backend: phoenix
preview_max_chars: lots
capture_full_prompts: true
headers:
  x-team: blue
  Authorization: Bearer abc123
backends:
  - type: phoenix
    name: phx
    endpoint: http://localhost:6006/v1/traces
    metrics: false
  - type: langfuse
    endpoint: http://localhost:3000
    public_key: pk-inline
    secret_key: ${LF_SECRET}
  - type: openobserve
    endpoint: http://localhost:5080/api/default/v1/traces
    user: root@example.com
    password_env: OO_PASSWORD
"""


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A HERMES_HOME with a durable config file; env overrides cleared."""
    pytest.importorskip("yaml")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv(pc.CONFIG_PATH_ENV, raising=False)
    for key in pc.field_kinds():
        monkeypatch.delenv(pc._ENV_PREFIX + key.upper(), raising=False)
    monkeypatch.setattr(pc, "DURABLE_CONFIG_PATH", tmp_path / "hermes_otel.yaml")
    monkeypatch.setattr(
        pc, "DEFAULT_CONFIG_PATH", tmp_path / "plugins" / "hermes_otel" / "config.yaml"
    )
    (tmp_path / "hermes_otel.yaml").write_text(YAML, encoding="utf-8")
    monkeypatch.setenv("LF_SECRET", "sk-expanded")
    monkeypatch.setenv("OO_PASSWORD", "pw")
    return tmp_path


class TestGroupsAndDocs:
    def test_every_field_is_grouped_exactly_once(self):
        names = {f.name for f in dataclasses.fields(HermesOtelConfig)}
        grouped = [k for _, keys in FIELD_GROUPS for k in keys]
        assert sorted(grouped) == sorted(names)
        assert len(grouped) == len(set(grouped))

    def test_every_field_has_a_description(self):
        assert set(FIELD_DOCS) == {f.name for f in dataclasses.fields(HermesOtelConfig)}


class TestFieldReports:
    def test_sources_follow_loader_precedence(self, home, monkeypatch):
        monkeypatch.setenv("HERMES_OTEL_PROJECT_NAME", "from-env")
        report = build_settings_report()
        by_key = {f["key"]: f for f in report["fields"]}
        assert by_key["project_name"]["source"] == "env"
        assert by_key["project_name"]["value"] == "from-env"
        assert by_key["project_name"]["file_value"] == "demo"
        assert by_key["project_name"]["env_var"] == "HERMES_OTEL_PROJECT_NAME"
        assert by_key["capture_full_prompts"]["source"] == "file"
        assert by_key["capture_full_prompts"]["value"] is True
        assert by_key["capture_full_prompts"]["changed"] is False  # full is the default
        # content_capture was not written but the legacy key decided it.
        assert by_key["content_capture"]["value"] == "full"
        assert by_key["content_capture"]["source"] == "file"
        assert by_key["content_capture"]["derived_from"] == ["capture_full_prompts"]
        assert by_key["enabled"]["source"] == "default"
        assert by_key["enabled"]["changed"] is False
        assert by_key["backends"]["env_var"] is None  # yaml-only field
        assert report["counts"]["env"] == 1
        assert report["counts"]["file"] >= 3

    def test_content_capture_drives_the_legacy_flags_in_the_report(self, home, monkeypatch):
        monkeypatch.setenv("HERMES_OTEL_CONTENT_CAPTURE", "off")
        by_key = {f["key"]: f for f in build_settings_report()["fields"]}
        assert by_key["content_capture"]["source"] == "env"
        for key in ("capture_previews", "capture_full_prompts", "capture_full_responses"):
            assert by_key[key]["value"] is False, key
            assert by_key[key]["source"] == "env"
            assert by_key[key]["derived_from"] == ["content_capture"]

    def test_invalid_file_and_env_values_are_flagged_not_used(self, home, monkeypatch):
        monkeypatch.setenv("HERMES_OTEL_FLUSH_INTERVAL_MS", "soon")
        by_key = {f["key"]: f for f in build_settings_report()["fields"]}
        assert by_key["preview_max_chars"]["file_invalid"] is True
        assert by_key["preview_max_chars"]["value"] == HermesOtelConfig().preview_max_chars
        assert by_key["preview_max_chars"]["source"] == "default"
        assert by_key["flush_interval_ms"]["env_invalid"] is True
        assert by_key["flush_interval_ms"]["env_raw"] == "soon"
        assert by_key["flush_interval_ms"]["source"] == "default"

    def test_every_field_has_group_kind_and_description(self, home):
        groups = {g for g, _ in FIELD_GROUPS}
        for f in build_settings_report()["fields"]:
            assert f["group"] in groups, f["key"]
            assert f["kind"] in ("bool", "int", "float", "str", "map", "backends")
            assert f["description"]

    def test_unknown_file_keys_are_listed_with_known_notes(self, home):
        unknown = build_settings_report()["config"]["unknown_keys"]
        assert unknown == [{"key": "query_backend", "note": pytest.approx(unknown[0]["note"])}]
        assert "Dashboard" in unknown[0]["note"]


class TestSecrets:
    @pytest.mark.parametrize(
        "name,secret",
        [
            ("secret_key", True),
            ("password", True),
            ("OTEL_UPTRACE_DSN", True),
            ("Authorization", True),
            ("project_name", False),
            ("endpoint", False),
        ],
    )
    def test_secret_names(self, name, secret):
        assert is_secret_name(name) is secret

    def test_headers_map_masks_auth_values(self, home):
        by_key = {f["key"]: f for f in build_settings_report()["fields"]}
        assert by_key["headers"]["value"] == {"x-team": "blue", "Authorization": MASK}
        assert build_settings_report(reveal=True)["fields"]
        revealed = {f["key"]: f for f in build_settings_report(reveal=True)["fields"]}
        assert revealed["headers"]["value"]["Authorization"] == "Bearer abc123"

    def test_backend_credentials_report_source_not_value(self, home, monkeypatch):
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "should-not-apply-to-phoenix")
        backends = {b["name"]: b for b in build_settings_report()["fields"][-1]["value"]}
        assert backends["phx"]["credentials"] == []
        assert backends["phx"]["signals"]["metrics"] == {
            "supported": False,
            "configured": "off",
            "exported": False,
        }
        lf = {c["field"]: c for c in backends["langfuse"]["credentials"]}
        assert lf["public_key"]["source"] == "file (inline)"
        assert lf["public_key"]["value"] == MASK
        assert lf["secret_key"]["source"] == "file, expanded from ${LF_SECRET}"
        assert lf["secret_key"]["value"] == MASK
        oo = {c["field"]: c for c in backends["openobserve"]["credentials"]}
        assert oo["user"]["value"] == "root@example.com"  # not a secret name
        assert oo["password"]["source"] == "env OO_PASSWORD"
        assert oo["password"]["value"] == MASK

    def test_reveal_shows_backend_secret_values(self, home):
        backends = {b["name"]: b for b in build_settings_report(reveal=True)["fields"][-1]["value"]}
        lf = {c["field"]: c for c in backends["langfuse"]["credentials"]}
        assert lf["secret_key"]["value"] == "sk-expanded"

    def test_raw_yaml_is_redacted_unless_revealed(self, home):
        raw = build_settings_report()["config"]["raw"]
        assert "pk-inline" not in raw
        assert f"public_key: {MASK}" in raw
        assert "secret_key: ${LF_SECRET}" in raw  # an env reference is not a secret
        assert f"Authorization: {MASK}" in raw
        assert "password_env: OO_PASSWORD" in raw  # names an env var, not a secret
        assert "x-team: blue" in raw
        assert "pk-inline" in build_settings_report(reveal=True)["config"]["raw"]

    def test_redact_keeps_comments_and_structure(self):
        text = "api_key: abc  # keep me\nname: fine\n  token: xyz\nkey_env: MY_KEY\nauth: Basic dXNlcg==\n"
        assert redact_yaml_text(text) == (
            f"api_key: {MASK}  # keep me\nname: fine\n  token: {MASK}\nkey_env: MY_KEY\nauth: Basic {MASK}\n"
        )


class TestEnvInventory:
    def test_overrides_known_and_other_vars(self, home, monkeypatch):
        monkeypatch.setenv("HERMES_OTEL_CAPTURE_LOGS", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "ls-secret")
        monkeypatch.setenv("OTEL_SOMETHING_NEW", "x")
        env = {e["name"]: e for e in env_inventory()}
        assert env["HERMES_OTEL_CAPTURE_LOGS"] == {
            "name": "HERMES_OTEL_CAPTURE_LOGS",
            "group": "override",
            "description": FIELD_DOCS["capture_logs"],
            "set": True,
            "value": "true",
            "maps_to": "capture_logs",
        }
        assert env["HERMES_OTEL_ENABLED"]["set"] is False
        assert env["HERMES_HOME"]["set"] is True and env["HERMES_HOME"]["group"] == "plugin"
        assert env["LANGSMITH_API_KEY"]["value"] == MASK
        assert env["OTEL_SOMETHING_NEW"]["group"] == "other"
        assert (
            env_inventory(reveal=True)[
                [e["name"] for e in env_inventory()].index("LANGSMITH_API_KEY")
            ]["value"]
            == "ls-secret"
        )

    def test_maps_and_backends_have_no_override_var(self):
        names = {e["maps_to"] for e in env_inventory() if e["group"] == "override"}
        assert "backends" not in names and "headers" not in names


class TestRenderings:
    def test_effective_yaml_names_each_source(self, home, monkeypatch):
        monkeypatch.setenv("HERMES_OTEL_PROJECT_NAME", "from-env")
        report = build_settings_report()
        text = report["effective_yaml"]
        assert "project_name: from-env  # env HERMES_OTEL_PROJECT_NAME" in text
        assert "capture_full_prompts: true  # file" in text
        assert "enabled: true  # default" in text
        assert "- type: phoenix" in text and "name: phx" in text and "metrics: false" in text
        assert "password_env: OO_PASSWORD" in text
        assert "pk-inline" not in text  # masked
        assert str(home / "hermes_otel.yaml") in text

    def test_effective_yaml_without_a_file(self, tmp_path):
        reports, _ = field_reports({})
        text = effective_yaml(reports, None)
        assert "with no config file" in text
        assert "backends" not in text.split("\n\n", 1)[1]  # unset list is omitted

    def test_config_block_reports_path_and_source(self, home):
        cfg = build_settings_report()["config"]
        assert cfg["path"] == str(home / "hermes_otel.yaml")
        assert cfg["path_source"] == "durable"
        assert cfg["exists"] is True and cfg["parse_ok"] is True
        assert cfg["mtime"] is not None

    def test_missing_file_is_reported_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv(pc.CONFIG_PATH_ENV, raising=False)
        monkeypatch.setattr(pc, "DURABLE_CONFIG_PATH", tmp_path / "hermes_otel.yaml")
        monkeypatch.setattr(pc, "DEFAULT_CONFIG_PATH", tmp_path / "nope.yaml")
        report = build_settings_report()
        assert report["config"]["path"] is None
        assert report["config"]["path_source"] == "none"
        assert report["config"]["raw"] is None
        assert report["counts"]["file"] == 0
        assert report["process"]["hermes_home"] == str(tmp_path)

    def test_explicit_env_path_is_reported_even_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv(pc.CONFIG_PATH_ENV, str(tmp_path / "missing.yaml"))
        cfg = build_settings_report()["config"]
        assert cfg["path_source"] == "env" and cfg["exists"] is False


class TestCaptureSummary:
    def test_modes(self):
        assert capture_summary(HermesOtelConfig())["mode"] == "full"
        assert capture_summary(HermesOtelConfig(capture_previews=False))["mode"] == "off"
        preview = capture_summary(
            HermesOtelConfig(capture_full_prompts=False, capture_full_responses=False)
        )
        assert preview["mode"] == "preview" and "1200" in preview["detail"]
        full = capture_summary(HermesOtelConfig(capture_full_responses=False))
        assert full["mode"] == "full" and full["detail"].startswith("full prompts on")
        both = capture_summary(HermesOtelConfig())
        assert "full prompts and full responses" in both["detail"]


CARDS_YAML = """\
metrics_temporality: delta
backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces
  - type: langfuse
    base_url: http://localhost:3000
    public_key: pk
    secret_key: sk
  - type: openobserve
    endpoint: http://localhost:5080/api/default/v1/traces
    user: root@example.com
    password: pw
    metrics_temporality: cumulative
  - type: jaeger
    name: jaeger-docker
    endpoint: http://localhost:4318/v1/traces
  - type: jaeger
    name: jaeger-query
    endpoint: http://localhost:4318/v1/traces
    query_port: 16686
  - type: jaeger
    name: jaeger-proxied
    endpoint: https://jaeger.example.com/v1/traces
    metrics: true
  - type: lgtm
    endpoint: http://localhost:4318/v1/traces
    ui_url: http://localhost:3000/explore
    logs: false
  - type: signoz
    endpoint: http://localhost:4318/v1/traces
  - type: otlp
    name: collector
    endpoint: http://collector:4318/v1/traces
  - type: honeycomb
    api_key: hc
    region: eu
  - type: weave
    api_key: wb
    entity: acme
    project: agents
"""


class TestBackendCards:
    """The per-backend summary the Settings tab renders as cards."""

    @pytest.fixture()
    def cards(self, home):
        (home / "hermes_otel.yaml").write_text(CARDS_YAML, encoding="utf-8")
        report = build_settings_report()
        return {b["name"]: b for b in report["fields"][-1]["value"]}

    def test_type_support_is_separate_from_the_entry_override(self, cards):
        phx = cards["phoenix"]["signals"]
        assert phx["traces"] == {"supported": True, "configured": "auto", "exported": True}
        assert phx["metrics"] == {"supported": False, "configured": "auto", "exported": False}
        assert phx["logs"] == {"supported": False, "configured": "auto", "exported": False}
        oo = cards["openobserve"]["signals"]
        assert oo["metrics"]["supported"] and oo["metrics"]["exported"]
        assert oo["logs"]["supported"] and oo["logs"]["exported"]
        # Forced on where the type does not accept the signal (a collector in front).
        forced = cards["jaeger-proxied"]["signals"]["metrics"]
        assert forced == {"supported": False, "configured": "on", "exported": True}
        # Switched off where the type would accept it.
        off = cards["lgtm"]["signals"]["logs"]
        assert off == {"supported": True, "configured": "off", "exported": False}

    def test_signal_report_matches_the_resolver(self, cards):
        """The card's exported flags are exactly what backends.resolve wires."""
        from hermes_otel import backends as b

        for name, bc in {
            "phoenix": pc.BackendConfig(type="phoenix", endpoint="http://x/v1/traces"),
            "jaeger-proxied": pc.BackendConfig(
                type="jaeger", endpoint="http://x/v1/traces", metrics=True
            ),
            "lgtm": pc.BackendConfig(type="lgtm", endpoint="http://x/v1/traces", logs=False),
        }.items():
            rb = b.resolve(bc)
            sig = cards[name]["signals"]
            assert sig["traces"]["exported"] is rb.supports_traces
            assert sig["metrics"]["exported"] is rb.supports_metrics
            assert sig["logs"]["exported"] is rb.supports_logs

    def test_display_type_and_docs(self, cards):
        assert cards["signoz"]["display_type"] == "SigNoz"
        assert cards["signoz"]["docs_path"] == "/backends/signoz"
        assert cards["collector"]["display_type"] == "OTLP"
        assert cards["weave"]["display_type"] == "W&B Weave"
        assert all(c["known_type"] for c in cards.values())

    def test_ui_link_derived_where_the_ui_shares_the_otlp_origin(self, cards):
        assert cards["phoenix"]["ui"]["url"] == "http://localhost:6006"
        assert cards["phoenix"]["ui"]["source"] == "derived"
        assert cards["langfuse"]["ui"]["url"] == "http://localhost:3000"
        assert cards["openobserve"]["ui"]["url"] == "http://localhost:5080"

    def test_ui_link_never_guesses_a_port(self, cards):
        docker = cards["jaeger-docker"]["ui"]
        assert docker["url"] is None and "ui_url" in docker["note"]
        assert cards["jaeger-query"]["ui"]["url"] == "http://localhost:16686"
        assert cards["jaeger-proxied"]["ui"]["url"] == "https://jaeger.example.com"
        assert cards["signoz"]["ui"]["url"] is None
        assert cards["collector"]["ui"]["url"] is None

    def test_ui_link_from_the_file_wins(self, cards):
        assert cards["lgtm"]["ui"] == {
            "url": "http://localhost:3000/explore",
            "source": "file",
            "note": "ui_url from the config file",
        }

    def test_saas_ui_links(self, cards):
        assert cards["honeycomb"]["ui"]["url"] == "https://ui.eu1.honeycomb.io"
        assert cards["weave"]["ui"]["url"] == "https://wandb.ai/acme/agents/weave"

    def test_temporality_names_the_rule_that_set_it(self, cards):
        assert cards["signoz"]["metrics_temporality"] == {"value": "delta", "source": "type preset"}
        assert cards["openobserve"]["metrics_temporality"] == {
            "value": "cumulative",
            "source": "entry",
        }
        assert cards["collector"]["metrics_temporality"] == {
            "value": "delta",
            "source": "top-level metrics_temporality",
        }

    def test_temporality_default_without_top_level(self, home):
        (home / "hermes_otel.yaml").write_text(
            "backends:\n  - type: lgtm\n    endpoint: http://l:4318/v1/traces\n", encoding="utf-8"
        )
        (card,) = build_settings_report()["fields"][-1]["value"]
        assert card["metrics_temporality"] == {"value": "cumulative", "source": "SDK default"}

    def test_query_fields_and_effective_yaml_round_trip(self, cards, home):
        assert cards["jaeger-query"]["query_fields"] == {"query_port": 16686}
        assert cards["phoenix"]["query_fields"] == {}
        text = build_settings_report()["effective_yaml"]
        assert "query_port: 16686" in text
        assert "ui_url: http://localhost:3000/explore" in text
        assert "metrics_temporality: cumulative" in text
        assert "logs: false" in text
