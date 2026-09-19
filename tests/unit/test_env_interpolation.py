"""``${VAR}`` interpolation in config.yaml values (#92)."""

import logging

from hermes_otel import plugin_config
from hermes_otel.plugin_config import _coerce_backends, _coerce_from_yaml, expand_env_refs


class TestExpandEnvRefs:
    def test_expands_set_variable(self, monkeypatch):
        monkeypatch.setenv("HONEYCOMB_API_KEY", "hc-123")
        assert expand_env_refs("${HONEYCOMB_API_KEY}") == "hc-123"
        assert expand_env_refs("Bearer ${HONEYCOMB_API_KEY}") == "Bearer hc-123"

    def test_unset_variable_keeps_literal_and_warns_once(self, monkeypatch, caplog):
        monkeypatch.delenv("HERMES_OTEL_TEST_MISSING", raising=False)
        plugin_config._ENV_REF_WARNED.discard("HERMES_OTEL_TEST_MISSING")
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            assert expand_env_refs("${HERMES_OTEL_TEST_MISSING}") == "${HERMES_OTEL_TEST_MISSING}"
            assert expand_env_refs("${HERMES_OTEL_TEST_MISSING}") == "${HERMES_OTEL_TEST_MISSING}"
        assert sum("HERMES_OTEL_TEST_MISSING" in r.getMessage() for r in caplog.records) == 1

    def test_non_strings_and_plain_strings_pass_through(self):
        assert expand_env_refs(None) is None
        assert expand_env_refs(5) == 5
        assert expand_env_refs("no refs here") == "no refs here"
        # Only the braced form is an interpolation; a bare $VAR is left alone.
        assert expand_env_refs("$HOME/x") == "$HOME/x"


class TestBackendsInterpolation:
    def test_header_values_and_secret_fields(self, monkeypatch):
        monkeypatch.setenv("HONEYCOMB_API_KEY", "hc-123")
        monkeypatch.setenv("PHOENIX_API_KEY", "px-456")
        monkeypatch.setenv("COLLECTOR_HOST", "collector.internal")
        otlp, phoenix = _coerce_backends(
            [
                {
                    "type": "otlp",
                    "endpoint": "https://${COLLECTOR_HOST}/v1/traces",
                    "headers": {"x-honeycomb-team": "${HONEYCOMB_API_KEY}"},
                },
                {
                    "type": "phoenix",
                    "endpoint": "https://phoenix.example/v1/traces",
                    "api_key": "${PHOENIX_API_KEY}",
                },
            ]
        )
        assert otlp.headers == {"x-honeycomb-team": "hc-123"}
        assert otlp.endpoint == "https://collector.internal/v1/traces"
        assert phoenix.api_key == "px-456"

    def test_env_suffix_fields_are_names_not_values(self, monkeypatch):
        # ``api_key_env: MY_VAR`` names a variable; it is not itself interpolated.
        monkeypatch.setenv("MY_VAR", "secret")
        (b,) = _coerce_backends([{"type": "phoenix", "endpoint": "e", "api_key_env": "MY_VAR"}])
        assert b.api_key_env == "MY_VAR"

    def test_global_headers(self, monkeypatch):
        monkeypatch.setenv("SHARED_SECRET", "s3cr3t")
        out = _coerce_from_yaml("headers", {"X-Company-Auth": "${SHARED_SECRET}"})
        assert out == {"X-Company-Auth": "s3cr3t"}
