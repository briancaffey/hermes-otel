"""Unit tests for helpers.py: the shared text helpers, the plugin version and traceparent."""

import json
import re
from pathlib import Path

import pytest

from hermes_otel.helpers import (
    clip_joined,
    clip_preview,
    format_traceparent,
    package_version,
    parse_traceparent,
    serialize_full,
    truncate_string,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestClipPreview:
    def test_returns_none_for_none(self):
        assert clip_preview(None, 100) is None

    def test_returns_none_for_empty_string(self):
        assert clip_preview("", 100) is None

    def test_returns_none_for_whitespace_only(self):
        assert clip_preview("   \n\t  ", 100) is None

    def test_strips_ansi_csi(self):
        # Red "hello" then reset
        assert clip_preview("\x1b[31mhello\x1b[0m world", 100) == "hello world"

    def test_strips_ansi_osc(self):
        # OSC — title setter
        assert clip_preview("\x1b]0;title\x07hello", 100) == "hello"

    def test_returns_none_when_only_ansi(self):
        assert clip_preview("\x1b[31m\x1b[0m", 100) is None

    def test_collapses_newlines(self):
        assert clip_preview("line1\nline2\n\nline3", 100) == "line1 line2 line3"

    def test_collapses_tabs_and_carriage_returns(self):
        assert clip_preview("a\tb\rc", 100) == "a b c"

    def test_collapses_multiple_spaces(self):
        assert clip_preview("a    b    c", 100) == "a b c"

    def test_trims_leading_trailing_whitespace(self):
        assert clip_preview("   hello   ", 100) == "hello"

    def test_returns_verbatim_when_under_limit(self):
        assert clip_preview("short", 100) == "short"

    def test_truncates_when_over_limit(self):
        result = clip_preview("x" * 50, 10)
        assert len(result) == 10
        assert result.endswith("...")
        assert result == "xxxxxxx..."

    def test_truncates_exactly_at_limit(self):
        result = clip_preview("x" * 10, 10)
        assert result == "x" * 10

    def test_non_string_coerced(self):
        assert clip_preview(12345, 100) == "12345"

    def test_zero_max_chars_returns_none(self):
        assert clip_preview("hello", 0) is None


class TestTruncateString:
    def test_normal_string(self):
        assert truncate_string("hello") == "hello"

    def test_integer_input(self):
        assert truncate_string(42) == "42"

    def test_none_input(self):
        assert truncate_string(None) == "None"

    def test_truncates_at_default_max(self):
        long = "x" * 1500
        result = truncate_string(long)
        assert len(result) == 1003  # 1000 + "..."
        assert result.endswith("...")

    def test_truncates_at_custom_max(self):
        result = truncate_string("abcdefghij", max_len=5)
        assert result == "abcde..."

    def test_exact_max_len_not_truncated(self):
        result = truncate_string("abcde", max_len=5)
        assert result == "abcde"

    def test_unserializable_object(self):
        class BadStr:
            def __str__(self):
                raise RuntimeError("nope")

        result = truncate_string(BadStr())
        assert result == "<unserializable>"

    def test_empty_string(self):
        assert truncate_string("") == ""


class TestClipJoined:
    def test_empty_items(self):
        assert clip_joined([], ", ") == ""

    def test_under_limit_is_verbatim(self):
        assert clip_joined(["ls", "pwd"], "; ") == "ls; pwd"

    def test_over_limit_gets_ellipsis(self):
        assert clip_joined(["abcdef", "ghijkl"], "", limit=8) == "abcde..."

    def test_tiny_limit_is_all_dots(self):
        assert clip_joined(["abcdef"], "", limit=2) == ".."


class TestClipPreviewEdges:
    def test_tiny_limit_is_all_dots(self):
        assert clip_preview("abcdef", 2) == ".."

    def test_unstringable_is_none(self):
        class Bad:
            def __str__(self):
                raise RuntimeError("nope")

        assert clip_preview(Bad(), 10) is None


class TestSerializeFull:
    def test_empty_is_none(self):
        for empty in (None, "", [], {}):
            assert serialize_full(empty) is None

    def test_namespaces_flatten_to_their_dict(self):
        from types import SimpleNamespace

        call = SimpleNamespace(id="c1", function=SimpleNamespace(name="ls", arguments="{}"))
        assert json.loads(serialize_full([call])) == [
            {"id": "c1", "function": {"name": "ls", "arguments": "{}"}}
        ]

    def test_unserialisable_falls_back_to_str(self):
        circular: list = []
        circular.append(circular)
        assert serialize_full(circular) == str(circular)

    def test_unstringable_is_none(self):
        class Bad:
            __slots__ = ()

            def __str__(self):
                raise RuntimeError("nope")

        assert serialize_full(Bad()) is None


class TestPackageVersion:
    def test_reads_the_shipped_manifest(self):
        manifest = (REPO_ROOT / "hermes_otel" / "plugin.yaml").read_text(encoding="utf-8")
        declared = re.search(r"^version:\s*\"?([0-9][^\"\s]*)\"?\s*$", manifest, re.M).group(1)
        assert package_version() == declared

    def test_falls_back_to_distribution_metadata(self, monkeypatch):
        import importlib.metadata

        from hermes_otel import helpers

        def no_manifest(*_args, **_kwargs):
            raise OSError("no manifest")

        def no_distribution(_name):
            raise importlib.metadata.PackageNotFoundError(_name)

        monkeypatch.setattr(helpers, "open", no_manifest, raising=False)
        monkeypatch.setattr(importlib.metadata, "version", lambda _name: "9.9.9")
        package_version.cache_clear()
        try:
            assert package_version() == "9.9.9"
            monkeypatch.setattr(importlib.metadata, "version", no_distribution)
            package_version.cache_clear()
            assert package_version() is None
        finally:
            package_version.cache_clear()


class TestParseTraceparent:
    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "00-abc",
            "01-" + "a" * 32 + "-" + "b" * 16 + "-01",  # not version 00
            "00-" + "a" * 31 + "-" + "b" * 16 + "-01",  # short trace id
            "00-" + "a" * 32 + "-" + "b" * 15 + "-01",  # short span id
            "00-" + "a" * 32 + "-" + "b" * 16 + "-1",  # short flags
            "00-" + "g" * 32 + "-" + "b" * 16 + "-01",  # not hex
            "00-" + "0" * 32 + "-" + "b" * 16 + "-01",  # all-zero trace id
            "00-" + "a" * 32 + "-" + "0" * 16 + "-01",  # all-zero span id
        ],
    )
    def test_malformed_is_none(self, value):
        assert parse_traceparent(value) is None

    def test_round_trip(self):
        header = format_traceparent(0xABC, 0x123, 1)
        assert header == "00-" + "0" * 29 + "abc-" + "0" * 13 + "123-01"
        assert parse_traceparent(header.upper()) == (0xABC, 0x123, 1)
        assert format_traceparent(1, 2, 0).endswith("-00")
