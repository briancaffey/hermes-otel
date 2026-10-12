#!/usr/bin/env python3
"""Compare the plugin's hook registrations with the hooks Hermes offers (#400).

    uv run --extra dev python scripts/hook_audit/hook_audit.py --hermes-agent ~/git/hermes-agent

``VALID_HOOKS`` is read from ``hermes_cli/plugins.py`` in a hermes-agent
checkout by parsing the source (nothing is imported). Every valid hook must be
either declared in ``hermes_otel/plugin.yaml`` (``provides_hooks``) or listed
in :data:`NOT_REGISTERED` with the reason; every declared hook must still be
valid. Exit 1 on a gap, so a hook Hermes renamed or removed, or a new one
nobody has decided about, fails loudly instead of silently. Re-run after every
``hermes update``.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, Set

REPO = Path(__file__).resolve().parents[2]

# Hooks Hermes offers that the plugin deliberately does not register, and why.
NOT_REGISTERED: Dict[str, str] = {
    "on_stream_start": "time to first chunk comes from post_api_request's first_chunk_at (#396)",
    "on_stream_delta": "per chunk; nothing the plugin needs (#396)",
    "on_stream_end": "the api span already ends on post_api_request (#396)",
    "on_interim_message": "interim text is not telemetry (#396)",
    "transform_api_error_classification": "decision hook (its return changes failover); not yet observed (#400)",
    "transform_tool_result": "replaces tool results; observer attributes pending (#400)",
    "pre_gateway_dispatch": "policy gate (its return drops or rewrites messages); see #32",
    "post_gateway_admission": "candidate for the gateway ingest root (#32)",
    "gateway_platform_event": "platform reactions / edits; low value for tracing (#400)",
    "pre_command": "slash-command counter pending (#400)",
    "on_human_input_request": "human-input spans pending (#400)",
    "on_human_input_resolved": "human-input spans pending (#400)",
    "on_skill_lifecycle": "skill.* spans already cover skills; lifecycle event pending (#400)",
    "on_room_member_activity": "bot-to-bot surfaces need a decision (#393)",
    "pre_transcription": "voice-turn counter pending (#400)",
}


def valid_hooks(hermes_agent: Path) -> Set[str]:
    """The string members of ``VALID_HOOKS`` in hermes_cli/plugins.py."""
    tree = ast.parse((hermes_agent / "hermes_cli" / "plugins.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        if target == "VALID_HOOKS" and isinstance(value, (ast.Set, ast.List, ast.Tuple)):
            return {
                e.value
                for e in value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
    raise SystemExit("VALID_HOOKS not found as a literal set in hermes_cli/plugins.py")


def declared_hooks() -> Set[str]:
    import yaml  # type: ignore

    data = yaml.safe_load((REPO / "hermes_otel" / "plugin.yaml").read_text(encoding="utf-8"))
    return set(data.get("provides_hooks") or [])


def audit(hermes_agent: Path) -> Dict[str, Set[str]]:
    valid, declared = valid_hooks(hermes_agent), declared_hooks()
    return {
        # registered by the plugin but no longer offered: renamed or removed upstream
        "gone_upstream": declared - valid,
        # offered upstream, neither registered nor deliberately skipped
        "undecided": valid - declared - set(NOT_REGISTERED),
        # a skip reason for a hook the plugin now registers, or Hermes dropped
        "stale_skips": set(NOT_REGISTERED) & declared | (set(NOT_REGISTERED) - valid),
        "registered": declared & valid,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--hermes-agent", type=Path, default=Path("~/git/hermes-agent").expanduser())
    args = ap.parse_args()
    result = audit(args.hermes_agent.expanduser())
    print(f"registered and valid: {len(result['registered'])}")
    bad = False
    for key, label in (
        ("gone_upstream", "registered but not in VALID_HOOKS (renamed or removed upstream)"),
        (
            "undecided",
            "new in VALID_HOOKS: register it or add it to NOT_REGISTERED with the reason",
        ),
        ("stale_skips", "in NOT_REGISTERED but registered or no longer offered: drop the entry"),
    ):
        if result[key]:
            bad = True
            print(f"\n{label}:")
            for hook in sorted(result[key]):
                print(f"  - {hook}")
    if not bad:
        print("no drift: every Hermes hook is registered or deliberately skipped")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
