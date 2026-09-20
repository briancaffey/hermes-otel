"""Every class the dashboard TSX uses must exist somewhere (#180).

The host only compiles the Tailwind classes its own pages use, so a class the
plugin uses but the host never defined silently does nothing. The plugin's own
layout rules live in ``dist/style.css``; ``dashboard-ui/host-classes.txt`` is
the list of classes the targeted Hermes release ships (regenerate it with
``scripts/host_css_classes.py``).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "dashboard-ui" / "src"
HOST_LIST = ROOT / "dashboard-ui" / "host-classes.txt"
PLUGIN_CSS = ROOT / "hermes_otel" / "dashboard" / "dist" / "style.css"

# Classes used as values in colour maps rather than in className attributes.
_MAP_VALUE = re.compile(r'"((?:text|bg|border)-[a-z]+(?:-[a-z]+)*(?:-\d{2,3})?(?:/\d+)?)"')


def _class_attr_literals(text: str):
    """Yield the string literals of every ``className=`` attribute."""
    i = 0
    while True:
        i = text.find("className=", i)
        if i < 0:
            return
        i += len("className=")
        if text[i] == '"':
            j = text.index('"', i + 1)
            yield text[i + 1 : j]
            i = j + 1
        elif text[i] == "{":
            depth, j = 0, i
            while j < len(text):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            body = text[i + 1 : j]
            for lit in re.findall(r'"([^"]*)"|`([^`]*)`', body):
                yield lit[0] or lit[1]
            i = j + 1
        else:
            i += 1


def used_classes() -> set:
    tokens: set = set()
    for path in sorted(SRC.glob("*.ts*")):
        text = path.read_text(encoding="utf-8")
        for lit in _class_attr_literals(text):
            for tok in lit.split():
                if "${" in tok or "}" in tok:
                    continue  # template expression, not a literal class
                tokens.add(tok)
        for m in _MAP_VALUE.finditer(text):
            tokens.add(m.group(1))
    return tokens


def defined_classes() -> set:
    host = {
        line.strip()
        for line in HOST_LIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    plugin = {
        re.sub(r"\\(.)", r"\1", m.group(1))
        for m in re.finditer(r"\.((?:\\.|[A-Za-z0-9_-])+)", PLUGIN_CSS.read_text(encoding="utf-8"))
    }
    return host | plugin


def test_every_class_used_by_the_tsx_is_defined():
    used = used_classes()
    assert len(used) > 100, "class extraction found suspiciously few tokens"
    missing = sorted(used - defined_classes())
    assert not missing, f"classes with no definition in the host CSS or dist/style.css: {missing}"


def test_plugin_classes_are_defined_in_the_plugin_stylesheet():
    """``otel-*`` classes never come from the host; they must be in style.css."""
    plugin_css = PLUGIN_CSS.read_text(encoding="utf-8")
    ours = sorted(c for c in used_classes() if c.startswith("otel-"))
    assert ours, "no otel-* classes found in the TSX"
    missing = [c for c in ours if f".{c}" not in plugin_css]
    assert not missing, missing


def test_host_list_is_pinned_to_a_hermes_version():
    head = HOST_LIST.read_text(encoding="utf-8").splitlines()[:3]
    assert any("hermes version" in line for line in head), head
