#!/usr/bin/env python3
"""Live before/after check for refactor PRs (#410).

    make refactor-check                      # this branch vs main, profile minimal
    make refactor-check BASE=origin/main
    uv run --extra dev python scripts/refactor_check/refactor_check.py --only shell-echo,no-tools

For the working tree and for ``--base``, in turn: install that side's
``hermes_otel/`` package into the profile's plugin directory (the original is
backed up and restored afterwards), run the scenario harness's cli turns through a real
Hermes, score every telemetry source with ``scenarios/check.py`` (live store and
each configured backend through the plugin's own query CLI), and dump the live
store's spans, metric names and logs for the batch in a normalised form. Then
diff the two sides.

What is compared is the *shape* of the telemetry, not the values a model
produces: span names, parentage, status, the attribute keys each span carries,
and the values of the attributes that are stable from run to run (tool names,
operation names, outcomes, turn numbers). Durations, token counts, cost,
prompts and answers vary between two runs of the same code and are left out;
``allowlist.yaml`` can exclude more, with a reason per entry. A difference not
in the allowlist fails the run.

One difference is recognised rather than allowlisted (#552): at the end of a
cli turn Hermes fires an auxiliary API call (title generation) that races
``on_session_end``. When it loses, the turn's root lacks
``hermes.turn.auxiliary.*``, the auxiliary ``api.*`` span is closed by the
leftover sweep (``hermes.span.ended_by`` / ``hermes.span.sweep_reason`` and no
response attributes) and ``hermes.span.swept.count`` is recorded; when it
wins, the span carries the full response. Either outcome happens with the
same plugin code, so when a prompt's two traces differ in exactly that way
(one auxiliary ``api.*`` span swept on one side and answered on the other,
the root differing only by ``hermes.turn.auxiliary.*``, nothing else) the
verdict reports an "auxiliary race" line instead of differences and the run
does not fail on it. Any other difference in that trace still fails.

The dashboard's Playwright checks (``scripts/dashboard_verify``) are not part
of this script; run them separately when a PR touches the dashboard.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SCENARIOS = REPO / "scripts" / "scenarios"
sys.path.insert(0, str(REPO / "scripts"))
from _common import profile_home  # noqa: E402

DEFAULT_PROMPTS = "shell-echo,no-tools,missing-file,multi-tool"

# Attribute values that do not depend on what the model said or how long it
# took; everything else is compared by key presence only.
STABLE_VALUE_KEYS = frozenset(
    {
        "openinference.span.kind",
        "traceloop.span.kind",
        "hermes.span_kind",
        "hermes.platform",
        "hermes.session.kind",
        "hermes.session.completed",
        "hermes.session.failed",
        "hermes.session.interrupted",
        "hermes.turn.number",
        "hermes.turn.final_status",
        "hermes.tool.outcome",
        "hermes.query_source",
        "hermes.skill.name",
        "hermes.cost.status",
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.tool.name",
        "llm.api_mode",
        "llm.model_name",
        "llm.provider",
        "tool.name",
        "error.type",
    }
)
# Keys that exist only to hold a per-run identity; dropped before the key set
# is compared (their presence is still visible through the span's shape).
IDENTITY_KEYS = frozenset(
    {
        "session.id",
        "session_id",
        "hermes.session_id",
        "gen_ai.conversation.id",
        "gen_ai.tool.call.id",
    }
)
PRESERVE_IN_PLUGIN_DIR = (".install-metadata.json", "config.yaml", ".env", "debug.log")

# The auxiliary race (#552, see the module docstring): what the sweep leaves on
# an api.* span it closes, what a response adds, what the root gains when the
# auxiliary call lands in time, and the metric the sweep records.
SWEEP_KEYS = frozenset({"hermes.span.ended_by", "hermes.span.sweep_reason"})
RESPONSE_KEY_PREFIXES = (
    "gen_ai.response.",
    "gen_ai.usage.",
    "gen_ai.output.",
    "llm.response.",
    "llm.token_count.",
    "output.",
    "hermes.cost.",
    "hermes.content.output_chars",
)
AUXILIARY_ROOT_PREFIX = "hermes.turn.auxiliary."
SWEPT_METRIC = "hermes.span.swept.count"


# ── profile plumbing (same rules as scripts/scenarios) ─────────────────


def batches_root(profile: str) -> Path:
    """Where scripts/scenarios/run.py writes ``<token>/runs.json``."""
    return profile_home(profile) / "scenario_runs"


# ── materialising one side's package ───────────────────────────────────


def materialise(ref: Optional[str], dest: Path) -> str:
    """Copy ``hermes_otel/`` of ``ref`` (a git ref) or of the working tree
    (``None``) into ``dest``; returns a label for the report."""
    dest.mkdir(parents=True, exist_ok=True)
    if ref is None:
        files = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-co", "--exclude-standard", "hermes_otel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split("\n")
        for rel in files:
            if not rel or "__pycache__" in rel:
                continue
            src = REPO / rel
            if src.is_file():
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
        head = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(REPO), "status", "--porcelain", "hermes_otel"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        return f"working tree @ {head}{' (modified)' if dirty else ''}"
    archive = subprocess.run(
        ["git", "-C", str(REPO), "archive", ref, "hermes_otel"], check=True, capture_output=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
    sha = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short", ref], capture_output=True, text=True
    ).stdout.strip()
    return f"{ref} @ {sha}"


def install_package(package_dir: Path, plugin_dir: Path) -> None:
    """Replace the installed plugin's files with ``package_dir/hermes_otel``,
    keeping the per-install files the profile owns."""
    src = package_dir / "hermes_otel"
    if not (src / "plugin.yaml").exists():
        raise SystemExit(f"no plugin.yaml under {src}")
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for child in list(plugin_dir.iterdir()):
        if child.name in PRESERVE_IN_PLUGIN_DIR:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for path in src.rglob("*"):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(src)
        target = plugin_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def backup_plugin(plugin_dir: Path, backup: Path) -> None:
    if backup.exists():
        shutil.rmtree(backup)
    shutil.copytree(plugin_dir, backup, ignore=shutil.ignore_patterns("__pycache__"))


def restore_plugin(backup: Path, plugin_dir: Path) -> None:
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)
    shutil.copytree(backup, plugin_dir)


# ── running one side ───────────────────────────────────────────────────


def run_batch(
    profile: str, token: str, prompts: str, hermes_args: Sequence[str], log: Path
) -> Path:
    cmd = [
        sys.executable,
        str(SCENARIOS / "run.py"),
        "--profile",
        profile,
        "--token",
        token,
        "--out",
        str(batches_root(profile) / token),
        "--only",
        prompts,
    ]
    if hermes_args:
        cmd += ["--", *hermes_args]
    with log.open("w", encoding="utf-8") as fh:
        subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, check=False)
    manifest = batches_root(profile) / token / "runs.json"
    if not manifest.exists():
        raise SystemExit(f"scenarios/run.py produced no manifest at {manifest}; see {log}")
    return manifest


def check_batch(manifest: Path, wait: float, log: Path) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(SCENARIOS / "check.py"),
        "--manifest",
        str(manifest),
        "--wait",
        str(wait),
    ]
    with log.open("w", encoding="utf-8") as fh:
        subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, check=False)
    report = manifest.parent / "report.json"
    return json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}


# ── the live store, normalised ─────────────────────────────────────────


def _marker_id(token: str, spans: Iterable[Dict[str, Any]]) -> Optional[str]:
    # scenarios/run.py appends "(scenario marker: <token>-<id>)" to every
    # prompt; the id itself may contain dashes, so the closing parenthesis
    # delimits it.
    tagged = re.compile(r"scenario marker: " + re.escape(token) + r"-([a-z0-9][a-z0-9-]*)\)")
    loose = re.compile(re.escape(token) + r"-([a-z0-9][a-z0-9-]*)")
    for pat in (tagged, loose):
        for sp in spans:
            for v in (sp.get("attributes") or {}).values():
                if isinstance(v, str):
                    m = pat.search(v)
                    if m:
                        return m.group(1)
    return None


def normalise_span(span: Dict[str, Any], by_id: Dict[str, Dict[str, Any]], ignore: Iterable[str]):
    attrs = span.get("attributes") or {}
    skip = set(ignore) | IDENTITY_KEYS
    parent = by_id.get(span.get("parent_span_id") or "")
    return {
        "name": span.get("name"),
        "parent": parent.get("name") if parent else None,
        "status": span.get("status"),
        "keys": sorted(k for k in attrs if k not in skip),
        "values": {k: attrs[k] for k in sorted(attrs) if k in STABLE_VALUE_KEYS and k not in skip},
    }


def batch_started_ns(manifest: Path) -> int:
    """When the batch started (``started_unix`` in ``runs.json``), in ns; 0 when unknown."""
    try:
        started = json.loads(manifest.read_text(encoding="utf-8")).get("started_unix")
    except (OSError, ValueError):
        return 0
    return int(float(started) * 1_000_000_000) if started else 0


def dump_live(profile: str, token: str, ignore: Iterable[str], since_ns: int = 0) -> Dict[str, Any]:
    """Every trace the batch produced, as shapes keyed by prompt id, and the
    metric names recorded since ``since_ns`` (the batch start).

    The live store keeps the last ``dashboard_live_max_spans`` rows of each
    kind for the whole profile, so a DB-wide metric-name list flaps as earlier
    sessions' points are evicted between the two sides (#532); only names whose
    newest point falls inside the batch count.
    """
    sys.path.insert(0, str(REPO))
    from hermes_otel.live_store import LIVE_DB_FILENAME, LiveStore

    store = LiveStore(db_path=str(profile_home(profile) / LIVE_DB_FILENAME))
    try:
        page = store.query_traces(text=token, limit=200)
        ids = [t["traceId"] for t in page.get("traces", [])]
        by_trace = store.spans_for_traces(ids)
        out: Dict[str, Any] = {"traces": {}, "metrics": [], "logs": {}}
        for tid, spans in by_trace.items():
            pid = _marker_id(token, spans) or tid
            by_id = {s.get("span_id"): s for s in spans}
            shapes = sorted(
                (normalise_span(s, by_id, ignore) for s in spans),
                key=lambda d: (d["name"] or "", d["parent"] or "", json.dumps(d["values"])),
            )
            out["traces"].setdefault(pid, []).append(shapes)
            logs = store.query_logs(trace_id=tid, limit=500)
            levels = sorted({str(r.get("severity_text") or r.get("level") or "?") for r in logs})
            out["logs"][pid] = {"levels": levels, "attributed": bool(logs)}
        out["metrics"] = sorted(
            m["name"] for m in store.metric_names() if int(m.get("lastTs") or 0) >= since_ns
        )
        return out
    finally:
        store.close()


# ── comparing the two sides ────────────────────────────────────────────


def _matrix(report: Dict[str, Any]) -> Dict[str, str]:
    """``{"<prompt>/<source>": PASS|FAIL|SKIP|...}`` from a scenarios/check.py
    report (a turn's cells, or a pre-harness report's per-run cells)."""
    out: Dict[str, str] = {}
    for run in report.get("runs", []):
        cells: Dict[str, Any] = dict(run.get("sources") or {})
        for turn in run.get("turns") or []:
            cells.update(turn.get("sources") or {})
        for source, cell in cells.items():
            findings = cell.get("findings") or []
            verdict = (
                "FAIL" if any(f[0] == "FAIL" for f in findings) else (cell.get("verdict") or "PASS")
            )
            out[f"{run.get('id')}/{source}"] = str(verdict)
    return out


def _same_shape(sa: Dict[str, Any], sb: Dict[str, Any], skip_keys: Set[str]) -> bool:
    """What ``compare`` checks on a span pair, as one answer."""
    return (
        sa["parent"] == sb["parent"]
        and sa["status"] == sb["status"]
        and set(sa["keys"]) - skip_keys == set(sb["keys"]) - skip_keys
        and all(sa["values"][k] == sb["values"][k] for k in set(sa["values"]) & set(sb["values"]))
    )


def _auxiliary_race(
    base_shapes: List[Dict[str, Any]], branch_shapes: List[Dict[str, Any]], skip_keys: Set[str]
) -> Optional[str]:
    """The side (``"base"`` / ``"branch"``) whose auxiliary ``api.*`` span was
    closed by the sweep while the other side's got its response (#552), or
    ``None`` when the two traces differ in any other way, or not at all.

    Exactly one ``api.*`` span may differ, by the sweep marks on one side
    against the response attributes on the other, with ``hermes.query_source``
    ``auxiliary`` on both; the root may differ by ``hermes.turn.auxiliary.*``
    keys, missing on the swept side; every other span is the same shape.
    """
    if [s["name"] for s in base_shapes] != [s["name"] for s in branch_shapes]:
        return None
    swept: Optional[str] = None
    root_lacking: Optional[str] = None
    for sa, sb in zip(base_shapes, branch_shapes):
        if _same_shape(sa, sb, skip_keys):
            continue
        ka, kb = set(sa["keys"]) - skip_keys, set(sb["keys"]) - skip_keys
        same_otherwise = _same_shape({**sa, "keys": []}, {**sb, "keys": []}, skip_keys)
        if sa["parent"] is None and sa["name"] == "agent":
            if not same_otherwise or not all(k.startswith(AUXILIARY_ROOT_PREFIX) for k in ka ^ kb):
                return None
            root_lacking = "base" if ka < kb else "branch" if kb < ka else None
            if root_lacking is None:
                return None
            continue
        if swept is not None or not sa["name"].startswith("api.") or not same_otherwise:
            return None
        if sa["values"].get("hermes.query_source") != "auxiliary":
            return None
        if SWEEP_KEYS <= ka and not (SWEEP_KEYS & kb):
            swept, swept_keys, answered_keys = "base", ka, kb
        elif SWEEP_KEYS <= kb and not (SWEEP_KEYS & ka):
            swept, swept_keys, answered_keys = "branch", kb, ka
        else:
            return None
        response = answered_keys - swept_keys
        if swept_keys - answered_keys != SWEEP_KEYS or not response:
            return None
        if not all(k.startswith(RESPONSE_KEY_PREFIXES) for k in response):
            return None
    if swept is None or (root_lacking is not None and root_lacking != swept):
        return None
    return swept


def compare(
    base: Dict[str, Any], branch: Dict[str, Any], allow: Dict[str, Any]
) -> Tuple[List[str], List[str]]:
    """Human-readable differences that fail the run, and the tolerated lines
    reported next to them (auxiliary races, #552); both empty when the sides
    agree."""
    diffs: List[str] = []
    tolerated: List[str] = []
    swept_sides: Set[str] = set()
    skip_prompts = set(allow.get("tolerate_prompts") or [])
    skip_metrics = set(allow.get("ignore_metrics") or [])
    # Applied here as well as at dump time, so --recompare honours an allowlist
    # entry added after the sides were recorded.
    skip_keys = set(allow.get("ignore_attribute_keys") or [])
    for pid in sorted(set(base["live"]["traces"]) | set(branch["live"]["traces"])):
        if pid in skip_prompts:
            continue
        a = base["live"]["traces"].get(pid)
        b = branch["live"]["traces"].get(pid)
        if a is None or b is None:
            diffs.append(f"{pid}: traces only on {'branch' if a is None else 'base'}")
            continue
        if len(a) != len(b):
            diffs.append(f"{pid}: {len(a)} trace(s) on base, {len(b)} on branch")
        for ta, tb in zip(a, b):
            swept = _auxiliary_race(ta, tb, skip_keys)
            if swept:
                swept_sides.add(swept)
                tolerated.append(
                    f"{pid}: auxiliary race (#552): the auxiliary api.* span was swept on "
                    f"{swept} and answered on the other side; the root lacks "
                    f"hermes.turn.auxiliary.* on {swept}"
                )
                continue
            names_a = [s["name"] for s in ta]
            names_b = [s["name"] for s in tb]
            if names_a != names_b:
                diffs.append(f"{pid}: span names base={names_a} branch={names_b}")
                continue
            for sa, sb in zip(ta, tb):
                for field in ("parent", "status"):
                    if sa[field] != sb[field]:
                        diffs.append(
                            f"{pid}/{sa['name']}: {field} base={sa[field]!r} branch={sb[field]!r}"
                        )
                gone = sorted(set(sa["keys"]) - set(sb["keys"]) - skip_keys)
                new = sorted(set(sb["keys"]) - set(sa["keys"]) - skip_keys)
                if gone:
                    diffs.append(f"{pid}/{sa['name']}: attributes gone on branch: {gone}")
                if new:
                    diffs.append(f"{pid}/{sa['name']}: attributes new on branch: {new}")
                for k in sorted(set(sa["values"]) & set(sb["values"])):
                    if sa["values"][k] != sb["values"][k]:
                        diffs.append(
                            f"{pid}/{sa['name']}: {k} base={sa['values'][k]!r} branch={sb['values'][k]!r}"
                        )
        la, lb = base["live"]["logs"].get(pid, {}), branch["live"]["logs"].get(pid, {})
        if la.get("attributed") != lb.get("attributed"):
            diffs.append(
                f"{pid}: trace-attributed logs base={la.get('attributed')} branch={lb.get('attributed')}"
            )
    ma = set(base["live"]["metrics"]) - skip_metrics
    mb = set(branch["live"]["metrics"]) - skip_metrics
    # The sweep that closed a raced span is the only thing recording
    # hermes.span.swept.count on that side; on the other side it is a difference.
    for side, mine, other in (("base", ma, mb), ("branch", mb, ma)):
        if side in swept_sides and SWEPT_METRIC in mine and SWEPT_METRIC not in other:
            mine.discard(SWEPT_METRIC)
            tolerated.append(f"metric names: {SWEPT_METRIC} only on {side}, from the race above")
    if ma != mb:
        diffs.append(f"metric names: gone={sorted(ma - mb)} new={sorted(mb - ma)}")
    xa, xb = _matrix(base.get("check") or {}), _matrix(branch.get("check") or {})
    for cell in sorted(set(xa) | set(xb)):
        if cell.split("/", 1)[0] in skip_prompts:
            continue
        if xa.get(cell) != xb.get(cell):
            diffs.append(f"check_batch {cell}: base={xa.get(cell)} branch={xb.get(cell)}")
    return diffs, tolerated


def load_allowlist(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def render_report(
    base: Dict[str, Any],
    branch: Dict[str, Any],
    diffs: List[str],
    out: Path,
    tolerated: Sequence[str] = (),
) -> str:
    lines = [
        "# refactor-check",
        "",
        f"- base: `{base['label']}` (batch `{base['token']}`)",
        f"- branch: `{branch['label']}` (batch `{branch['token']}`)",
        f"- profile: `{branch['profile']}`, prompts: `{branch['prompts']}`",
        "",
        "## Verdict",
        "",
        "No differences outside the allowlist." if not diffs else f"{len(diffs)} difference(s):",
    ]
    if diffs:
        lines += ["", *[f"- {d}" for d in diffs]]
    if tolerated:
        lines += ["", "Tolerated, not failing the run:", "", *[f"- {t}" for t in tolerated]]
    lines += ["", "## check_batch matrices", ""]
    for side in (base, branch):
        rep = side.get("check_report_md")
        lines += [f"### {side['label']}", "", rep.strip() if rep else "(no report)", ""]
    text = "\n".join(lines) + "\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    return text


# ── main ───────────────────────────────────────────────────────────────


def run_side(
    label: str,
    ref: Optional[str],
    *,
    profile: str,
    prompts: str,
    wait: float,
    hermes_args: Sequence[str],
    out: Path,
    ignore: Iterable[str],
    stamp: str,
) -> Dict[str, Any]:
    side_dir = out / label
    side_dir.mkdir(parents=True, exist_ok=True)
    plugin_dir = profile_home(profile) / "plugins" / "hermes_otel"
    with tempfile.TemporaryDirectory(prefix="refactor-check-") as tmp:
        desc = materialise(ref, Path(tmp))
        print(f"[{label}] {desc}", flush=True)
        install_package(Path(tmp), plugin_dir)
    token = f"R{stamp}-{label}"
    print(f"[{label}] running {prompts} as batch {token} …", flush=True)
    manifest = run_batch(profile, token, prompts, hermes_args, side_dir / "run_batch.log")
    print(f"[{label}] checking every source …", flush=True)
    report = check_batch(manifest, wait, side_dir / "check_batch.log")
    live = dump_live(profile, token, ignore, since_ns=batch_started_ns(manifest))
    result = {
        "label": desc,
        "token": token,
        "profile": profile,
        "prompts": prompts,
        "manifest": str(manifest),
        "check": report,
        "check_report_md": (
            (manifest.parent / "report.md").read_text(encoding="utf-8")
            if (manifest.parent / "report.md").exists()
            else ""
        ),
        "live": live,
    }
    (side_dir / "side.json").write_text(
        json.dumps(result, indent=1, sort_keys=True), encoding="utf-8"
    )
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base", default="main", help="git ref to compare against (default main)")
    ap.add_argument("--profile", default="minimal", help="Hermes profile whose plugin dir is used")
    ap.add_argument("--only", default=DEFAULT_PROMPTS, help="comma-separated prompt ids")
    ap.add_argument(
        "--wait", type=float, default=45.0, help="ingestion wait before querying backends"
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="report directory (default under scripts/refactor_check/out)",
    )
    ap.add_argument(
        "--base-run",
        type=Path,
        default=None,
        help="reuse a previous base side.json instead of re-running it",
    )
    ap.add_argument("--allowlist", type=Path, default=HERE / "allowlist.yaml")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    ap.add_argument(
        "--recompare",
        type=Path,
        default=None,
        help="re-score a finished run directory (base/side.json + branch/side.json) without Hermes",
    )
    ap.add_argument(
        "hermes_args", nargs="*", help="extra arguments after `--`, passed to hermes chat"
    )
    args = ap.parse_args(argv)

    branch_name = (
        subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        or "branch"
    )
    stamp = dt.datetime.now().strftime("%y%m%d-%H%M%S")
    out = args.out or (
        HERE / "out" / f"{branch_name.replace('/', '-')}-vs-{args.base.replace('/', '-')}-{stamp}"
    )
    plugin_dir = profile_home(args.profile) / "plugins" / "hermes_otel"
    if args.dry_run:
        print(
            f"branch {branch_name} vs {args.base}; profile {args.profile} ({plugin_dir}); prompts {args.only}; out {out}"
        )
        return 0
    allow = load_allowlist(args.allowlist)
    if args.recompare:
        base = json.loads((args.recompare / "base" / "side.json").read_text(encoding="utf-8"))
        branch = json.loads((args.recompare / "branch" / "side.json").read_text(encoding="utf-8"))
        diffs, tolerated = compare(base, branch, allow)
        print(render_report(base, branch, diffs, args.recompare, tolerated=tolerated))
        return 1 if diffs else 0
    if not (plugin_dir / "plugin.yaml").exists():
        sys.exit(f"profile {args.profile!r} has no hermes_otel plugin at {plugin_dir}")
    if shutil.which("hermes") is None:
        sys.exit("`hermes` is not on PATH")
    ignore = allow.get("ignore_attribute_keys") or []
    out.mkdir(parents=True, exist_ok=True)
    backup = out / "plugin-backup"
    backup_plugin(plugin_dir, backup)
    try:
        if args.base_run:
            base = json.loads(Path(args.base_run).read_text(encoding="utf-8"))
            print(f"[base] reusing {args.base_run}")
        else:
            base = run_side(
                "base",
                args.base,
                profile=args.profile,
                prompts=args.only,
                wait=args.wait,
                hermes_args=args.hermes_args,
                out=out,
                ignore=ignore,
                stamp=stamp,
            )
        branch = run_side(
            "branch",
            None,
            profile=args.profile,
            prompts=args.only,
            wait=args.wait,
            hermes_args=args.hermes_args,
            out=out,
            ignore=ignore,
            stamp=stamp,
        )
    finally:
        restore_plugin(backup, plugin_dir)
        print(f"restored {plugin_dir} from the backup")
    diffs, tolerated = compare(base, branch, allow)
    print(render_report(base, branch, diffs, out, tolerated=tolerated))
    print(f"report: {out / 'report.md'}")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
