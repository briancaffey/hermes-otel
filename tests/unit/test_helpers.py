"""Unit tests for helpers.py: clip_preview, tool identity, skill inference, outcome."""

import pytest
from hermes_otel.helpers import (
    clip_preview,
    extract_tool_result_status,
    infer_skill_name,
    infer_skill_name_from_text,
    resolve_tool_identity,
)


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


class TestResolveToolIdentity:
    def test_path_key_wins(self):
        t, c = resolve_tool_identity({"path": "/tmp/foo", "target": "/other"})
        assert t == "/tmp/foo"
        assert c is None

    def test_file_path_fallback(self):
        t, _ = resolve_tool_identity({"file_path": "/etc/hosts"})
        assert t == "/etc/hosts"

    def test_target_fallback(self):
        t, _ = resolve_tool_identity({"target": "widget"})
        assert t == "widget"

    def test_url_fallback(self):
        t, _ = resolve_tool_identity({"url": "https://example.com"})
        assert t == "https://example.com"

    def test_uri_fallback(self):
        t, _ = resolve_tool_identity({"uri": "file:///x"})
        assert t == "file:///x"

    def test_command_key(self):
        _, c = resolve_tool_identity({"command": "ls -la"})
        assert c == "ls -la"

    def test_cmd_fallback(self):
        _, c = resolve_tool_identity({"cmd": "pwd"})
        assert c == "pwd"

    def test_both_target_and_command(self):
        t, c = resolve_tool_identity({"path": "/x", "command": "cat"})
        assert t == "/x"
        assert c == "cat"

    def test_none_args(self):
        assert resolve_tool_identity(None) == (None, None)

    def test_non_dict_args(self):
        assert resolve_tool_identity("not-a-dict") == (None, None)

    def test_empty_values_ignored(self):
        t, c = resolve_tool_identity({"path": "  ", "command": ""})
        assert t is None
        assert c is None

    def test_non_string_values_ignored(self):
        t, c = resolve_tool_identity({"path": 42, "command": [1, 2]})
        assert t is None
        assert c is None


class TestExtractToolResultStatus:
    def test_explicit_status_string(self):
        assert extract_tool_result_status({"status": "timeout"}) == "timeout"

    def test_explicit_status_wins_over_error(self):
        assert extract_tool_result_status({"status": "blocked", "error": "x"}) == "blocked"

    def test_explicit_status_lowercased(self):
        assert extract_tool_result_status({"status": "Error"}) == "error"

    def test_error_field_maps_to_error(self):
        assert extract_tool_result_status({"error": "boom"}) == "error"

    def test_empty_error_is_ignored(self):
        assert extract_tool_result_status({"error": ""}) is None

    def test_whitespace_error_is_ignored(self):
        assert extract_tool_result_status({"error": "   "}) is None

    def test_timeout_flag(self):
        assert extract_tool_result_status({"timeout": True}) == "timeout"

    def test_blocked_flag(self):
        assert extract_tool_result_status({"blocked": True}) == "blocked"

    def test_non_dict_returns_none(self):
        assert extract_tool_result_status("ok") is None
        assert extract_tool_result_status(None) is None
        assert extract_tool_result_status(42) is None

    def test_empty_dict_returns_none(self):
        assert extract_tool_result_status({}) is None


class TestInferSkillName:
    def test_matches_skills_path(self):
        assert infer_skill_name({"path": "/home/user/skills/monitor/SKILL.md"}) == "monitor"

    def test_matches_skills_path_no_trailing(self):
        # Just /skills/name with no trailing slash should still match if end of string
        assert infer_skill_name({"path": "/a/b/skills/foo"}) == "foo"

    def test_does_not_match_optional_skills_references(self):
        assert infer_skill_name({"path": "/optional-skills/monitor/references/x.md"}) is None

    def test_file_path_key(self):
        assert infer_skill_name({"file_path": "/x/skills/builder/tool.py"}) == "builder"

    def test_target_key(self):
        assert infer_skill_name({"target": "/repo/skills/deployer/README"}) == "deployer"

    def test_no_path_returns_none(self):
        assert infer_skill_name({"command": "ls"}) is None

    def test_no_match_returns_none(self):
        assert infer_skill_name({"path": "/tmp/regular/file.txt"}) is None

    def test_none_args(self):
        assert infer_skill_name(None) is None

    def test_infer_from_text_helper(self):
        assert infer_skill_name_from_text("cat /skills/hello/x") == "hello"

    def test_infer_from_text_miss(self):
        assert infer_skill_name_from_text("no match here") is None

    def test_infer_from_text_non_string(self):
        assert infer_skill_name_from_text(42) is None
