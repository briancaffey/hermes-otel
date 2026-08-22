"""Guards on the published install artifact — the ``hermes_otel/`` directory.

``hermes plugins install briancaffey/hermes-otel/hermes_otel`` copies *only*
this directory into ``~/.hermes/plugins/hermes_otel/``. Anything a hook touches
at runtime must therefore live inside it, and the dependency list users are
told to install must match what the package actually declares.

Both mistakes are silent: the test suite imports from the repo, so a module
left at the repo root, or a dependency added to ``pyproject.toml`` only, passes
every other test and fails on a user's machine. See issue #53.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ARTIFACT = REPO_ROOT / "hermes_otel"


def _pyproject_dependencies() -> list:
    """Parse ``[project] dependencies`` without requiring a TOML lib on 3.9/3.10."""
    text = (REPO_ROOT / "pyproject.toml").read_text()
    block = re.search(r"^dependencies = \[(.*?)^\]", text, re.S | re.M)
    assert block, "could not find [project] dependencies in pyproject.toml"
    return re.findall(r'"([^"]+)"', block.group(1))


def _requirements() -> list:
    lines = (ARTIFACT / "requirements.txt").read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def _manifest_python_dependencies() -> list:
    """Read ``python_dependencies`` from plugin.yaml without a yaml dependency."""
    text = (ARTIFACT / "plugin.yaml").read_text()
    block = re.search(r"^python_dependencies:\n((?:  - .*\n)+)", text, re.M)
    assert block, "plugin.yaml declares no python_dependencies"
    return [ln.strip()[2:].strip() for ln in block.group(1).splitlines()]


class TestArtifactContents:
    """Everything the installed plugin needs must ship inside ``hermes_otel/``."""

    @pytest.mark.parametrize(
        "rel",
        [
            "__init__.py",
            "plugin.yaml",
            "requirements.txt",
            "after-install.md",
            "hooks.py",
            "tracer.py",
            "span_tracker.py",
            "session_state.py",
            "helpers.py",
            "plugin_config.py",
            "backends.py",
            "live_store.py",
            "log_handler.py",
            "debug_utils.py",
            "langsmith_backend.py",
            # Registered as the `hermes_otel:observability` skill by register().
            "skills/observability/SKILL.md",
            # Dashboard tab, loaded by the separate `hermes dashboard` process.
            "dashboard/manifest.json",
            "dashboard/plugin_api.py",
            "dashboard/backends/__init__.py",
            "dashboard/dist/index.js",
            "dashboard/dist/style.css",
        ],
    )
    def test_required_file_ships(self, rel):
        assert (ARTIFACT / rel).is_file(), f"{rel} is missing from the install artifact"

    def test_no_runtime_module_left_at_repo_root(self):
        """A module at the repo root would import in tests but not once installed."""
        stray = sorted(p.name for p in REPO_ROOT.glob("*.py"))
        assert stray == [], (
            f"Python modules at the repo root are not installed: {stray}. "
            "Runtime code belongs in hermes_otel/."
        )

    def test_register_resolves_the_bundled_skill(self):
        """``__init__.register`` locates SKILL.md relative to the package."""
        import hermes_otel

        pkg_dir = Path(hermes_otel.__file__).resolve().parent
        assert (pkg_dir / "skills" / "observability" / "SKILL.md").is_file()


class TestDeclaredDependencies:
    """One dependency list, three places users can read it."""

    def test_requirements_matches_pyproject(self):
        assert _requirements() == _pyproject_dependencies()

    def test_manifest_matches_pyproject(self):
        assert _manifest_python_dependencies() == _pyproject_dependencies()
