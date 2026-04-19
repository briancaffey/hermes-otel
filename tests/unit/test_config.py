"""Tests for plugin_config.py — HermesOtelConfig loader precedence."""

import sys
from pathlib import Path

import pytest

from hermes_otel.plugin_config import (
    BackendConfig,
    HermesOtelConfig,
    load_config,
)


# ── Env-var hygiene ─────────────────────────────────────────────────────────

_ENV_VARS = [
    "HERMES_OTEL_ENABLED",
    "HERMES_OTEL_SAMPLE_RATE",
    "HERMES_OTEL_ROOT_SPAN_TTL_MS",
    "HERMES_OTEL_FLUSH_INTERVAL_MS",
    "HERMES_OTEL_PREVIEW_MAX_CHARS",
    "HERMES_OTEL_CAPTURE_PREVIEWS",
    "HERMES_OTEL_PROJECT_NAME",
    "HERMES_OTEL_SPAN_BATCH_MAX_QUEUE_SIZE",
    "HERMES_OTEL_SPAN_BATCH_SCHEDULE_DELAY_MS",
    "HERMES_OTEL_SPAN_BATCH_MAX_EXPORT_BATCH_SIZE",
    "HERMES_OTEL_SPAN_BATCH_EXPORT_TIMEOUT_MS",
    "HERMES_OTEL_FORCE_FLUSH_ON_SESSION_END",
]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class TestDefaults:
    def test_defaults_when_nothing_set(self, tmp_path):
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg == HermesOtelConfig()
        assert cfg.enabled is True
        assert cfg.sample_rate is None
        assert cfg.root_span_ttl_ms == 600_000
        assert cfg.flush_interval_ms == 60_000
        assert cfg.preview_max_chars == 1200
        assert cfg.capture_previews is True
        assert cfg.headers is None
        assert cfg.global_tags is None
        assert cfg.resource_attributes is None
        assert cfg.project_name is None

    def test_dataclass_is_frozen(self):
        cfg = HermesOtelConfig()
        with pytest.raises((AttributeError, TypeError)):
            cfg.enabled = False  # type: ignore[misc]


