#!/usr/bin/env python3
"""Render the Hermes plugin-catalog entry for hermes-otel from the shipped manifest.

The catalog (``plugin-catalog/hermes-otel.yaml`` in NousResearch/hermes-agent)
pins an exact commit and repeats the plugin's declared capabilities; every
release needs a sha-bump PR upstream that updates ``sha`` and ``version``
together. This script makes that PR a file copy: it reads
``hermes_otel/plugin.yaml`` (description, provides_hooks, requires_hermes,
version) and writes the entry in the catalog's schema (#133).

    python scripts/render_catalog_entry.py --sha <40-hex> [--version 1.7.0] [-o out.yaml]

``--no-image`` omits the banner line (for a commit that predates
``docs/catalog-banner.png``). Stdlib + PyYAML only, like the upstream validator.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "hermes_otel" / "plugin.yaml"

CATALOG_NAME = "hermes-otel"
REPO_URL = "https://github.com/briancaffey/hermes-otel"
SUBDIR = "hermes_otel"
MAINTAINER = "briancaffey"
TIER = "community"
# Same shelf as hermes-telemetry and tokenwatch; the catalog has no observability category.
CATEGORY = "general"
DOCS_URL = "https://briancaffey.github.io/hermes-otel/"
BANNER_PATH = "docs/catalog-banner.png"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class _Quoted(str):
    """A string the dumper must double-quote (``sha`` and ``version``, per the catalog README)."""


def _represent_quoted(dumper: yaml.SafeDumper, value: _Quoted):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style='"')


yaml.SafeDumper.add_representer(_Quoted, _represent_quoted)


def load_manifest(path: Path = MANIFEST) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: not a mapping")
    return data


def build_entry(
    manifest: dict, *, sha: str, version: str | None = None, image: bool = True
) -> dict:
    if not _SHA_RE.match(sha):
        raise ValueError(f"sha must be the full 40-hex commit, got {sha!r}")
    version = version or str(manifest.get("version") or "")
    if not version:
        raise ValueError("no version: pass --version or set version in plugin.yaml")
    hooks = sorted({str(h) for h in (manifest.get("provides_hooks") or [])})
    entry: dict = {
        "name": CATALOG_NAME,
        "repo": REPO_URL,
        "sha": _Quoted(sha),
        "subdir": SUBDIR,
        "description": " ".join(str(manifest.get("description") or "").split()),
        "maintainer": MAINTAINER,
        "tier": TIER,
        "category": CATEGORY,
    }
    requires_hermes = str(manifest.get("requires_hermes") or "").strip()
    if requires_hermes:
        entry["requires_hermes"] = _Quoted(requires_hermes)
    entry["docs_url"] = DOCS_URL
    entry["version"] = _Quoted(version)
    if image:
        entry["image"] = (
            f"https://raw.githubusercontent.com/briancaffey/hermes-otel/{sha}/{BANNER_PATH}"
        )
    entry["capabilities"] = {
        "provides_tools": [],
        "provides_hooks": hooks,
        "provides_middleware": [],
        # Every backend variable is optional (live-only mode needs none) and
        # requires_env gates loading in Hermes, so it must stay empty.
        "requires_env": [],
    }
    return entry


def render(entry: dict) -> str:
    return yaml.safe_dump(
        entry, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sha", required=True, help="40-hex commit the entry pins")
    parser.add_argument("--version", help="release version label (default: plugin.yaml version)")
    parser.add_argument("--no-image", action="store_true", help="omit the banner image line")
    parser.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    args = parser.parse_args(argv)

    try:
        entry = build_entry(
            load_manifest(), sha=args.sha, version=args.version, image=not args.no_image
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = render(entry)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
