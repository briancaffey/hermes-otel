"""Unit tests for the ``clickstack`` backend resolver (ClickStack / HyperDX).

No network: these assert the resolved endpoint, the bare ``authorization``
header and the signal flags only. The live path is verified against the
compose stack in docker-compose/clickstack.
"""

import pytest

from hermes_otel import backends
from hermes_otel.backend_config import BackendConfig
from hermes_otel.backend_specs import spec_for

EP = "http://localhost:4408"


class TestClickstackBackendType:
    def test_base_endpoint_gets_traces_suffix_and_bare_key(self):
        rb = backends.resolve(BackendConfig(type="clickstack", endpoint=EP, api_key="k1"))
        assert rb.type == "clickstack"
        assert rb.display_name == "ClickStack"
        assert rb.endpoint == "http://localhost:4408/v1/traces"
        assert rb.headers == {
            "authorization": "k1"
        }  # no Bearer: the Collector compares the raw value
        assert rb.supports_traces is True
        assert rb.supports_metrics is True
        assert rb.supports_logs is True

    @pytest.mark.parametrize(
        "suffix", ["/v1/traces", "/v1/metrics", "/v1/logs", "/v1/traces/", "/"]
    )
    def test_signal_suffixes_are_rewritten_to_traces(self, suffix):
        rb = backends.resolve(BackendConfig(type="clickstack", endpoint=EP + suffix, api_key="k"))
        assert rb.endpoint == "http://localhost:4408/v1/traces"

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="endpoint"):
            backends.resolve(BackendConfig(type="clickstack", api_key="k"))

    def test_missing_key_raises_so_the_entry_is_skipped(self):
        # Every batch would be a 401; better to skip the entry with the hint.
        with pytest.raises(ValueError, match="api_key"):
            backends.resolve(BackendConfig(type="clickstack", endpoint=EP))

    def test_key_from_plugin_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_CLICKSTACK_API_KEY", "envkey")
        rb = backends.resolve(BackendConfig(type="clickstack", endpoint=EP))
        assert rb.headers == {"authorization": "envkey"}

    def test_key_from_vendor_env(self, monkeypatch):
        monkeypatch.setenv("HYPERDX_API_KEY", "vendorkey")
        rb = backends.resolve(BackendConfig(type="clickstack", endpoint=EP))
        assert rb.headers == {"authorization": "vendorkey"}

    def test_api_key_env_field_selects_var(self, monkeypatch):
        monkeypatch.setenv("MY_HDX", "custom")
        rb = backends.resolve(BackendConfig(type="clickstack", endpoint=EP, api_key_env="MY_HDX"))
        assert rb.headers == {"authorization": "custom"}

    def test_inline_key_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_CLICKSTACK_API_KEY", "envkey")
        rb = backends.resolve(BackendConfig(type="clickstack", endpoint=EP, api_key="inline"))
        assert rb.headers == {"authorization": "inline"}

    def test_user_headers_merge_and_may_override(self):
        rb = backends.resolve(
            BackendConfig(type="clickstack", endpoint=EP, api_key="k", headers={"X-Custom": "v"})
        )
        assert rb.headers == {"authorization": "k", "X-Custom": "v"}
        rb = backends.resolve(
            BackendConfig(
                type="clickstack", endpoint=EP, api_key="k", headers={"authorization": "other"}
            )
        )
        assert rb.headers == {"authorization": "other"}

    def test_signal_overrides(self):
        rb = backends.resolve(
            BackendConfig(type="clickstack", endpoint=EP, api_key="k", metrics=False, logs=False)
        )
        assert rb.supports_metrics is False
        assert rb.supports_logs is False

    def test_no_temporality_preset_and_root_usage_kept(self):
        assert spec_for("clickstack").temporality is None
        rb = backends.resolve(BackendConfig(type="clickstack", endpoint=EP, api_key="k"))
        assert rb.root_usage is True

    def test_signal_support_table(self):
        assert backends.signal_support("clickstack") == {
            "traces": True,
            "metrics": True,
            "logs": True,
        }

    def test_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_CLICKSTACK_ENDPOINT", "http://hdx:4318")
        rb = backends.resolve(BackendConfig(type="clickstack", api_key="k"))
        assert rb.endpoint == "http://hdx:4318/v1/traces"


class TestClickstackEnvMode:
    def test_nothing_set_selects_nothing(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        assert backends.resolve_from_env() is None

    def test_endpoint_and_key_select_clickstack(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_CLICKSTACK_ENDPOINT", EP)
        monkeypatch.setenv("OTEL_CLICKSTACK_API_KEY", "k")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "clickstack"
        assert rb.headers == {"authorization": "k"}

    def test_endpoint_without_key_selects_nothing(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_CLICKSTACK_ENDPOINT", EP)
        assert backends.resolve_from_env() is None

    def test_vendor_key_alone_is_not_an_opt_in(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("HYPERDX_API_KEY", "vendor")
        assert backends.resolve_from_env() is None
        hints = backends.env_opt_in_hints()
        assert any("HYPERDX_API_KEY" in h and "OTEL_CLICKSTACK_ENDPOINT" in h for h in hints)

    def test_clickstack_outranks_greptime_in_env_priority(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)
        monkeypatch.setenv("OTEL_GREPTIME_ENDPOINT", "http://g:4000")
        monkeypatch.setenv("OTEL_CLICKSTACK_ENDPOINT", EP)
        monkeypatch.setenv("OTEL_CLICKSTACK_API_KEY", "k")
        rb = backends.resolve_from_env()
        assert rb is not None and rb.type == "clickstack"
