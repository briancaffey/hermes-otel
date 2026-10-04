"""Secret redaction for exported telemetry (#265): Hermes's redactor when present, a built-in floor otherwise."""

from __future__ import annotations

import sys
import types

import pytest

from hermes_otel import redaction as r


@pytest.fixture(autouse=True)
def _fresh_cache():
    r._reset_cache()
    yield
    r._reset_cache()


class TestBuiltinFallback:
    @pytest.fixture(autouse=True)
    def _no_hermes(self, monkeypatch):
        monkeypatch.setattr(r, "_hermes_resolved", True)
        monkeypatch.setattr(r, "_hermes_redactor", None)
        monkeypatch.delenv("HERMES_REDACT_SECRETS", raising=False)

    def _cases(self):
        """(text, substring that must vanish, substring that must survive); keys assembled at runtime."""
        k = lambda prefix, body: prefix + body  # noqa: E731
        return [
            (
                "Authorization: Bearer " + k("sk-proj-", "abcdefghijklmnopqrstuvwxyz0123456789"),
                "abcdefghijklmnopqrstuvwxyz",
                "Bearer sk-pro",
            ),
            (
                "authorization: Basic " + k("dXNlcjpw", "YXNzd29yZA=="),
                "dXNlcjpwYXNzd29yZA==",
                "Basic ",
            ),
            (
                "OPENAI_API_KEY=" + k("sk-", "abcdefghijklmnopqrstuvwxyz"),
                "abcdefghijklmnopqrstuvwxyz",
                "OPENAI_API_KEY=sk-abc",
            ),
            (
                "LANGFUSE_SECRET_KEY=" + k("sk-lf-", "12345678-abcd-efgh-ijkl-123456789012"),
                "12345678-abcd",
                "sk-lf-",
            ),
            (
                '{"x-api-key": "' + k("hcaik_", "0123456789abcdef") + '"}',
                "hcaik_" + "0123456789abcdef",
                '"x-api-key": "',
            ),
            (k("ghp_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"), "CDEFGHIJKLMNOPQRSTUVWXYZ0", "ghp_AB"),
            ("password: hunter2!", "hunter2", "password: "),
            (
                "https://user:p4ssw0rd@host.example/path",
                "p4ssw0rd",
                "https://user:***@host.example/path",
            ),
            (
                k("AKIA", "IOSFODNN7EXAMPLE") + " and " + k("xoxb-", "1234567890-abcdefghij"),
                "IOSFODNN7EXAMPLE",
                "xoxb-1",
            ),
        ]

    def test_credential_shapes_are_masked(self):
        for text, gone, kept in self._cases():
            out = r.redact_text(text)
            assert gone not in out, text
            assert kept in out, (text, out)
            assert out != text

    @pytest.mark.parametrize(
        "text",
        [
            "tool terminal completed in 0.3s (status=ok, task=t1)",
            "retrying after 429 from openrouter (attempt 2)",
            "bearer of bad news",
            "the key to success is persistence",
            "https://example.com/path?x=1",
            "",
        ],
    )
    def test_ordinary_text_is_untouched(self, text):
        assert r.redact_text(text) == text

    def test_non_strings_pass_through(self):
        assert r.redact_text(None) is None
        assert r.redact_text(42) == 42

    def test_mask_shape_matches_hermes(self):
        assert r.mask("short") == "***"
        assert r.mask("abcdefghijklmnopqrstuvwxyz") == "abcdef***wxyz"

    def test_env_switch_disables_the_fallback(self, monkeypatch):
        monkeypatch.setenv("HERMES_REDACT_SECRETS", "false")
        text = "token=" + "sk-" + "abcdefghijklmnopqrstuvwxyz"
        assert r.redact_text(text) == text
        assert r.redaction_source() == "off"

    def test_source_is_builtin(self):
        assert r.redaction_source() == "builtin"

    def test_attributes_are_redacted_in_place_strings_only(self):
        attrs = {"a": "x-api-key: " + "hcaik_" + "0123456789abcdef", "n": 7, "b": "", "c": None}
        r.redact_attributes(attrs)
        assert "0123456789abcdef" not in attrs["a"]
        assert attrs["n"] == 7 and attrs["b"] == "" and attrs["c"] is None
        r.redact_attributes(None)  # no-op


class TestHermesRedactor:
    def test_hermes_redactor_is_preferred_when_importable(self, monkeypatch):
        fake_pkg = types.ModuleType("agent")
        fake_mod = types.ModuleType("agent.redact")
        calls = []

        def redact_sensitive_text(text):
            calls.append(text)
            return "<hermes>" + text

        fake_mod.redact_sensitive_text = redact_sensitive_text
        monkeypatch.setitem(sys.modules, "agent", fake_pkg)
        monkeypatch.setitem(sys.modules, "agent.redact", fake_mod)
        assert r.redact_text("hello") == "<hermes>hello"
        assert r.redaction_source() == "hermes"
        assert calls == ["hello"]

    def test_hermes_redactor_failure_returns_the_original(self, monkeypatch):
        fake_pkg = types.ModuleType("agent")
        fake_mod = types.ModuleType("agent.redact")

        def boom(text):
            raise RuntimeError("no")

        fake_mod.redact_sensitive_text = boom
        monkeypatch.setitem(sys.modules, "agent", fake_pkg)
        monkeypatch.setitem(sys.modules, "agent.redact", fake_mod)
        assert (
            r.redact_text("token=" + "sk-" + "abcdefghijklmnopqrstuvwxyz")
            == "token=" + "sk-" + "abcdefghijklmnopqrstuvwxyz"
        )

    def test_import_result_is_cached(self, monkeypatch):
        fake_pkg = types.ModuleType("agent")
        fake_mod = types.ModuleType("agent.redact")
        fake_mod.redact_sensitive_text = lambda t: "h:" + t
        monkeypatch.setitem(sys.modules, "agent", fake_pkg)
        monkeypatch.setitem(sys.modules, "agent.redact", fake_mod)
        assert r.redact_text("a") == "h:a"
        monkeypatch.delitem(sys.modules, "agent.redact")
        assert r.redact_text("b") == "h:b"  # still the cached callable
