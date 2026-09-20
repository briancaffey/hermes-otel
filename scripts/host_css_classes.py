#!/usr/bin/env python3
"""Extract the class names the Hermes dashboard's compiled CSS defines.

The plugin tab styles itself with the host's Tailwind utility classes, but the
host only ships the classes its own pages use (#180). ``dashboard-ui/
host-classes.txt`` records which classes a given Hermes release defines so
``tests/unit/test_dashboard_css_classes.py`` can prove every class the TSX
uses is either there or in the plugin's own ``dist/style.css``.

Regenerate after bumping the Hermes version the plugin targets::

    python scripts/host_css_classes.py            # finds hermes_cli/web_dist
    python scripts/host_css_classes.py path/to/index-*.css

Class names are unescaped (``.sm\\:grid-cols-5`` → ``sm:grid-cols-5``).
"""

from __future__ import annotations

import glob
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "dashboard-ui" / "host-classes.txt"
_CLASS = re.compile(r"\.((?:\\.|[A-Za-z0-9_-])+)")


def find_host_css() -> Path:
    try:
        import hermes_cli  # type: ignore

        base = Path(hermes_cli.__file__).resolve().parent / "web_dist" / "assets"
    except Exception:  # pragma: no cover - depends on the environment
        base = Path.home() / "git" / "hermes-agent" / "hermes_cli" / "web_dist" / "assets"
    files = sorted(glob.glob(str(base / "index-*.css")))
    if not files:
        sys.exit(f"no host CSS under {base}; pass the path explicitly")
    return Path(files[-1])


def hermes_version() -> str:
    try:
        out = subprocess.run(["hermes", "--version"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip().splitlines()[0] if out.stdout.strip() else "unknown"
    except Exception:  # pragma: no cover
        return "unknown"


def extract(css_text: str) -> set:
    names = set()
    for m in _CLASS.finditer(css_text):
        raw = m.group(1)
        # A number right after a dot inside a selector is not a class
        # (``.5`` in ``opacity: .5`` never appears in selectors, but be safe).
        if raw[0].isdigit():
            continue
        names.add(re.sub(r"\\(.)", r"\1", raw))
    return names


def main(argv: list) -> None:
    css_path = Path(argv[1]) if len(argv) > 1 else find_host_css()
    names = sorted(extract(css_path.read_text(encoding="utf-8")))
    header = [
        "# Class names defined by the Hermes dashboard's compiled CSS.",
        f"# Source: {css_path.name}; hermes version: {hermes_version()}.",
        "# Regenerate with scripts/host_css_classes.py; see #180.",
    ]
    OUT.write_text("\n".join(header + names) + "\n", encoding="utf-8")
    print(f"wrote {len(names)} class names to {OUT}")


if __name__ == "__main__":
    main(sys.argv)
