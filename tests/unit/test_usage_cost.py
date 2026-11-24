"""The two shape defects in the per-call cost path.

Hermes's ``post_api_request`` usage is ``asdict(CanonicalUsage)``, so
``cache_write_by_ttl`` arrives as a tuple — ``()`` when the response named no
cache tier. Core pricing reads that field with ``dict(...)``; the plugin's int
coercion turned it into ``0`` and core raised on the int, so the whole estimate
was lost and the span carried no cost. The same hook carries no API key, and a
gateway's ``/v1/models`` price list answers only to an authenticated request, so
an endpoint that publishes its rates there priced as ``unknown``.
"""

import sys

import pytest
from helpers.config import install_config

from hermes_otel.hooks.usage import _cache_write_dict, _endpoint_api_key


class TestCacheWriteDict:
    def test_empty_tuple_is_no_tier(self):
        assert _cache_write_dict(()) == {}

    def test_real_bucket_is_preserved(self):
        assert _cache_write_dict((("1h", 4096),)) == {"1h": 4096}

    def test_dict_passes_through(self):
        assert _cache_write_dict({"1h": 4096}) == {"1h": 4096}

    @pytest.mark.parametrize("value", [0, None, "x", 3.5])
    def test_not_a_bucket_list_is_no_tier(self, value):
        """to_int on these yields 0, which is what core pricing raised on."""
        assert _cache_write_dict(value) == {}

    def test_malformed_pairs_are_no_tier(self):
        assert _cache_write_dict(("not-a-pair",)) == {}

    @pytest.mark.parametrize(
        "value", [(), (("1h", 4096),), {"1h": 4096}, 0, None, "junk", ("not-a-pair",)]
    )
    def test_result_is_what_core_pricing_reads(self, value):
        """``_cache_write_buckets`` calls ``dict(usage.cache_write_by_ttl)``. The result
        must be dict-shaped for that to work; the int the old code passed was not."""
        assert dict(_cache_write_dict(value)) == _cache_write_dict(value)


class TestEndpointApiKey:
    def test_key_env_resolves_from_the_matching_provider_entry(self, monkeypatch):
        install_config(
            monkeypatch,
            {"bifrost": {"base_url": "https://bifrost.test/v1", "key_env": "BIFROST_VK"}},
            {"BIFROST_VK": "vk-123"},
        )
        assert _endpoint_api_key("https://bifrost.test/v1") == "vk-123"

    def test_api_key_env_spelling(self, monkeypatch):
        install_config(
            monkeypatch,
            {"p": {"base_url": "https://x.test/v1", "api_key_env": "X_KEY"}},
            {"X_KEY": "k-9"},
        )
        assert _endpoint_api_key("https://x.test/v1") == "k-9"

    def test_default_port_matches_the_bare_host(self, monkeypatch):
        install_config(
            monkeypatch,
            {"p": {"base_url": "https://x.test", "key_env": "X_KEY"}},
            {"X_KEY": "k-9"},
        )
        assert _endpoint_api_key("https://x.test:443/v1") == "k-9"

    def test_a_different_origin_is_not_the_entry(self, monkeypatch):
        """http vs https are different trust boundaries, as the core treats them."""
        install_config(
            monkeypatch,
            {"p": {"base_url": "http://x.test/v1", "key_env": "X_KEY"}},
            {"X_KEY": "k-9"},
        )
        assert _endpoint_api_key("https://x.test/v1") == ""

    def test_no_base_url(self, monkeypatch):
        install_config(
            monkeypatch,
            {"p": {"base_url": "https://x.test/v1", "key_env": "X_KEY"}},
            {"X_KEY": "k-9"},
        )
        assert _endpoint_api_key("") == ""

    def test_unmatched_endpoint_is_empty(self, monkeypatch):
        install_config(
            monkeypatch,
            {"p": {"base_url": "https://y.test/v1", "key_env": "Y_KEY"}},
            {"Y_KEY": "k"},
        )
        assert _endpoint_api_key("https://x.test/v1") == ""

    def test_missing_env_var_is_empty(self, monkeypatch):
        install_config(monkeypatch, {"p": {"base_url": "https://x.test/v1", "key_env": "ABSENT"}})
        assert _endpoint_api_key("https://x.test/v1") == ""

    def test_entry_without_a_key_env_is_empty(self, monkeypatch):
        install_config(monkeypatch, {"p": {"base_url": "https://x.test/v1"}})
        assert _endpoint_api_key("https://x.test/v1") == ""

    def test_non_dict_config_is_empty(self, monkeypatch):
        install_config(monkeypatch, ["not", "a", "mapping"])
        assert _endpoint_api_key("https://x.test/v1") == ""

    def test_missing_modules_fail_open(self, monkeypatch):
        """An older Hermes build, and the test suite itself, have neither module."""
        monkeypatch.setitem(sys.modules, "agent.models_dev", None)
        monkeypatch.setitem(sys.modules, "utils", None)
        assert _endpoint_api_key("https://x.test/v1") == ""
