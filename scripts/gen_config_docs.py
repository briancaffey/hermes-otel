#!/usr/bin/env python3
"""Render the config-field and env-var reference tables from HermesOtelConfig.

The tables live between ``[//]: # (generated:<name>:start)`` / ``:end`` marker lines in
``website/docs/reference/config-schema.md``, ``env-vars.md`` and ``log-events.md``. Run with
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

from hermes_otel import log_events as EV  # noqa: E402
from hermes_otel.backend_specs import SPECS  # noqa: E402
from hermes_otel.instruments import metric_spellings  # noqa: E402
from hermes_otel.plugin_config import SPAN_FAMILIES, HermesOtelConfig, field_kinds  # noqa: E402

DOCS = REPO_ROOT / "website" / "docs" / "reference"
CONFIG_DOCS = REPO_ROOT / "website" / "docs" / "configuration"

_TYPE_LABEL = {
    "bool": "bool",
    "int": "int",
    "float": "float",
    "str": "string",
    "map": "map",
    "list": "list of strings (env: comma-separated)",
    "backends": "list",
}


def _default_label(field: dataclasses.Field, kind: str) -> str:
    d = field.default
    if d is None or d is dataclasses.MISSING:
        return "*(unset)*" if kind in ("map", "backends", "str") else "`null`"
    if isinstance(d, bool):
        return f"`{'true' if d else 'false'}`"
    if isinstance(d, tuple):
        return f"`[{', '.join(d)}]`"
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
        rows.append(f"| `{f.name}` | {typ} | {_default_label(f, kind)} | {_describe(f)} |")
    return "\n".join(rows)


def _describe(field: dataclasses.Field) -> str:
    """The field's ``doc`` plus its numeric ``bounds`` (#487), from its metadata."""
    desc = field.metadata["doc"]
    bounds = field.metadata.get("bounds")
    if bounds is None:
        return desc
    lo, hi = bounds
    rule = f"{lo}–{hi}" if hi is not None else f"≥ {lo}"
    return f"{desc}. Bounds: {rule}"


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


def render_events_table() -> str:
    """One section per event from ``hermes_otel.log_events.EVENTS`` (#269)."""
    out = []
    for name, spec in EV.EVENTS.items():
        out.append(f"### `{name}`")
        out.append("")
        out.append(
            f"Emitted from **`{spec['hook']}`**. Severity: {spec['severity']}. Body: `{spec['body']}`."
        )
        out.append("")
        out.append("| Attribute | Content-gated |")
        out.append("|---|---|")
        for attr in spec["attributes"]:
            gated = attr.endswith(" (content)")
            clean = attr[: -len(" (content)")] if gated else attr
            out.append(f"| `{clean}` | {'yes' if gated else ''} |")
        out.append("")
    return "\n".join(out).rstrip()


def _signals(spec) -> str:
    parts = ["traces"]
    if spec.metrics:
        parts.append("metrics")
    if spec.logs:
        parts.append("logs")
    return " + ".join(parts)


def render_backend_types() -> str:
    """The ``type:`` list in ``config.yaml.example`` (yaml comment lines), from
    the manifest: name, signals, how it is selected in env-var mode, docs page."""
    rows = ["# Supported `type` values (generated from hermes_otel/backend_specs.py):"]
    for spec in SPECS:
        bits = [_signals(spec)]
        if spec.aliases:
            bits.append("alias " + " / ".join(f"`{a}`" for a in spec.aliases))
        if spec.env_opt_in:
            bits.append("env-var mode needs " + " or ".join(spec.env_opt_in[:2]))
        elif spec.endpoint_env:
            bits.append(f"env-var mode: {spec.endpoint_env}")
        else:
            bits.append("backends: entry only")
        if spec.adapter_module is None:
            bits.append("export only (no dashboard adapter)")
        rows.append(f"#   {spec.type:<12} {spec.display_name} — {'; '.join(bits)}")
        rows.append(f"#   {'':<12} docs: website/docs/backends/{spec.type}.md")
    return "\n".join(rows)


def render_backend_env_table() -> str:
    """The backend half of the env-var reference, from each spec's inventory."""
    rows = ["| Var | Backend | Effect |", "|---|---|---|"]
    for spec in SPECS:
        for name, description in spec.env_vars:
            rows.append(f"| `{name}` | {spec.display_name} | {description} |")
    return "\n".join(rows)


def render_disable_metrics() -> str:
    """The ``disable_metrics`` vocabulary (#479): every instrument's OTLP name
    and the other spellings the loader accepts for it."""
    rows = ["| Instrument | Also accepted |", "|---|---|"]
    for name, spellings in metric_spellings().items():
        rows.append(f"| `{name}` | {', '.join(f'`{s}`' for s in spellings) or '—'} |")
    return "\n".join(rows)


def render_disable_spans() -> str:
    """The ``disable_spans`` vocabulary (#479), as one line of names."""
    return ", ".join(f"`{f}`" for f in SPAN_FAMILIES)


def render_disable_names_yaml() -> str:
    """Both vocabularies as yaml comment lines for ``config.yaml.example``."""
    rows = ["# Valid disable_metrics entries (generated from hermes_otel/instruments.py):"]
    rows.append("#   all")
    for name, spellings in metric_spellings().items():
        rows.append(f"#   {name:<36} {' / '.join(spellings)}".rstrip())
    rows.append("# Valid disable_spans families (generated from hermes_otel/plugin_config.py):")
    rows.append(f"#   {', '.join(SPAN_FAMILIES)}")
    return "\n".join(rows)


BLOCKS = {
    (DOCS / "config-schema.md", "config-fields"): render_fields_table,
    (CONFIG_DOCS / "yaml.md", "disable-metrics"): render_disable_metrics,
    (CONFIG_DOCS / "yaml.md", "disable-spans"): render_disable_spans,
    (REPO_ROOT / "config.yaml.example", "disable-names"): render_disable_names_yaml,
    (DOCS / "env-vars.md", "env-overrides"): render_env_table,
    (DOCS / "env-vars.md", "backend-env-vars"): render_backend_env_table,
    (DOCS / "log-events.md", "log-events"): render_events_table,
    (REPO_ROOT / "config.yaml.example", "backend-types"): render_backend_types,
}


def _splice(text: str, name: str, body: str, prefix: str = "") -> str:
    """Replace the block between the ``generated:<name>`` markers. ``prefix`` is
    what precedes each marker in a file that cannot hold a bare marker line
    (``# `` in ``config.yaml.example``, where everything is a yaml comment)."""
    start = f"{prefix}[//]: # (generated:{name}:start)"
    end = f"{prefix}[//]: # (generated:{name}:end)"
    pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pat.search(text):
        raise SystemExit(f"markers for {name!r} not found")
    return pat.sub(lambda _m: f"{start}\n\n{body}\n\n{end}", text)


# Files whose marker lines carry a comment prefix.
_MARKER_PREFIX = {REPO_ROOT / "config.yaml.example": "# "}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="update the docs in place")
    args = ap.parse_args(argv)
    drift = 0
    for (path, name), render in BLOCKS.items():
        current = path.read_text(encoding="utf-8")
        updated = _splice(current, name, render(), _MARKER_PREFIX.get(path, ""))
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
