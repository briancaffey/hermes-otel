"""``git commit`` detection on the terminal tool (#478): the command match, the
exit-code rule and the subject line read from git's confirmation."""

from __future__ import annotations

import pytest

from hermes_otel.hooks import tools


@pytest.mark.parametrize(
    "command, matched",
    [
        ("git commit", True),
        ('git commit -m "x"', True),
        ("git commit -am fix", True),
        ("git  commit", True),
        ("GIT_X=1 git commit", True),
        ("cd repo && git add . && git commit -m 'add'", True),
        ("git commit-tree HEAD^{tree}", False),
        ("git committed", False),
        ("git commits", False),
        ("mygit commit", False),
        ("git status", False),
        ("git log --oneline", False),
        ("", False),
        (None, False),
    ],
)
def test_the_command_match(command, matched):
    assert tools.is_git_commit("terminal", command) is matched


@pytest.mark.parametrize("tool_name", ["terminal", "bash", "shell"])
def test_shell_tools_match(tool_name):
    assert tools.is_git_commit(tool_name, "git commit -m x")


@pytest.mark.parametrize("tool_name", ["read_file", "write_file", "web_search", "", "process"])
def test_other_tools_never_match(tool_name):
    assert not tools.is_git_commit(tool_name, "git commit -m x")


def test_exit_code_from_either_spelling_and_never_a_bool():
    assert tools.exit_code({"exit_code": 0}) == 0
    assert tools.exit_code({"exit_code": 1}) == 1
    assert tools.exit_code({"returncode": 128}) == 128
    assert tools.exit_code({"exit_code": None}) is None
    assert tools.exit_code({"exit_code": True}) is None
    assert tools.exit_code({"exit_code": "0"}) is None
    assert tools.exit_code({}) is None


@pytest.mark.parametrize(
    "outcome, result, ok",
    [
        ("completed", {"exit_code": 0, "output": "[main abc1234] x"}, True),
        ("completed", {"output": "plain text, no exit code"}, True),
        ("completed", {"exit_code": 1, "output": "nothing to commit"}, False),
        ("completed", {"exit_code": 128}, False),
        ("error", {"exit_code": 0}, False),
        ("timeout", {}, False),
        ("blocked", {"exit_code": 0}, False),
        ("cancelled", {}, False),
    ],
)
def test_commit_succeeded(outcome, result, ok):
    assert tools.commit_succeeded(outcome, result) is ok


class TestCommitSubject:
    def test_reads_the_first_line_of_the_message(self):
        out = "[main 1a2b3c4] feat(x): add the thing\n 1 file changed, 2 insertions(+)\n"
        assert tools.commit_subject(out) == "feat(x): add the thing"

    def test_root_commit_form(self):
        out = "[main (root-commit) 9f8e7d6] initial\n 1 file changed\n"
        assert tools.commit_subject(out) == "initial"

    def test_detached_head_and_long_sha(self):
        out = "[detached HEAD 0123456789abcdef0123456789abcdef01234567] on a sha\n"
        assert tools.commit_subject(out) == "on a sha"

    def test_confirmation_after_other_output(self):
        out = "hint: something\n[feature/x 1234abc] later line\n"
        assert tools.commit_subject(out) == "later line"

    @pytest.mark.parametrize(
        "output",
        [None, "", 42, "nothing to commit, working tree clean", "[main 1234abc] ", "[main] no sha"],
    )
    def test_none_when_git_printed_no_confirmation(self, output):
        assert tools.commit_subject(output) is None

    def test_redacted_and_clipped(self, monkeypatch):
        seen = []

        def fake_redact(text):
            seen.append(text)
            return text.replace("sk-secret", "***")

        monkeypatch.setattr(tools, "redact_text", fake_redact)
        out = "[main 1234abc] token sk-secret " + "x" * 500 + "\n"
        subject = tools.commit_subject(out)
        assert seen and seen[0].startswith("token sk-secret")
        assert subject.startswith("token *** ")
        assert subject.endswith("...") and len(subject) == tools._COMMIT_SUBJECT_MAX + 3
