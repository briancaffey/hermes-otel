"""A pip/wheel install is a working way to ship the plugin (#643).

Hermes discovers pip-installed plugins through the ``hermes_agent.plugins``
entry-point group (``ENTRY_POINTS_GROUP`` in ``hermes_cli/plugins_discovery.py``)
and loads the target by name, so the group, the name and the target all have to
match. Container images with a read-only root filesystem bake the plugin in this
way, and some copy the installed package into ``plugins/`` instead, so the wheel
also has to carry every file ``hermes plugins install`` would unpack.
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest
import yaml

tomllib = pytest.importorskip("tomllib")  # 3.11+

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG = REPO_ROOT / "hermes_otel"


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_entry_point_is_in_the_group_hermes_scans():
    groups = _pyproject()["project"]["entry-points"]
    assert set(groups) == {"hermes_agent.plugins"}
    manifest_name = yaml.safe_load((PKG / "plugin.yaml").read_text())["name"]
    # The name is what plugins.enabled lists and what Hermes looks the target up by.
    assert groups["hermes_agent.plugins"] == {manifest_name: "hermes_otel:register"}


def test_entry_point_target_is_register():
    module, _, attr = _pyproject()["project"]["entry-points"]["hermes_agent.plugins"][
        "hermes_otel"
    ].partition(":")
    assert callable(getattr(importlib.import_module(module), attr))


def _shipped() -> set[str]:
    """Files a wheel holds, by setuptools' rules: the modules of each listed
    package plus each package-data glob."""
    setuptools = _pyproject()["tool"]["setuptools"]
    files = set()
    for package in setuptools["packages"]:
        files |= {
            p.relative_to(PKG).as_posix()
            for p in (REPO_ROOT / package.replace(".", "/")).glob("*.py")
        }
    for pattern in setuptools["package-data"]["hermes_otel"]:
        files |= {p.relative_to(PKG).as_posix() for p in PKG.glob(pattern) if p.is_file()}
    return files


def test_wheel_carries_every_tracked_plugin_file():
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "hermes_otel"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    missing = sorted(
        f
        for f in (Path(t).relative_to("hermes_otel").as_posix() for t in tracked)
        if f not in _shipped()
    )
    assert missing == []
