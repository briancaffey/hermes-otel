#!/usr/bin/env python3
"""Run Hermes's own plugin security scanner against this repository.

``hermes plugins install`` clones a plugin repo and scans the result with
``tools/plugin_guard.py`` before anything is moved into ``~/.hermes/plugins``.
A single ``critical`` finding yields a ``dangerous`` verdict, which is a hard
block — ``--force`` does not override it (see issue #53). This script runs that
exact scanner locally and in CI so a regression shows up as a red build instead
of as a user who cannot install the plugin.

Two trees are checked:

``hermes_otel/``
    The install artifact. ``hermes plugins install briancaffey/hermes-otel/hermes_otel``
    scans *only* this subdirectory, so it is the tree that gates installs.

repo root
    Everything else (docs, tests, compose examples). Not scanned by the
    subdirectory install, but kept clean as defense in depth: it is what a
    ``owner/repo`` install would see, and it keeps the sanitized examples from
    silently rotting back to ``dangerous``.

The scanner modules are pure-stdlib and import nothing from Hermes, so they are
fetched straight from the upstream repo at a pinned ref and verified against
``scripts/hermes_scanner.lock.json``. That avoids pulling the multi-GB Hermes
image (and ``pip install hermes-agent`` does not help: PyPI still ships 0.19.0,
which predates the scanner).

Usage::

    python scripts/scan_plugin_artifact.py                 # pinned ref, verify hashes
    python scripts/scan_plugin_artifact.py --ref main      # what upstream ships today
    python scripts/scan_plugin_artifact.py --update-lock   # re-pin after review
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = REPO_ROOT / "scripts" / "hermes_scanner.lock.json"

UPSTREAM = "NousResearch/hermes-agent"
SCANNER_FILES = ("tools/skills_guard.py", "tools/plugin_guard.py")

# The install artifact: the subdirectory `hermes plugins install` unpacks.
ARTIFACT_DIR = "hermes_otel"

SEVERITY_ORDER = ("critical", "high", "medium", "low")
# A `critical` finding blocks installs outright; a `high` one downgrades the
# verdict to `caution`, which prompts the user. Both must stay at zero.
BLOCKING_SEVERITIES = ("critical", "high")


def _fetch(ref: str, rel_path: str) -> bytes:
    url = f"https://raw.githubusercontent.com/{UPSTREAM}/{ref}/{rel_path}"
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (fixed host)
        if resp.status != 200:
            raise RuntimeError(f"GET {url} returned HTTP {resp.status}")
        return resp.read()


def _load_lock() -> Dict[str, object]:
    if not LOCK_PATH.exists():
        raise SystemExit(f"missing lock file: {LOCK_PATH} (run with --update-lock)")
    return json.loads(LOCK_PATH.read_text())


def load_scanner(ref: Optional[str], verify: bool, update_lock: bool, dest: Path):
    """Fetch the guard modules and import ``plugin_guard`` from them."""
    lock = _load_lock()
    ref = ref or str(lock["ref"])
    hashes = dict(lock.get("sha256", {}))  # type: ignore[arg-type]

    pkg = dest / "tools"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")

    fetched: Dict[str, str] = {}
    for rel in SCANNER_FILES:
        blob = _fetch(ref, rel)
        digest = hashlib.sha256(blob).hexdigest()
        fetched[rel] = digest
        if verify and not update_lock:
            expected = hashes.get(rel)
            if expected is None:
                raise SystemExit(f"{rel} is not pinned in {LOCK_PATH.name}")
            if expected != digest:
                raise SystemExit(
                    f"checksum mismatch for {rel} at ref {ref}\n"
                    f"  expected {expected}\n  actual   {digest}\n"
                    "Upstream changed the scanner. Review the diff, then re-pin with "
                    "--update-lock."
                )
        (pkg / Path(rel).name).write_bytes(blob)

    if update_lock:
        LOCK_PATH.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Pinned copy of Hermes's plugin security scanner. Regenerate "
                        "with `python scripts/scan_plugin_artifact.py --ref <tag> "
                        "--update-lock` after reviewing the upstream diff."
                    ),
                    "repo": UPSTREAM,
                    "ref": ref,
                    "sha256": fetched,
                },
                indent=2,
            )
            + "\n"
        )
        print(f"[lock] re-pinned to {ref}")

    sys.path.insert(0, str(dest))
    from tools.plugin_guard import (  # noqa: E402
        scan_plugin,
        should_allow_plugin_install,
    )

    return scan_plugin, should_allow_plugin_install, ref


def export_tracked(source: Path, dest: Path, subdir: Optional[str] = None) -> Path:
    """Copy git-tracked files to ``dest`` — i.e. exactly what a clone contains.

    Untracked build output, virtualenvs and local databases are never part of
    an install, so scanning the raw working tree would report findings a user
    can never hit (and miss the point when it stays quiet).
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"] + ([subdir] if subdir else []),
        cwd=str(source),
        capture_output=True,
        check=True,
    )
    names = [n for n in out.stdout.decode().split("\0") if n]
    if not names:
        raise SystemExit(f"no tracked files found under {subdir or source}")
    for name in names:
        src = source / name
        if not src.is_file():
            continue
        rel = Path(name).relative_to(subdir) if subdir else Path(name)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return dest


