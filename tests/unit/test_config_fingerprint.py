"""``HermesOtelConfig.fingerprint`` (#484): stable across spellings that mean
the same thing, different for every effective change, and never built from a
secret's own text."""

from __future__ import annotations

import re
from dataclasses import replace

from hermes_otel.backend_config import BackendConfig
from hermes_otel.plugin_config import (
    SHORT_FINGERPRINT_CHARS,
    HermesOtelConfig,
    fingerprint_source,
)

# Built at runtime so the plugin scanner does not read it as a hardcoded key.
SECRET = "sk-lf-" + "supersecret-" + "0123456789abcdef"
BEARER = "Bearer tok-abcdef-123456789"
PASSWORD = "hunter2-hunter2"


def _langfuse(**over):
    base = dict(
        type="langfuse",
        name="lf",
        endpoint="http://langfuse:3000",
        public_key_env="LF_PUBLIC",
        secret_key=SECRET,
        headers={"x-team": "blue", "Authorization": BEARER},
        extra=(("query_port", 3000), ("ui_url", "http://langfuse:3000")),
    )
    base.update(over)
    return BackendConfig(**base)


def _config(**over):
    base = dict(
        project_name="fp-a",
        headers={"x-a": "1", "x-b": "2"},
        resource_attributes={"deployment.environment": "dev", "team": "obs"},
        log_exclude_loggers=("urllib3", "httpx"),
        backends=(
            BackendConfig(type="phoenix", endpoint="http://phoenix:6006/v1/traces"),
            _langfuse(),
            BackendConfig(
                type="openobserve", endpoint="http://oo:5080", user="root", password=PASSWORD
            ),
        ),
    )
    base.update(over)
    return HermesOtelConfig(**base)


class TestStable:
    def test_same_config_same_fingerprint(self):
        assert _config().fingerprint() == _config().fingerprint()
        fp = _config().fingerprint()
        assert len(fp) == 64 and re.fullmatch(r"[0-9a-f]{64}", fp)
        assert SHORT_FINGERPRINT_CHARS < 64

    def test_mapping_key_order_does_not_count(self):
        a = _config(headers={"x-a": "1", "x-b": "2"}, resource_attributes={"k1": "v", "k2": "w"})
        b = _config(headers={"x-b": "2", "x-a": "1"}, resource_attributes={"k2": "w", "k1": "v"})
        assert a.fingerprint() == b.fingerprint()
        # A backend's header map and its query-key pairs too.
        c = _config(backends=(_langfuse(headers={"Authorization": BEARER, "x-team": "blue"}),))
        d = _config(
            backends=(
                _langfuse(
                    headers={"x-team": "blue", "Authorization": BEARER},
                    extra=(("ui_url", "http://langfuse:3000"), ("query_port", 3000)),
                ),
            )
        )
        assert c.fingerprint() == d.fingerprint()

    def test_tuple_and_list_spellings_are_one(self):
        entries = (BackendConfig(type="phoenix", endpoint="http://p:6006/v1/traces"),)
        a = _config(log_exclude_loggers=("urllib3", "httpx"), backends=entries)
        b = _config(log_exclude_loggers=["urllib3", "httpx"], backends=list(entries))
        assert a.fingerprint() == b.fingerprint()

    def test_deprecated_global_tags_fold_into_resource_attributes(self):
        a = HermesOtelConfig(resource_attributes={"team": "obs"})
        b = HermesOtelConfig(global_tags={"team": "obs"})
        assert a.fingerprint() == b.fingerprint()

    def test_none_init_inputs_are_dropped(self):
        cfg = _config()
        assert cfg.fingerprint(endpoint=None) == cfg.fingerprint()


class TestChanges:
    def test_every_effective_change_moves_the_fingerprint(self):
        base = _config()
        seen = {base.fingerprint()}
        variants = [
            _config(project_name="fp-b"),
            _config(sample_rate=0.5),
            _config(headers={"x-a": "1", "x-b": "3"}),
            _config(log_exclude_loggers=("urllib3",)),
            # backend endpoint, header, name, removed and added
            _config(backends=(_langfuse(endpoint="http://langfuse:3001"),)),
            _config(backends=(_langfuse(headers={"x-team": "red", "Authorization": BEARER}),)),
            _config(backends=(_langfuse(name="other"),)),
            _config(backends=base.backends[:2]),
            _config(
                backends=base.backends
                + (BackendConfig(type="jaeger", endpoint="http://jaeger:4318/v1/traces"),)
            ),
        ]
        for cfg in variants:
            fp = cfg.fingerprint()
            assert fp not in seen, cfg
            seen.add(fp)

    def test_explicit_endpoint_is_part_of_it(self):
        cfg = HermesOtelConfig()
        assert cfg.fingerprint(endpoint="http://a:6006/v1/traces") != cfg.fingerprint()
        assert cfg.fingerprint(endpoint="http://a:6006/v1/traces") != cfg.fingerprint(
            endpoint="http://b:6006/v1/traces"
        )
        assert cfg.fingerprint(endpoint="http://a:6006/v1/traces") == cfg.fingerprint(
            endpoint="http://a:6006/v1/traces"
        )

    def test_rotated_secret_changes_it(self):
        a = _config()
        b = _config(
            backends=(a.backends[0], _langfuse(secret_key=SECRET + "-rotated"), a.backends[2])
        )
        c = _config(
            backends=(
                a.backends[0],
                _langfuse(headers={"x-team": "blue", "Authorization": BEARER + "x"}),
                a.backends[2],
            )
        )
        d = _config(
            backends=(
                a.backends[0],
                a.backends[1],
                replace(a.backends[2], password=PASSWORD + "!"),
            )
        )
        assert len({a.fingerprint(), b.fingerprint(), c.fingerprint(), d.fingerprint()}) == 4


class TestSecrets:
    def test_secret_values_never_enter_the_hashed_text(self):
        src = fingerprint_source(_config(headers={"x-a": "1", "api-key": "top-secret-value"}))
        for secret in (SECRET, BEARER, PASSWORD, "top-secret-value", "tok-abcdef"):
            assert secret not in src
        # They are replaced by a stable hash, not dropped: the field still counts.
        assert src.count("sha256:") == 4

    def test_names_and_non_secrets_stay_readable(self):
        src = fingerprint_source(_config())
        for text in ("LF_PUBLIC", "http://langfuse:3000", "root", "fp-a", "blue", "urllib3"):
            assert text in src

    def test_a_bearer_string_is_a_secret_whatever_its_key(self):
        src = fingerprint_source(
            HermesOtelConfig(resource_attributes={"note": "Bearer abcdefghij"})
        )
        assert "abcdefghij" not in src and "sha256:" in src