class TestEnvOverrides:
    def test_env_sample_rate(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_SAMPLE_RATE", "0.25")
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.sample_rate == 0.25

    def test_env_sample_rate_zero(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_SAMPLE_RATE", "0")
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.sample_rate == 0.0

    def test_env_enabled_false(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_ENABLED", "false")
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.enabled is False

    def test_env_enabled_accepts_truthy_variants(self, monkeypatch, tmp_path):
        for val in ("1", "true", "True", "YES", "on"):
            monkeypatch.setenv("HERMES_OTEL_ENABLED", val)
            cfg = load_config(path=tmp_path / "nonexistent.yaml")
            assert cfg.enabled is True, val

    def test_env_capture_previews_false(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_CAPTURE_PREVIEWS", "false")
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.capture_previews is False

    def test_env_ttl(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_ROOT_SPAN_TTL_MS", "30000")
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.root_span_ttl_ms == 30_000

    def test_env_flush_interval(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_FLUSH_INTERVAL_MS", "5000")
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.flush_interval_ms == 5_000

    def test_env_preview_max_chars(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_PREVIEW_MAX_CHARS", "500")
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.preview_max_chars == 500

    def test_env_project_name(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_PROJECT_NAME", "my-project")
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.project_name == "my-project"

    def test_env_bad_int_ignored(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_ROOT_SPAN_TTL_MS", "not-a-number")
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        # Bad value → falls back to default.
        assert cfg.root_span_ttl_ms == 600_000


class TestBatchProcessorTunables:
    """Phase 2: BatchSpanProcessor knobs."""

    def test_defaults(self, tmp_path):
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert cfg.span_batch_max_queue_size == 2048
        assert cfg.span_batch_schedule_delay_ms == 1000
        assert cfg.span_batch_max_export_batch_size == 512
        assert cfg.span_batch_export_timeout_ms == 30_000
        assert cfg.force_flush_on_session_end is True

    def test_env_override_queue_size(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_SPAN_BATCH_MAX_QUEUE_SIZE", "8192")
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert cfg.span_batch_max_queue_size == 8192

    def test_env_override_schedule_delay(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_SPAN_BATCH_SCHEDULE_DELAY_MS", "250")
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert cfg.span_batch_schedule_delay_ms == 250

    def test_env_override_batch_size(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_SPAN_BATCH_MAX_EXPORT_BATCH_SIZE", "100")
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert cfg.span_batch_max_export_batch_size == 100

    def test_env_override_timeout(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_SPAN_BATCH_EXPORT_TIMEOUT_MS", "5000")
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert cfg.span_batch_export_timeout_ms == 5000

    def test_env_override_force_flush_on_session_end(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_FORCE_FLUSH_ON_SESSION_END", "false")
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert cfg.force_flush_on_session_end is False

    def test_yaml_loads_batch_tunables(self, tmp_path):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        path.write_text(
            "span_batch_max_queue_size: 1024\n"
            "span_batch_schedule_delay_ms: 100\n"
            "span_batch_max_export_batch_size: 256\n"
            "span_batch_export_timeout_ms: 10000\n"
            "force_flush_on_session_end: false\n"
        )
        cfg = load_config(path=path)
        assert cfg.span_batch_max_queue_size == 1024
        assert cfg.span_batch_schedule_delay_ms == 100
        assert cfg.span_batch_max_export_batch_size == 256
        assert cfg.span_batch_export_timeout_ms == 10_000
        assert cfg.force_flush_on_session_end is False


def _has_yaml() -> bool:
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


class TestYaml:
    """YAML tests are skipped when pyyaml isn't available."""

    def test_yaml_values_loaded(self, tmp_path):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        path.write_text(
            "sample_rate: 0.1\n"
            "root_span_ttl_ms: 120000\n"
            "preview_max_chars: 400\n"
            "capture_previews: false\n"
            "project_name: yaml-project\n"
            "resource_attributes:\n"
            "  deployment: prod\n"
            "  region: us-east-1\n"
            "global_tags:\n"
            "  team: platform\n"
            "headers:\n"
            "  X-Auth: secret-value\n"
        )
        cfg = load_config(path=path)
        assert cfg.sample_rate == 0.1
        assert cfg.root_span_ttl_ms == 120_000
        assert cfg.preview_max_chars == 400
        assert cfg.capture_previews is False
        assert cfg.project_name == "yaml-project"
        assert cfg.resource_attributes == {"deployment": "prod", "region": "us-east-1"}
        assert cfg.global_tags == {"team": "platform"}
        assert cfg.headers == {"X-Auth": "secret-value"}

    def test_env_overrides_yaml(self, tmp_path, monkeypatch):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        path.write_text("sample_rate: 0.1\npreview_max_chars: 400\n")
        monkeypatch.setenv("HERMES_OTEL_SAMPLE_RATE", "0.9")
        cfg = load_config(path=path)
        assert cfg.sample_rate == 0.9
        # yaml-only field preserved when env doesn't override
        assert cfg.preview_max_chars == 400

    def test_yaml_without_file_uses_defaults(self, tmp_path):
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert cfg == HermesOtelConfig()

    def test_malformed_yaml_warns_and_uses_defaults(self, tmp_path, capsys):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        # Unterminated mapping → yaml parse error
        path.write_text("enabled: [broken\nsample_rate: 0.5")
        cfg = load_config(path=path)
        captured = capsys.readouterr()
        assert "[hermes-otel]" in captured.out
        assert cfg == HermesOtelConfig()

    def test_non_mapping_yaml_warns(self, tmp_path, capsys):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        path.write_text("- item1\n- item2\n")
        cfg = load_config(path=path)
        captured = capsys.readouterr()
        assert "[hermes-otel]" in captured.out
        assert cfg == HermesOtelConfig()

    def test_unknown_keys_ignored(self, tmp_path):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        path.write_text("wobble: 42\nsample_rate: 0.5\n")
        cfg = load_config(path=path)
        assert cfg.sample_rate == 0.5


class TestCaptureConversationHistory:
    def test_defaults_off(self, tmp_path):
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert cfg.capture_conversation_history is False
        assert cfg.conversation_history_max_chars == 20_000

    def test_env_toggle(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_CAPTURE_CONVERSATION_HISTORY", "true")
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert cfg.capture_conversation_history is True

    def test_env_max_chars(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_OTEL_CONVERSATION_HISTORY_MAX_CHARS", "5000")
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert cfg.conversation_history_max_chars == 5000

    def test_yaml_values(self, tmp_path):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        path.write_text(
            "capture_conversation_history: true\n"
            "conversation_history_max_chars: 4096\n"
        )
        cfg = load_config(path=path)
        assert cfg.capture_conversation_history is True
        assert cfg.conversation_history_max_chars == 4096


class TestBackendsYaml:
    """Yaml ``backends:`` list parses into a tuple of BackendConfig."""

    def test_default_is_none(self, tmp_path):
        cfg = load_config(path=tmp_path / "missing.yaml")
        assert cfg.backends is None

    def test_loads_multiple_backends(self, tmp_path):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        path.write_text(
            "backends:\n"
            "  - type: phoenix\n"
            "    endpoint: http://localhost:6006/v1/traces\n"
            "  - type: jaeger\n"
            "    endpoint: http://localhost:4318/v1/traces\n"
            "  - type: signoz\n"
            "    endpoint: http://localhost:4328/v1/traces\n"
            "    ingestion_key_env: OTEL_SIGNOZ_INGESTION_KEY\n"
        )
        cfg = load_config(path=path)
        assert cfg.backends is not None
        assert len(cfg.backends) == 3
        assert cfg.backends[0].type == "phoenix"
        assert cfg.backends[0].endpoint == "http://localhost:6006/v1/traces"
        assert cfg.backends[1].type == "jaeger"
        assert cfg.backends[2].type == "signoz"
        assert cfg.backends[2].ingestion_key_env == "OTEL_SIGNOZ_INGESTION_KEY"

    def test_loads_langfuse_credentials(self, tmp_path):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        path.write_text(
            "backends:\n"
            "  - type: langfuse\n"
            "    public_key_env: LANGFUSE_PUBLIC_KEY\n"
            "    secret_key_env: LANGFUSE_SECRET_KEY\n"
            "    base_url: https://cloud.langfuse.com\n"
        )
        cfg = load_config(path=path)
        assert cfg.backends is not None
        b = cfg.backends[0]
        assert b.type == "langfuse"
        assert b.public_key_env == "LANGFUSE_PUBLIC_KEY"
        assert b.secret_key_env == "LANGFUSE_SECRET_KEY"
        assert b.base_url == "https://cloud.langfuse.com"

    def test_loads_per_backend_headers(self, tmp_path):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        path.write_text(
            "backends:\n"
            "  - type: otlp\n"
            "    name: my-collector\n"
            "    endpoint: http://collector:4318/v1/traces\n"
            "    headers:\n"
            "      X-Auth: secret\n"
            "      X-Tenant: acme\n"
        )
        cfg = load_config(path=path)
        b = cfg.backends[0]
        assert b.type == "otlp"
        assert b.name == "my-collector"
        assert b.headers == {"X-Auth": "secret", "X-Tenant": "acme"}

    def test_skips_entry_without_type(self, tmp_path, capsys):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        path.write_text(
            "backends:\n"
            "  - endpoint: http://no-type/v1/traces\n"
            "  - type: jaeger\n"
            "    endpoint: http://jaeger/v1/traces\n"
        )
        cfg = load_config(path=path)
        out = capsys.readouterr().out
        assert "missing 'type'" in out
        # Only the jaeger entry survives.
        assert cfg.backends is not None
        assert len(cfg.backends) == 1
        assert cfg.backends[0].type == "jaeger"

    def test_non_list_backends_ignored(self, tmp_path, capsys):
        if not _has_yaml():
            pytest.skip("pyyaml not installed")
        path = tmp_path / "config.yaml"
        path.write_text("backends: not-a-list\n")
        cfg = load_config(path=path)
        captured = capsys.readouterr()
        assert "must be a list" in captured.out
        assert cfg.backends is None


class TestMissingPyYaml:
    def test_missing_pyyaml_silent_fallback(self, tmp_path, monkeypatch, capsys):
        """When pyyaml isn't importable, loading a real yaml file is skipped silently."""
        path = tmp_path / "config.yaml"
        path.write_text("sample_rate: 0.5\n")

        # Hide yaml by stubbing sys.modules
        original_yaml = sys.modules.get("yaml")
        if "yaml" in sys.modules:
            monkeypatch.delitem(sys.modules, "yaml", raising=False)
        monkeypatch.setitem(sys.modules, "yaml", None)

        try:
            cfg = load_config(path=path)
            captured = capsys.readouterr()
            # Must NOT print a warning for missing pyyaml
            assert "pyyaml" not in captured.out.lower()
            assert cfg == HermesOtelConfig()
        finally:
            if original_yaml is not None:
                sys.modules["yaml"] = original_yaml