def report(result, allowed, reason: str, label: str, show_all: bool) -> None:
    counts = Counter(f.severity for f in result.findings)
    summary = ", ".join(f"{counts[s]} {s}" for s in SEVERITY_ORDER if counts[s])
    print(f"\n=== {label} ===")
    print(
        f"verdict: {result.verdict}  ({len(result.findings)} findings"
        + (f": {summary}" if summary else "")
        + ")"
    )
    print(f"install decision: {reason}")

    shown = [f for f in result.findings if show_all or f.severity in BLOCKING_SEVERITIES]
    if shown:
        print(f"\n{'severity':9s} {'category':20s} {'pattern':26s} location")
        for f in sorted(shown, key=lambda f: (SEVERITY_ORDER.index(f.severity), f.file)):
            print(f"{f.severity:9s} {f.category:20s} {f.pattern_id:26s} {f.file}:{f.line}")
            print(f"{'':9s} └─ {(f.match or '')[:100]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", default=None, help="upstream git ref (default: pinned)")
    ap.add_argument("--update-lock", action="store_true", help="re-pin scanner hashes")
    ap.add_argument("--no-verify", action="store_true", help="skip checksum verification")
    ap.add_argument("--all-findings", action="store_true", help="list medium/low too")
    ap.add_argument(
        "--artifact-only",
        action="store_true",
        help="scan only the install artifact, not the whole repo",
    )
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="hermes-scan-") as tmp:
        tmp_path = Path(tmp)
        scan_plugin, should_allow, ref = load_scanner(
            args.ref, not args.no_verify, args.update_lock, tmp_path / "scanner"
        )
        print(f"[scanner] {UPSTREAM}@{ref}")

        trees: List[Tuple[str, str, Optional[str]]] = [
            (
                f"install artifact — {ARTIFACT_DIR}/",
                f"briancaffey/hermes-otel/{ARTIFACT_DIR}",
                ARTIFACT_DIR,
            )
        ]
        if not args.artifact_only:
            trees.append(("full repository", "briancaffey/hermes-otel", None))

        failures = []
        for label, source_id, subdir in trees:
            staged = export_tracked(REPO_ROOT, tmp_path / (subdir or "repo") / "tree", subdir)
            files = sum(1 for _ in staged.rglob("*") if _.is_file())
            size_kb = sum(f.stat().st_size for f in staged.rglob("*") if f.is_file()) // 1024
            result = scan_plugin(staged, source=source_id)
            allowed, reason = should_allow(result)
            report(
                result,
                allowed,
                reason,
                f"{label}  ({files} files, {size_kb} KB)",
                args.all_findings,
            )
            blocking = [f for f in result.findings if f.severity in BLOCKING_SEVERITIES]
            if result.verdict != "safe" or blocking:
                failures.append(label)

        print()
        if failures:
            print("FAIL: not installable — " + "; ".join(failures))
            print(
                "Every critical/high finding must be resolved at the source. A "
                "`dangerous` verdict cannot be overridden with --force."
            )
            return 1
        print("PASS: both trees scan `safe` — `hermes plugins install` is unblocked.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
