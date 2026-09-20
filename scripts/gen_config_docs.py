#!/usr/bin/env python3
"""Render the config-field and env-var reference tables from HermesOtelConfig.

The tables live between ``[//]: # (generated:<name>:start)`` / ``:end`` marker lines in
``website/docs/reference/config-schema.md`` and ``env-vars.md``. Run with
``--write`` to update them, ``--check`` (the default; also run by
``tests/unit/test_config_surface.py``) to fail when they drift from the code.

    uv run --extra dev python scripts/gen_config_docs.py --write
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from hermes_otel.plugin_config import FIELD_DOCS, HermesOtelConfig, field_kinds  # noqa: E402

DOCS = REPO_ROOT / "website" / "docs" / "reference"

_TYPE_LABEL = {
    "bool": "bool",
    "int": "int",
    "float": "float",
    "str": "string",
    "map": "map",
    "backends": "list",
}


def _default_label(field: dataclasses.Field, kind: str) -> str:
    d = field.default
    if d is None or d is dataclasses.MISSING:
        return "*(unset)*" if kind in ("map", "backends", "str") else "`null`"
    if isinstance(d, bool):
        return f"`{'true' if d else 'false'}`"
    if isinstance(d, str):
        return f'`"{d}"`'
    return f"`{d}`"


def render_fields_table() -> str:
    kinds = field_kinds()
    rows = ["| Field | Type | Default | Description |", "|---|---|---|---|"]
    for f in dataclasses.fields(HermesOtelConfig):
        kind = kinds[f.name]
        typ = _TYPE_LABEL[kind]
        if kind in ("float", "int", "str") and str(f.type).startswith("Optional"):
            typ += " \\| null"
        rows.append(f"| `{f.name}` | {typ} | {_default_label(f, kind)} | {FIELD_DOCS[f.name]} |")
    return "\n".join(rows)


def render_env_table() -> str:
    kinds = field_kinds()
    rows = ["| Env var | Maps to | Type | Default |", "|---|---|---|---|"]
    for f in dataclasses.fields(HermesOtelConfig):
        kind = kinds[f.name]
        if kind not in ("bool", "int", "float", "str"):
            continue
        rows.append(
            f"| `HERMES_OTEL_{f.name.upper()}` | `{f.name}` | {_TYPE_LABEL[kind]} | {_default_label(f, kind)} |"
        )
    return "\n".join(rows)


BLOCKS = {
    (DOCS / "config-schema.md", "config-fields"): render_fields_table,
    (DOCS / "env-vars.md", "env-overrides"): render_env_table,
}


def _splice(text: str, name: str, body: str) -> str:
    start, end = f"[//]: # (generated:{name}:start)", f"[//]: # (generated:{name}:end)"
    pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pat.search(text):
        raise SystemExit(f"markers for {name!r} not found")
    return pat.sub(lambda _m: f"{start}\n\n{body}\n\n{end}", text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="update the docs in place")
    args = ap.parse_args(argv)
    missing = [f.name for f in dataclasses.fields(HermesOtelConfig) if f.name not in FIELD_DOCS]
    if missing:
        print(f"FIELD_DOCS is missing: {missing}")
        return 1
    drift = 0
    for (path, name), render in BLOCKS.items():
        current = path.read_text(encoding="utf-8")
        updated = _splice(current, name, render())
        if updated != current:
            drift += 1
            if args.write:
                path.write_text(updated, encoding="utf-8")
                print(f"updated {path.relative_to(REPO_ROOT)} [{name}]")
            else:
                print(f"DRIFT: {path.relative_to(REPO_ROOT)} [{name}] — run with --write")
    if args.write:
        return 0
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
