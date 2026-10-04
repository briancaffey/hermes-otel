"""The log-events reference must list every event the hooks can emit (#269), and the
catalogue must describe every event constant the hooks use."""

from __future__ import annotations

import re
from pathlib import Path

from hermes_otel import log_events as EV

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hermes_otel" / "hooks"
EVENTS_MD = REPO_ROOT / "website" / "docs" / "reference" / "log-events.md"
LOGS_MD = REPO_ROOT / "website" / "docs" / "configuration" / "logs.md"


def _event_constants_used_by_hooks() -> set:
    """``EV.<NAME>`` references in the hooks that name an event (not a prefix or attribute)."""
    names = set()
    for path in HOOKS.glob("*.py"):
        for const in re.findall(r"\bEV\.([A-Z_]+)\b", path.read_text(encoding="utf-8")):
            value = getattr(EV, const, None)
            if isinstance(value, str) and value in EV.EVENTS:
                names.add(value)
    return names


def test_every_emitted_event_is_in_the_catalogue():
    used = _event_constants_used_by_hooks()
    assert used, "no EV.* event constants found in hooks/"
    assert used <= set(EV.EVENTS), sorted(used - set(EV.EVENTS))


def test_every_catalogue_event_is_emitted_somewhere():
    used = _event_constants_used_by_hooks()
    assert sorted(set(EV.EVENTS) - used) == [], "catalogue entries no hook emits"


def test_every_event_is_documented_in_the_reference_and_the_guide():
    ref = EVENTS_MD.read_text(encoding="utf-8")
    guide = LOGS_MD.read_text(encoding="utf-8")
    missing_ref = [n for n in EV.EVENTS if f"### `{n}`" not in ref]
    missing_guide = [n for n in EV.EVENTS if f"`{n}`" not in guide]
    assert missing_ref == [], f"regenerate reference/log-events.md: {missing_ref}"
    assert missing_guide == [], f"mention in configuration/logs.md: {missing_guide}"


def test_catalogue_entries_are_complete():
    for name, spec in EV.EVENTS.items():
        assert spec["hook"] and spec["severity"] and spec["body"], name
        assert spec["attributes"], name
        for attr in spec["attributes"]:
            clean = attr.replace(" (content)", "")
            assert re.fullmatch(r"[a-z_.]+(?: / [a-z_.]+)?", clean), (name, attr)
            if attr.endswith(" (content)"):
                assert clean in EV.CONTENT_ATTRIBUTES, (name, attr)
