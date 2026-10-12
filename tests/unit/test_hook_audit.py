"""The hook drift audit (#400): plugin registrations vs Hermes's VALID_HOOKS."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "hook_audit", REPO / "scripts" / "hook_audit" / "hook_audit.py"
)
hook_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook_audit)


def _fake_hermes(tmp_path: Path, hooks) -> Path:
    (tmp_path / "hermes_cli").mkdir()
    body = ", ".join(repr(h) for h in hooks)
    (tmp_path / "hermes_cli" / "plugins.py").write_text(
        f"VALID_HOOKS: set[str] = {{\n    # a comment\n    {body},\n}}\n", encoding="utf-8"
    )
    return tmp_path


def test_reads_the_literal_set(tmp_path):
    assert hook_audit.valid_hooks(_fake_hermes(tmp_path, ["a", "b"])) == {"a", "b"}


def test_flags_a_renamed_hook_and_a_new_one(tmp_path):
    declared = hook_audit.declared_hooks()
    offered = (declared - {"pre_verify"}) | {"brand_new_hook"} | set(hook_audit.NOT_REGISTERED)
    result = hook_audit.audit(_fake_hermes(tmp_path, sorted(offered)))
    assert result["gone_upstream"] == {"pre_verify"}
    assert result["undecided"] == {"brand_new_hook"}
    assert not result["stale_skips"]


def test_a_skip_for_a_registered_hook_is_stale(tmp_path, monkeypatch):
    monkeypatch.setitem(hook_audit.NOT_REGISTERED, "pre_tool_call", "oops")
    offered = hook_audit.declared_hooks() | set(hook_audit.NOT_REGISTERED)
    result = hook_audit.audit(_fake_hermes(tmp_path, sorted(offered)))
    assert "pre_tool_call" in result["stale_skips"]


_CHECKOUT = Path(os.environ.get("HERMES_AGENT_DIR", "~/git/hermes-agent")).expanduser()


@pytest.mark.skipif(
    not (_CHECKOUT / "hermes_cli" / "plugins.py").exists(), reason="no hermes-agent checkout"
)
def test_no_drift_against_the_local_hermes_checkout():
    result = hook_audit.audit(_CHECKOUT)
    assert not result["gone_upstream"], result["gone_upstream"]
    assert not result["undecided"], result["undecided"]
    assert not result["stale_skips"], result["stale_skips"]
