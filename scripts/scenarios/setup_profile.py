#!/usr/bin/env python3
"""Create or refresh the Hermes profile the scenario harness runs in.

    uv run --extra dev python scripts/scenarios/setup_profile.py
    uv run --extra dev python scripts/scenarios/setup_profile.py --backends-from default
    uv run --extra dev python scripts/scenarios/setup_profile.py --second-profile scenarios-b \
        --second-provider nvidia --second-model nvidia/nemotron-3-super-120b-a12b \
        --copy-env NVIDIA_API_KEY

What it does, every time (idempotent):

* ``hermes profile create <profile> --no-alias --no-skills`` when the profile
  does not exist yet; the new profile inherits the active profile's ``model:``.
* ``config.yaml``: ``model:`` copied from ``--model-from`` (default: the
  default profile), a small toolset (terminal, file, web, skills), manual
  approvals with a short timeout, ``gateway.standalone: true`` (so a
  multiplexed host gateway never serves this profile; the harness starts its
  own) and the plugin enabled.
* the working tree's ``hermes_otel/`` copied to ``<profile>/plugins/hermes_otel``,
  so a run tests the code in this checkout.
* ``hermes_otel.yaml``: live store and logs on; ``backends:`` and
  ``query_backend`` copied from ``--backends-from`` when given (with any
  ``${VAR}`` the entries name, copied from that profile's ``.env``; nothing
  else, so no bot token reaches the harness profile). Without it the profile
  is live-store only.
* one bundled skill (``systematic-debugging``) for the skill-load scenario.

``--second-profile`` builds a sibling profile the same way with a different
model, for the two-profile kanban scenario.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from _common import profile_home  # noqa: E402
from common import DEFAULT_PROFILE, REPO, merge, yaml_mod  # noqa: E402

DISABLED_TOOLSETS = [
    "browser",
    "browser-use",
    "delegation",
    "memory",
    "code_execution",
    "tts",
    "vision",
    "clarify",
    "todo",
    "session_search",
    "connections",
]
SKILL = Path("software-development") / "systematic-debugging"
ENV_PREFIXES = ("OTEL_", "HERMES_OTEL_")
ENV_SKIP = {"HERMES_OTEL_DEBUG"}


def read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return yaml_mod().safe_load(path.read_text(encoding="utf-8")) or {}


def write_yaml(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(yaml_mod().safe_dump(data, sort_keys=False), encoding="utf-8")


def read_env(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def ensure_profile(name: str) -> Path:
    home = profile_home(name)
    if not (home / "config.yaml").exists():
        print(f"creating profile {name} …", flush=True)
        subprocess.run(
            [
                "hermes",
                "profile",
                "create",
                name,
                "--no-alias",
                "--no-skills",
                "--description",
                "hermes-otel scenario harness (scripts/scenarios); safe to delete",
            ],
            stdin=subprocess.DEVNULL,
            check=True,
        )
    return home


def install_plugin(home: Path) -> None:
    dest = home / "plugins" / "hermes_otel"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        REPO / "hermes_otel",
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db", "*.db-*", "debug.log"),
    )


def hermes_config(home: Path, model: Dict[str, Any], approvals_timeout: int) -> Dict[str, Any]:
    cfg = read_yaml(home / "config.yaml")
    cfg["model"] = model
    cfg = merge(
        cfg,
        {
            "toolsets": ["terminal", "file", "web", "skills"],
            "agent": {"disabled_toolsets": DISABLED_TOOLSETS},
            "skills": {"external_dirs": []},
            "gateway": {"standalone": True},
            "approvals": {"mode": "manual", "timeout": approvals_timeout},
        },
    )
    plugins = cfg.setdefault("plugins", {})
    enabled = list(plugins.get("enabled") or [])
    if "hermes_otel" not in enabled:
        enabled.append("hermes_otel")
    plugins["enabled"] = enabled
    plugins["disabled"] = [p for p in (plugins.get("disabled") or []) if p != "hermes_otel"]
    return cfg


def otel_config(project: str, backends_from: Optional[Path]) -> tuple[Dict[str, Any], List[str]]:
    """The plugin config, plus the env var names its backends need."""
    cfg: Dict[str, Any] = {
        "project_name": project,
        "logs": {"capture": True, "level": "INFO"},
        # A full run writes a few thousand log rows; the default cap (1000 per
        # kind) would evict the first scenarios' records before check.py reads them.
        "dashboard_live_max_spans": 20000,
    }
    needed: List[str] = []
    if backends_from is not None:
        src = read_yaml(backends_from / "hermes_otel.yaml")
        if src.get("backends"):
            cfg["backends"] = src["backends"]
            text = yaml_mod().safe_dump(src["backends"])
            needed = sorted(set(re.findall(r"\$\{([A-Z0-9_]+)", text)))
        if src.get("query_backend"):
            cfg["query_backend"] = src["query_backend"]
    return cfg, needed


def write_env(home: Path, sources: List[tuple]) -> List[str]:
    """Copy variables into the harness profile's .env (its other lines are
    kept). ``sources`` is ``[(profile home, names, take_otel_prefixed)]``:
    the backends' profile gives the ``${VAR}`` names its entries use,
    ``--copy-env`` names come from the model profile. Nothing else is copied:
    no bot token, and no ``OTEL_*`` variable that would switch on an
    env-mode exporter the harness did not ask for."""
    picked: Dict[str, str] = {}
    for source, names, prefixed in sources:
        if source is None:
            continue
        for k, v in read_env(source / ".env").items():
            if (
                v
                and k not in ENV_SKIP
                and (k in names or (prefixed and k.startswith(ENV_PREFIXES)))
            ):
                picked[k] = v
    if not picked:
        return []
    path = home / ".env"
    current = read_env(path)
    current.update(picked)
    path.write_text("".join(f"{k}={v}\n" for k, v in current.items()), encoding="utf-8")
    return sorted(picked)


def copy_skill(home: Path, source: Path) -> bool:
    for base in (source / "skills", profile_home("minimal") / "skills"):
        src = base / SKILL
        if src.is_dir():
            dest = home / "skills" / SKILL
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest)
            return True
    return False


def build(
    name: str,
    model: Dict[str, Any],
    *,
    project: str,
    backends_from: Optional[Path],
    env_source: Path,
    copy_env: List[str],
    approvals_timeout: int,
    skill_source: Path,
) -> None:
    home = ensure_profile(name)
    write_yaml(home / "config.yaml", hermes_config(home, model, approvals_timeout))
    install_plugin(home)
    otel, needed = otel_config(project, backends_from)
    write_yaml(home / "hermes_otel.yaml", otel)
    copied = write_env(home, [(backends_from, needed, False), (env_source, copy_env, False)])
    skill = copy_skill(home, skill_source)
    print(f"profile {name}: {home}")
    print(f"  model     {model.get('provider')} / {model.get('default')}")
    print(f"  plugin    {REPO / 'hermes_otel'} → plugins/hermes_otel")
    backends = [b.get("name") or b.get("type") for b in otel.get("backends") or []]
    print(f"  backends  {', '.join(backends) if backends else 'none (live store only)'}")
    if copied:
        print(f"  .env      {', '.join(copied)}")
    if not skill:
        print(f"  skill     {SKILL} not found; the skill-load scenario will fail")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--model-from", default="default", help="profile whose model: block to use")
    ap.add_argument(
        "--backends-from",
        default=None,
        help="profile whose hermes_otel.yaml backends to copy (default: none, live store only)",
    )
    ap.add_argument("--approvals-timeout", type=int, default=20)
    ap.add_argument("--second-profile", default=None, help="also build this sibling profile")
    ap.add_argument("--second-provider", default=None)
    ap.add_argument("--second-model", default=None)
    ap.add_argument("--second-base-url", default=None)
    ap.add_argument(
        "--copy-env",
        action="append",
        default=[],
        help="an env var to copy from --model-from's .env (repeatable; e.g. NVIDIA_API_KEY)",
    )
    args = ap.parse_args(argv)

    if shutil.which("hermes") is None:
        sys.exit("`hermes` is not on PATH")
    source = profile_home(args.model_from)
    model = read_yaml(source / "config.yaml").get("model")
    if not isinstance(model, dict) or not model.get("default"):
        sys.exit(f"no model: block in {source / 'config.yaml'}")
    backends_from = profile_home(args.backends_from) if args.backends_from else None
    common = dict(
        backends_from=backends_from,
        approvals_timeout=args.approvals_timeout,
        skill_source=source,
    )
    build(
        args.profile,
        dict(model),
        project="hermes-scenarios",
        env_source=source,
        copy_env=args.copy_env,
        **common,
    )
    if args.second_profile:
        second = dict(model)
        if args.second_model:
            second = {"default": args.second_model, "provider": args.second_provider or "custom"}
            if args.second_base_url:
                second["base_url"] = args.second_base_url
        build(
            args.second_profile,
            second,
            project="hermes-scenarios",
            env_source=source,
            copy_env=args.copy_env,
            **common,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
