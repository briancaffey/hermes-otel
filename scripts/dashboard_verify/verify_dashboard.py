#!/usr/bin/env python3
"""Verify the hermes-otel dashboard against one or more backends.

    uv run --with playwright --extra dev python scripts/dashboard_verify/verify_dashboard.py \
        --source lgtm-local --profile minimal --run-batch
    uv run --with playwright --extra dev python scripts/dashboard_verify/verify_dashboard.py \
        --source lgtm-local --source openobserve-local --profile minimal --batch B261006-120000

The plan this implements is .claude/skills/hermes-otel-dashboard-verify/SKILL.md.
In short: a fixed, marked workload runs through Hermes (scripts/turn_batch),
the plugin's live store is the oracle, every backend answer through the plugin
API is compared with it (S/T/M/L checks), then headless Chromium drives the
real dashboard tab and compares what the page shows with what the API said
(B checks). One report per source: report.md, report.json and screenshots.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TURN_BATCH = ROOT / "scripts" / "turn_batch"
DEFAULT_PROMPTS = ["shell-echo", "no-tools", "missing-file", "multi-tool"]
SETTLE_S = 120

# ── results ──────────────────────────────────────────────────────────────


@dataclass
class Check:
    id: str
    status: str  # PASS | FAIL | WARN | SKIP
    detail: str = ""


@dataclass
class Report:
    source: str
    checks: List[Check] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def add(self, cid: str, ok: Optional[bool], detail: str = "", soft: bool = False) -> None:
        if ok is None:
            st = "SKIP"
        elif ok:
            st = "PASS"
        else:
            st = "WARN" if soft else "FAIL"
        self.checks.append(Check(cid, st, detail))
        print(f"  [{st:4}] {cid}: {detail[:220]}")

    def failed(self) -> bool:
        return any(c.status == "FAIL" for c in self.checks)

    def counts(self) -> Dict[str, int]:
        return dict(Counter(c.status for c in self.checks))


# ── the dashboard host ───────────────────────────────────────────────────


class Host:
    """The Hermes dashboard: session token + plugin API with the profile attached."""

    def __init__(self, base: str, profile: str, timeout: float = 120.0):
        self.base = base.rstrip("/")
        self.api_base = self.base + "/api/plugins/hermes_otel"
        self.profile = profile if profile not in ("", "default") else ""
        self.timeout = timeout
        html = urllib.request.urlopen(self.base + "/", timeout=30).read().decode("utf-8", "replace")
        m = re.search(r'__HERMES_SESSION_TOKEN__="([^"]+)"', html)
        self.token = m.group(1) if m else ""

    def url(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        q = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        if self.profile and "profile" not in q:
            q["profile"] = self.profile
        return self.api_base + path + ("?" + urllib.parse.urlencode(q) if q else "")

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
        req = urllib.request.Request(self.url(path, params))
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(body)
            except Exception:
                return e.code, {"detail": body[:300]}
        except Exception as e:  # connection errors
            return 0, {"detail": str(e)}

    def page_url(self, **nav: Any) -> str:
        q = {k: v for k, v in nav.items() if v is not None and v != ""}
        if self.profile:
            q["profile"] = self.profile
        return self.base + "/otel?" + urllib.parse.urlencode(q)


# ── the workload ─────────────────────────────────────────────────────────


def load_prompts() -> Dict[str, Dict[str, Any]]:
    import yaml

    doc = yaml.safe_load((TURN_BATCH / "prompts.yaml").read_text())
    return {p["id"]: p for p in doc.get("prompts", [])}


def batch_dir(token: str) -> Path:
    base = (
        Path(os.environ.get("HERMES_HOME", "")).expanduser()
        if os.environ.get("HERMES_HOME")
        else Path.home() / ".hermes"
    )
    return base / "turn_batches" / token


def run_batch(profile: str, only: List[str], token: str) -> Path:
    cmd = [
        sys.executable,
        str(TURN_BATCH / "run_batch.py"),
        "--profile",
        profile,
        "--only",
        ",".join(only),
        "--token",
        token,
    ]
    print("workload:", " ".join(cmd))
    subprocess.run(cmd, check=False, cwd=str(ROOT))
    return batch_dir(token) / "runs.json"


def load_manifest(path: Path) -> List[Dict[str, Any]]:
    doc = json.loads(path.read_text())
    runs = doc.get("runs", doc) if isinstance(doc, dict) else doc
    if isinstance(runs, dict):
        runs = list(runs.values())
    return [r for r in runs if isinstance(r, dict)]


def parse_iso(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def batch_window(runs: List[Dict[str, Any]]) -> Tuple[int, int]:
    starts = [parse_iso(r["started_at"]) for r in runs if r.get("started_at")]
    ends = [
        parse_iso(r["started_at"]) + float(r.get("duration_s") or 0)
        for r in runs
        if r.get("started_at")
    ]
    return int(min(starts)) - 30, int(max(ends)) + 90


# ── row normalisation (mirrors dashboard-ui/src/lib.ts rowFromBackend) ───


def norm_tid(tid: Any) -> str:
    """Trace ids compare as 32 lower-case hex digits (some backends drop leading zeros)."""
    t = str(tid or "").lower()
    return t.zfill(32) if t and len(t) < 32 and all(c in "0123456789abcdef" for c in t) else t


def attrs_of(span: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    raw = span.get("attributes")
    if isinstance(raw, dict):
        return dict(raw)
    for a in raw or []:
        v = a.get("value") or {}
        if "stringValue" in v:
            out[a["key"]] = v["stringValue"]
        elif "intValue" in v:
            out[a["key"]] = int(v["intValue"])
        elif "doubleValue" in v:
            out[a["key"]] = float(v["doubleValue"])
        elif "boolValue" in v:
            out[a["key"]] = bool(v["boolValue"])
        else:
            out[a["key"]] = v
    return out


def row_from_backend(t: Dict[str, Any]) -> Dict[str, Any]:
    sets = t.get("spanSets") or ([t["spanSet"]] if t.get("spanSet") else [])
    spans = (sets[0].get("spans") or []) if sets else []
    attrs: Dict[str, Any] = {}
    for s in spans:
        for k, v in attrs_of(s).items():
            attrs.setdefault(k, v)
    start_ns = int(t.get("startTimeUnixNano") or 0)
    dur = float(t.get("durationMs") or 0)
    name = t.get("rootTraceName") or ""
    return {
        "traceId": norm_tid(t.get("traceID") or t.get("traceId")),
        "rootName": name,
        "startNs": start_ns,
        "durationMs": dur,
        "model": attrs.get("gen_ai.request.model")
        or attrs.get("llm.model_name")
        or attrs.get("gen_ai.response.model"),
        "tokens": attrs.get("gen_ai.usage.total_tokens") or attrs.get("llm.token_count.total"),
        "error": attrs.get("status") == "error" or bool(attrs.get("error.type")),
        "session": attrs.get("hermes.session_id") or attrs.get("session.id"),
        "matched": [s.get("name") for s in spans],
        "attrs": attrs,
    }


def spans_from_detail(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for b in d.get("batches") or d.get("resourceSpans") or []:
        for ss in b.get("scopeSpans") or b.get("instrumentationLibrarySpans") or []:
            for s in ss.get("spans") or []:
                st = s.get("status") or {}
                code = st.get("code") if isinstance(st, dict) else st
                out.append(
                    {
                        "span_id": s.get("spanId") or s.get("span_id"),
                        "parent": s.get("parentSpanId") or s.get("parent_span_id") or "",
                        "name": s.get("name"),
                        "error": code in (2, "2", "STATUS_CODE_ERROR", "ERROR", "error"),
                        "attrs": attrs_of(s),
                    }
                )
    return out


def live_spans(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "span_id": s.get("span_id"),
            "parent": s.get("parent_span_id") or "",
            "name": s.get("name"),
            "error": str(s.get("status") or "").upper() == "ERROR",
            "attrs": s.get("attributes") or {},
        }
        for s in d.get("spans") or []
    ]


def series_totals(resp: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for label, vals in (resp.get("series") or {}).items():
        out[label] = round(sum(float(v) for v in vals if v is not None), 6)
    return out


def close(a: Any, b: Any, abs_tol: float, rel_tol: float = 0.0) -> bool:
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(fa - fb) <= max(abs_tol, rel_tol * max(abs(fa), abs(fb)))


def resolve_instrument(names: List[Dict[str, Any]], otlp: str, prefer: str = "") -> Optional[str]:
    matches = [n["name"] for n in names if n.get("otlp_name") == otlp or n.get("name") == otlp]
    if not matches:
        return None
    if prefer:
        for n in matches:
            if n.endswith(prefer) or n.endswith(prefer.replace("_", ".")):
                return n
    for n in matches:
        if n == otlp:
            return n
    for n in matches:
        if not re.search(r"[_.](sum|count|bucket)$", n):
            return n
    return matches[0]


# ── the verification ─────────────────────────────────────────────────────


class Verifier:
    def __init__(
        self,
        host: Host,
        source: str,
        runs: List[Dict[str, Any]],
        prompts: Dict[str, Any],
        out: Path,
        expect: Dict[str, Any],
    ):
        self.h = host
        self.src = source
        self.live = source == "live"
        self.runs = runs
        self.prompts = prompts
        self.out = out
        self.expect = expect
        self.r = Report(source)
        self.start_s, self.end_s = batch_window(runs)
        self.entry: Dict[str, Any] = {}
        self.filters: Dict[str, str] = {}
        self.oracle: Dict[str, Dict[str, Any]] = {}  # marker -> live facts
        self.found: Dict[str, Dict[str, Any]] = {}  # marker -> backend row

    # ── helpers
    def api(self, path: str, **params: Any) -> Tuple[int, Any]:
        if not self.live:
            params.setdefault("backend", self.src)
        return self.h.get(path, params)

    def tpath(self, p: str) -> str:
        return "/live" + p if self.live else p

    def window(self) -> Dict[str, int]:
        return {"start_s": self.start_s, "end_s": self.end_s}

    def level(self, key: str) -> str:
        return "server" if self.live else self.filters.get(key, "none")

    # ── S
    def check_status(self) -> bool:
        code, st = self.h.get("/status")
        if code != 200:
            self.r.add("S1", False, f"/status answered {code}: {str(st)[:200]}")
            return False
        if self.live:
            code, ls = self.h.get("/live/status")
            self.r.add("S1", code == 200 and bool(ls.get("live")), f"live store: {str(ls)[:160]}")
            self.r.add("S2", None, "live source declares no filters")
        else:
            entry = next((b for b in st.get("available", []) if b.get("name") == self.src), None)
            if not entry:
                self.r.add(
                    "S1",
                    False,
                    f"{self.src} not in /status.available: {[b.get('name') for b in st.get('available', [])]}",
                )
                return False
            self.entry = entry
            self.filters = entry.get("filters") or {}
            want = self.expect.get(entry.get("type"), {})
            ok = bool(entry.get("supported")) and (
                not want
                or (
                    bool(entry.get("metrics")) == bool(want.get("metrics"))
                    and bool(entry.get("logs")) == bool(want.get("logs"))
                )
            )
            self.r.add(
                "S1",
                ok,
                f"type={entry.get('type')} supported={entry.get('supported')} metrics={entry.get('metrics')} logs={entry.get('logs')} (expected metrics={want.get('metrics')} logs={want.get('logs')})",
            )
            keys = (
                "service",
                "name",
                "model",
                "session",
                "tool",
                "min_duration",
                "status_error",
                "status_ok",
                "free_text",
                "raw",
                "roots_only",
            )
            missing = [k for k in keys if self.filters.get(k) not in ("server", "client", "none")]
            self.r.add(
                "S2",
                not missing,
                f"filters={self.filters}" if not missing else f"missing/invalid: {missing}",
            )
        # S3 taxonomy
        parts = []
        okay = True
        code, body = self.api(self.tpath("/traces/0000000000000000000000000000dead"))
        good = code == 404 and (self.live or (body or {}).get("kind") == "not_found")
        okay &= good
        parts.append(
            f"unknown trace → {code} {(body or {}).get('kind') or (body or {}).get('detail', '')[:40]!s}"
        )
        if not self.live:
            code, body = self.h.get(
                "/traces/search", {"backend": "no-such-backend", "lookback_hours": 1}
            )
            good = code == 400 and (body or {}).get("kind") == "request"
            okay &= good
            parts.append(f"unknown backend → {code} {(body or {}).get('kind')}")
        if self.live or self.entry.get("metrics"):
            code, body = self.api(
                self.tpath("/metrics/query"),
                name="hermes.token.usage",
                lookback_hours=24,
                bucket_s=1,
            )
            good = code == 422 and (self.live or (body or {}).get("kind") == "request")
            okay &= good
            parts.append(f"bucket cap → {code} {(body or {}).get('kind') or '-'}")
        else:
            code, body = self.api(self.tpath("/metrics/query"), name="x", lookback_hours=1)
            good = code == 503 and (body or {}).get("kind") == "config"
            okay &= good
            parts.append(f"no metrics → {code} {(body or {}).get('kind') or '-'}")
        self.r.add("S3", okay, "; ".join(parts))
        return True

    # ── oracle
    def load_oracle(self) -> bool:
        ok_all = True
        for run in self.runs:
            marker = run["marker"]
            code, resp = self.h.get("/live/traces", {"text": marker, "limit": 5, **self.window()})
            rows = (resp or {}).get("traces") or []
            if code != 200 or len(rows) != 1:
                self.r.add(
                    "O",
                    False,
                    f"live store has {len(rows)} trace(s) for marker {marker} (code {code}); the oracle needs exactly one",
                )
                ok_all = False
                continue
            row = rows[0]
            tid = norm_tid(row["traceId"])
            row["traceId"] = tid
            _, det = self.h.get(f"/live/traces/{tid}")
            spans = live_spans(det or {})
            root = next((s for s in spans if not s["parent"]), None)
            _, logs = self.h.get(
                "/live/logs/search", {"trace_id": tid, "limit": 2000, **self.window()}
            )
            self.oracle[marker] = {
                "run": run,
                "row": row,
                "trace_id": tid,
                "spans": spans,
                "root_error": bool(root and root["error"]),
                "logs": (logs or {}).get("logs") or [],
            }
        if self.oracle:
            o = next(iter(self.oracle.values()))
            self.r.add(
                "O",
                ok_all,
                f"{len(self.oracle)}/{len(self.runs)} turns in the live store; e.g. {o['run']['id']}: {len(o['spans'])} spans, {len(o['logs'])} log lines, model {o['row'].get('model')}",
            )
        return bool(self.oracle)

    def wait_for_settle(self, settle_s: int) -> None:
        """Poll until the source lists every oracle trace, or the settle budget is spent."""
        deadline = time.time() + settle_s
        missing: List[str] = []
        while True:
            missing = []
            for marker, o in self.oracle.items():
                rows = self.search(limit=200)
                if not any(r["traceId"] == o["trace_id"] for r in rows):
                    missing.append(o["run"]["id"])
            if not missing or time.time() > deadline:
                break
            time.sleep(10)
        if missing:
            self.r.notes.append(f"settle: still missing after {settle_s}s: {missing}")

    # ── T
    def search(self, **params: Any) -> List[Dict[str, Any]]:
        p = {**self.window(), **params}
        if self.live:
            code, resp = self.h.get("/live/traces", p)
            return (resp or {}).get("traces") or [] if code == 200 else []
        code, resp = self.api("/traces/search", **p)
        if code != 200:
            return []
        return [row_from_backend(t) for t in (resp or {}).get("traces") or []]

    def search_raw(self, **params: Any) -> Tuple[int, Dict[str, Any]]:
        p = {**self.window(), **params}
        return self.h.get("/live/traces", p) if self.live else self.api("/traces/search", **p)

    def check_traces(self) -> None:
        listing = self.search(limit=200)
        by_id = {r["traceId"]: r for r in listing}
        for marker, o in self.oracle.items():
            rid = o["run"]["id"]
            tid = o["trace_id"]
            row = by_id.get(tid)
            # T1: found in the window + by marker
            free = self.level("free_text")
            if free != "none":
                hits = (
                    self.search(free_text=marker, limit=10)
                    if not self.live
                    else self.search(text=marker, limit=10)
                )
                ids = [r["traceId"] for r in hits]
                found_marker = ids == [tid]
                detail = (
                    f"{rid}: in window={bool(row)}; free_text={free} → {len(hits)} hit(s) {ids[:3]}"
                )
            else:
                found_marker = True
                detail = f"{rid}: in window={bool(row)}; free_text=none (marker search skipped)"
            self.r.add("T1", bool(row) and found_marker, detail)
            if not row:
                continue
            self.found[marker] = row
            if self.live:
                self.r.add("T2", None, f"{rid}: live is the oracle")
                self.r.add("T3", None, f"{rid}: live is the oracle")
                self.r.add("T4", None, f"{rid}: live has no backend UI")
                continue
            # T2: row parity
            lrow = o["row"]
            probs = []
            if row["rootName"] != lrow.get("rootName"):
                probs.append(f"root {row['rootName']!r} vs {lrow.get('rootName')!r}")
            if bool(row["error"]) != o["root_error"]:
                probs.append(f"status error={row['error']} vs live root error={o['root_error']}")
            if (row["model"] or "") != (lrow.get("model") or ""):
                probs.append(f"model {row['model']!r} vs {lrow.get('model')!r}")
            if (row["session"] or "") != (lrow.get("session") or ""):
                probs.append(f"session {row['session']!r} vs {lrow.get('session')!r}")
            if not close(row["tokens"], lrow.get("tokens"), 0):
                probs.append(f"tokens {row['tokens']} vs {lrow.get('tokens')}")
            start_tol = float(
                self.expect.get(self.entry.get("type"), {}).get("start_tolerance_ms", 5)
            )
            if not close(row["startNs"], lrow.get("startNs"), start_tol * 1e6):
                probs.append(f"start {row['startNs']} vs {lrow.get('startNs')}")
            if not close(row["durationMs"], lrow.get("durationMs"), 5, 0.01):
                probs.append(f"duration {row['durationMs']} vs {lrow.get('durationMs')}")
            self.r.add(
                "T2",
                not probs,
                f"{rid}: "
                + (
                    "; ".join(probs)
                    if probs
                    else f"root/status/model/session/tokens/start/duration match ({lrow.get('tokens')} tokens, {lrow.get('durationMs'):.0f} ms)"
                ),
            )
            # T3: detail parity
            code, det = self.api(f"/traces/{tid}")
            spans = spans_from_detail(det or {}) if code == 200 else []
            probs = []
            if code != 200:
                probs.append(f"detail answered {code}: {str(det)[:120]}")
            lnames = Counter(s["name"] for s in o["spans"])
            bnames = Counter(s["name"] for s in spans)
            if lnames != bnames:
                probs.append(f"span names {dict(bnames)} vs live {dict(lnames)}")
            ids = {s["span_id"] for s in spans}
            if spans and len([s for s in spans if not s["parent"]]) != 1:
                probs.append(f"{len([s for s in spans if not s['parent']])} parentless spans")
            orphans = [s["name"] for s in spans if s["parent"] and s["parent"] not in ids]
            if orphans:
                probs.append(f"orphans {orphans}")
            lerr = Counter(s["name"] for s in o["spans"] if s["error"])
            berr = Counter(s["name"] for s in spans if s["error"])
            if lerr != berr:
                probs.append(f"error spans {dict(berr)} vs live {dict(lerr)}")
            self.r.add(
                "T3",
                not probs,
                f"{rid}: "
                + (
                    "; ".join(probs)
                    if probs
                    else f"{len(spans)} spans, names and statuses match, one root, no orphans"
                ),
            )
            o["backend_spans"] = spans
            # T4: trace_url
            url = (det or {}).get("ui_url") if isinstance(det, dict) else None
            want_ui = self.expect.get(self.entry.get("type"), {}).get("ui", True)
            if want_ui:
                self.r.add("T4", bool(url) and tid in str(url), f"{rid}: ui_url={url}")
            else:
                self.r.add("T4", None, f"{rid}: no UI expected; ui_url={url}")
            o["trace_url"] = url

    def check_filters(self) -> None:
        ids = {o["trace_id"] for o in self.oracle.values()}
        if not ids:
            return
        by_run = {o["run"]["id"]: o for o in self.oracle.values()}
        # T5 status
        for st, key in (("ok", "status_ok"), ("error", "status_error")):
            lvl = self.level(key)
            if lvl == "none":
                self.r.add("T5", None, f"status={st}: adapter declares none")
                continue
            got = {r["traceId"] for r in self.search(status=st, limit=200)}
            sem = (
                "any"
                if self.live
                else self.expect.get(self.entry.get("type"), {}).get("status_semantics", "any")
            )
            if st == "ok":
                # "any" semantics: a turn whose tool span errored is not ok either
                clean = {
                    o["trace_id"]
                    for o in by_run.values()
                    if sem == "root" or not any(s["error"] for s in o["spans"])
                }
                ok = clean <= got and not ((ids - clean) & got)
                self.r.add(
                    "T5",
                    ok,
                    f"status=ok ({lvl}, semantics={sem}) returns {len(ids & got)}/{len(ids)} batch turns (expected {len(clean)})",
                )
            else:
                # every batch root is ok; backends whose status=error means "any span" may list missing-file
                leaked = [rid for rid, o in by_run.items() if o["trace_id"] in got]
                tolerated = (
                    [rid for rid in leaked if any(s["error"] for s in by_run[rid]["spans"])]
                    if sem == "any"
                    else []
                )
                bad = [rid for rid in leaked if rid not in tolerated]
                self.r.add(
                    "T5",
                    not bad,
                    f"status=error ({lvl}) lists batch turns {leaked} (semantics={sem}; tolerated {tolerated})",
                )
        # T6 tool / kind / session / model / min_duration
        tool_runs = {
            rid
            for rid, o in by_run.items()
            if any(s["name"].startswith("tool.") for s in o["spans"])
        }
        shell_runs = {
            rid
            for rid, o in by_run.items()
            if any(s["name"] == "tool.terminal" for s in o["spans"])
        }
        lvl = self.level("tool")
        if lvl == "none":
            self.r.add("T6", None, "tool=terminal: adapter declares none")
        else:
            got = {r["traceId"] for r in self.search(tool="terminal", limit=200)}
            hit = {rid for rid, o in by_run.items() if o["trace_id"] in got}
            self.r.add(
                "T6",
                hit == shell_runs,
                f"tool=terminal ({lvl}) → {sorted(hit)} (expected {sorted(shell_runs)})",
            )
        lvl = self.level("name")
        if lvl == "none":
            self.r.add("T6", None, "kind=tool: adapter declares no name filter")
        else:
            rows = (
                self.search(kind="tool", limit=200)
                if self.live
                else self.search(name_prefix="tool.", roots_only="false", limit=200)
            )
            got = {r["traceId"]: r for r in rows}
            hit = {rid for rid, o in by_run.items() if o["trace_id"] in got}
            matched_ok = all(
                all(
                    str(n).startswith("tool.") for n in got[o["trace_id"]].get("matched", ["tool."])
                )
                for rid, o in by_run.items()
                if o["trace_id"] in got
            )
            self.r.add(
                "T6",
                hit == tool_runs and matched_ok,
                f"kind=tool ({lvl}) → {sorted(hit)} (expected {sorted(tool_runs)}); matched spans are tool spans: {matched_ok}",
            )
        lvl = self.level("session")
        o0 = next(iter(self.oracle.values()))
        sid = o0["row"].get("session")
        if lvl == "none" or not sid:
            self.r.add("T6", None, f"session filter: level={lvl} sid={sid}")
        else:
            got = [r["traceId"] for r in self.search(session=sid, limit=50)]
            want = sorted(
                o["trace_id"] for o in self.oracle.values() if o["row"].get("session") == sid
            )
            self.r.add(
                "T6",
                sorted(got) == want,
                f"session={sid} ({lvl}) → {len(got)} trace(s), expected {len(want)}",
            )
        lvl = self.level("model")
        model = o0["row"].get("model")
        if lvl == "none" or not model:
            self.r.add("T6", None, f"model filter: level={lvl} model={model}")
        else:
            got = {r["traceId"] for r in self.search(model=model, limit=200)}
            self.r.add(
                "T6", ids <= got, f"model={model} ({lvl}) → {len(ids & got)}/{len(ids)} batch turns"
            )
        lvl = self.level("min_duration")
        if lvl == "none":
            self.r.add("T6", None, "min_duration: adapter declares none")
        else:
            got = {r["traceId"] for r in self.search(min_duration_ms=100_000_000, limit=200)}
            self.r.add(
                "T6",
                not (ids & got),
                f"min_duration_ms=1e8 ({lvl}) → {len(ids & got)} batch turns (expected 0)",
            )
        # T7 paging
        pages: List[List[Dict[str, Any]]] = []
        before = None
        seen: List[str] = []
        starts: List[int] = []
        ok = True
        why = ""
        for _ in range(20):
            code, resp = self.search_raw(limit=2, **({"before_ns": before} if before else {}))
            if code != 200:
                ok, why = False, f"page answered {code}: {str(resp)[:100]}"
                break
            rows = [
                r if self.live else row_from_backend(r) for r in (resp or {}).get("traces") or []
            ]
            pages.append(rows)
            for r in rows:
                if r["traceId"] in seen:
                    ok, why = False, f"trace {r['traceId'][:8]} repeated across pages"
                if starts and r["startNs"] >= min(starts):
                    ok, why = False, "a later page is not strictly older"
                seen.append(r["traceId"])
                starts.append(int(r["startNs"]))
            if not resp.get("has_more"):
                break
            before = resp.get("next_before_ns")
            if not before:
                ok, why = False, "has_more without next_before_ns"
                break
        if ok and not ids <= set(seen):
            ok, why = False, f"union of pages misses {len(ids - set(seen))} batch turn(s)"
        if ok and len(pages) < 2:
            ok, why = False, f"only {len(pages)} page(s) for {len(seen)} traces at limit=2"
        self.r.add(
            "T7",
            ok,
            why
            or f"{len(pages)} pages of ≤2, {len(seen)} traces, strictly older, no overlap, newest first",
        )

    # ── M
    def check_metrics(self, settle_s: int = 0) -> None:
        if not self.live and not self.entry.get("metrics"):
            self.r.add("M1", None, "source serves no metrics")
            self.r.add("M2", None, "source serves no metrics")
            return
        # Metrics land later than spans (collector batching, remote write):
        # compare once the window has closed, and retry a mismatch within the
        # settle budget before calling it.
        deadline = time.time() + settle_s
        while time.time() < self.end_s and time.time() < deadline:
            time.sleep(5)
        saved = list(self.r.checks)
        while True:
            self._check_metrics_once()
            m2 = next((c for c in self.r.checks[len(saved) :] if c.id == "M2"), None)
            if not m2 or m2.status != "FAIL" or time.time() > deadline:
                return
            del self.r.checks[len(saved) :]
            time.sleep(15)

    def _check_metrics_once(self) -> None:
        win = self.window()
        bucket = max(60, self.end_s - self.start_s)
        code, resp = self.api(
            self.tpath("/metrics/names"),
            lookback_hours=max(1, math.ceil((time.time() - self.start_s) / 3600)),
        )
        names = (resp or {}).get("names") or []
        res = {
            "tokens": resolve_instrument(names, "hermes.token.usage"),
            "calls": resolve_instrument(names, "hermes.model.usage"),
            "toolcalls": resolve_instrument(
                names, "hermes.tool.duration", "" if self.live else "_count"
            ),
        }
        self.r.add(
            "M1", code == 200 and all(res.values()), f"{len(names)} instruments; resolved {res}"
        )
        self.resolved = res
        if not all(res.values()):
            self.r.add("M2", False, "cannot compare: instrument missing")
            return
        # Live oracle totals
        _, lt = self.h.get(
            "/live/metrics/query",
            {
                "name": "hermes.token.usage",
                "agg": "sum",
                "group_by": "token_type",
                "bucket_s": bucket,
                **win,
            },
        )
        _, lc = self.h.get(
            "/live/metrics/query",
            {"name": "hermes.model.usage", "agg": "count", "bucket_s": bucket, **win},
        )
        live_tokens = series_totals(lt or {})
        live_calls = sum(series_totals(lc or {}).values())
        live_tools = sum(
            1 for o in self.oracle.values() for s in o["spans"] if s["name"].startswith("tool.")
        )
        if self.live:
            self.r.add(
                "M2",
                None,
                f"live totals: tokens={live_tokens} calls={live_calls} tool spans={live_tools}",
            )
            return
        _, bt = self.api(
            "/metrics/query",
            name=res["tokens"],
            agg="sum",
            group_by="token_type",
            bucket_s=bucket,
            **win,
        )
        _, bc = self.api("/metrics/query", name=res["calls"], agg="sum", bucket_s=bucket, **win)
        # the page counts observations when the native name has no _count part
        tc_agg = "sum" if re.search(r"[_.]count$", res["toolcalls"]) else "count"
        _, btc = self.api(
            "/metrics/query", name=res["toolcalls"], agg=tc_agg, bucket_s=bucket, **win
        )
        b_tokens = series_totals(bt or {})
        b_calls = sum(series_totals(bc or {}).values())
        b_tools = sum(series_totals(btc or {}).values())
        probs = []
        for label in sorted(set(live_tokens) | set(b_tokens)):
            if not close(live_tokens.get(label, 0), b_tokens.get(label, 0), 0.5):
                probs.append(
                    f"tokens[{label}] {b_tokens.get(label, 0)} vs live {live_tokens.get(label, 0)}"
                )
        if not close(b_calls, live_calls, 0.5):
            probs.append(f"model calls {b_calls} vs live {live_calls}")
        if not close(b_tools, live_tools, 0.5):
            probs.append(f"tool calls {b_tools} vs live {live_tools}")
        self.r.add(
            "M2",
            not probs,
            (
                "; ".join(probs)
                if probs
                else f"tokens {b_tokens}, model calls {b_calls:g}, tool calls {b_tools:g} = live"
            ),
        )
        self.metric_totals = {
            "tokens": sum(b_tokens.values()),
            "calls": b_calls,
            "toolcalls": b_tools,
        }

    # ── L
    def check_logs(self) -> None:
        if not self.live and not self.entry.get("logs"):
            for cid in ("L1", "L2", "L3", "L4", "L5"):
                self.r.add(cid, None, "source serves no logs")
            return
        win = self.window()
        seen_loggers: Counter = Counter()
        sample: Optional[Dict[str, Any]] = None
        for marker, o in self.oracle.items():
            rid = o["run"]["id"]
            tid = o["trace_id"]
            code, resp = self.api(self.tpath("/logs/search"), trace_id=tid, limit=2000, **win)
            rows = (resp or {}).get("logs") or []
            o["backend_logs"] = rows
            want_min = int((self.prompts.get(rid, {}).get("expect") or {}).get("logs_min") or 0)
            wrong = [r for r in rows if (r.get("trace_id") or "") != tid]
            probs = []
            if code != 200:
                probs.append(f"answered {code}: {str(resp)[:100]}")
            if not self.live and len(rows) != len(o["logs"]):
                probs.append(f"{len(rows)} lines vs live {len(o['logs'])}")
            if len(rows) < want_min:
                probs.append(f"{len(rows)} lines < logs_min {want_min}")
            if wrong:
                probs.append(f"{len(wrong)} lines carry another trace id")
            self.r.add(
                "L1",
                not probs,
                f"{rid}: "
                + (
                    "; ".join(probs)
                    if probs
                    else f"{len(rows)} lines = live, ≥ {want_min}, all on this trace"
                ),
            )
            for r in rows:
                seen_loggers[r.get("logger") or ""] += 1
            if rows and (sample is None or len(rows) > len(sample["rows"])):
                sample = {"tid": tid, "rows": rows, "rid": rid}
        if not sample:
            for cid in ("L2", "L3", "L4", "L5"):
                self.r.add(cid, False, "no log lines on any batch turn")
            return
        tid = sample["tid"]
        allrows = sample["rows"]
        # L2 level + logger
        _, resp = self.api(
            self.tpath("/logs/search"), trace_id=tid, min_level=30, limit=2000, **win
        )
        warn = (resp or {}).get("logs") or []
        want_warn = [r for r in allrows if int(r.get("severity_number") or 0) >= 13]
        bad = [r for r in warn if int(r.get("severity_number") or 0) < 13]
        probs = []
        if bad:
            probs.append(f"{len(bad)} lines below WARN returned")
        if len(warn) != len(want_warn):
            probs.append(f"{len(warn)} WARN+ lines vs {len(want_warn)} expected from the full set")
        logger = seen_loggers.most_common(1)[0][0]
        _, resp = self.api(
            self.tpath("/logs/search"), trace_id=tid, logger=logger, limit=2000, **win
        )
        lg = (resp or {}).get("logs") or []
        want_lg = [r for r in allrows if (r.get("logger") or "") == logger]
        if any((r.get("logger") or "") != logger for r in lg):
            probs.append("logger filter returned other loggers")
        if len(lg) != len(want_lg):
            probs.append(f"logger={logger}: {len(lg)} vs {len(want_lg)} expected")
        self.r.add(
            "L2",
            not probs,
            f"{sample['rid']}: "
            + (
                "; ".join(probs)
                if probs
                else f"min_level=30 → {len(warn)} lines, logger={logger} → {len(lg)} lines, both exact"
            ),
        )
        # L3 paging
        before = None
        seen_t: List[int] = []
        pages = 0
        ok, why = True, ""
        for _ in range(200):
            _, resp = self.api(
                self.tpath("/logs/search"),
                trace_id=tid,
                limit=3,
                **({"before_ns": before} if before else {}),
                **win,
            )
            rows = (resp or {}).get("logs") or []
            pages += 1
            for r in rows:
                t = int(r.get("time_unix_nano") or 0)
                if seen_t and t >= min(seen_t) and before:
                    ok, why = False, "a later page is not strictly older"
                seen_t.append(t)
            if not (resp or {}).get("has_more"):
                break
            before = (resp or {}).get("next_before_ns")
            if not before:
                ok, why = False, "has_more without next_before_ns"
                break
        if ok and len(seen_t) != len(allrows):
            ok, why = (
                False,
                f"paging visited {len(seen_t)} lines, the full set has {len(allrows)} (ties on time_unix_nano are lost by a strictly-older cursor)",
            )
        self.r.add(
            "L3", ok, why or f"{pages} pages of ≤3 cover all {len(allrows)} lines, strictly older"
        )
        # L4 loggers
        code, resp = self.api(
            self.tpath("/loggers"),
            lookback_hours=max(1, math.ceil((time.time() - self.start_s) / 3600)),
        )
        names = [x.get("logger") or x.get("name") for x in (resp or {}).get("loggers") or []]
        self.r.add(
            "L4",
            code == 200 and logger in names,
            f"{len(names)} loggers; includes {logger}: {logger in names}",
        )
        # L5 events only
        code, resp = self.api(self.tpath("/logs/search"), events_only="true", limit=200, **win)
        ev = (resp or {}).get("logs") or []
        self.r.add(
            "L5",
            code == 200 and all(r.get("event_name") for r in ev),
            f"events_only → {len(ev)} rows, all with event_name: {all(r.get('event_name') for r in ev)}",
        )

    # ── B
    def check_browser(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:  # pragma: no cover
            self.r.add("B", None, f"playwright not importable: {e}")
            return
        PANEL = '[role="tabpanel"]:not([hidden]) '
        # Lookback is relative to "now" at each page load; pad it so a page loaded
        # minutes into the browser phase still covers the whole batch window.
        hours = math.ceil(((time.time() - self.start_s) / 3600 + 0.25) * 100) / 100
        rng = next((h for h in (0.25, 1, 6, 24, 72, 168) if h >= hours), 168)
        bucket = {0.25: 15, 1: 60, 6: 300, 24: 900, 72: 3600, 168: 10800}[rng]
        src = "" if self.live else self.src
        errors: List[str] = []
        failed: List[str] = []

        def text(page, sel):
            loc = page.locator(PANEL + sel)
            return loc.first.inner_text() if loc.count() else ""

        def goto(page, name, **nav):
            errors.clear()
            failed.clear()
            page.goto(self.h.page_url(source=src, **nav), wait_until="networkidle", timeout=120000)
            time.sleep(3)
            page.screenshot(path=str(self.out / f"{name}.png"), full_page=True)

        def num(s: str) -> Optional[float]:
            m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
            return float(m.group(0).replace(",", "")) if m else None

        def tile(page, label: str) -> str:
            grid = text(page, "div.otel-kpi-grid")
            items = [x.strip() for x in grid.split("\n") if x.strip()]
            for i, it in enumerate(items):
                if it.upper() == label and i > 0:
                    return items[i - 1]
            return ""

        page_errors: Dict[str, List[str]] = {}

        def record(name):
            page_errors[name] = [*errors, *failed]

        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(
                viewport={"width": 1360, "height": 900}, device_scale_factor=1.5
            )
            page = ctx.new_page()
            page.on(
                "console",
                lambda m: (
                    errors.append(f"console {m.type}: {m.text[:160]}")
                    if m.type == "error"
                    else None
                ),
            )
            page.on(
                "response",
                lambda r: (
                    failed.append(f"{r.status} {r.url.split('/hermes_otel')[-1][:140]}")
                    if r.status >= 400
                    and "/api/plugins/hermes_otel" in r.url
                    and "0000000000000000000000000000dead" not in r.url
                    else None
                ),
            )
            # B1/B2 per turn
            for marker, o in self.oracle.items():
                rid = o["run"]["id"]
                nav: Dict[str, Any] = {"tab": "traces", "lookback": hours}
                if self.live or self.level("free_text") != "none":
                    nav["text"] = marker
                elif self.level("session") != "none" and o["row"].get("session"):
                    nav["session"] = o["row"]["session"]
                else:
                    self.r.add("B1", None, f"{rid}: no text or session filter to isolate the card")
                    continue
                goto(page, f"traces-{rid}", **nav)
                cards = page.locator(PANEL + '[role="button"][aria-label^="open trace"]')
                n = cards.count()
                card_text = cards.first.inner_text().replace("\n", " ") if n else ""
                probs = []
                if n != 1:
                    probs.append(f"{n} cards (expected 1)")
                lrow = o["row"]
                if lrow.get("rootName") and lrow["rootName"] not in card_text:
                    probs.append(f"root {lrow['rootName']!r} not on the card")
                if lrow.get("model") and str(lrow["model"]).split("/")[-1][:12] not in card_text:
                    probs.append("model not on the card")
                tok = lrow.get("tokens")
                if (
                    tok
                    and f"{int(tok):,}" not in card_text
                    and f"{tok / 1000:.1f}k" not in card_text
                ):
                    probs.append(f"tokens {int(tok):,} not on the card")
                record(f"traces-{rid}")
                self.r.add(
                    "B1",
                    not probs,
                    f"{rid}: " + ("; ".join(probs) if probs else f"one card: {card_text[:120]}"),
                )
                if n != 1:
                    continue
                cards.first.click()
                time.sleep(5)
                page.screenshot(path=str(self.out / f"detail-{rid}.png"), full_page=True)
                spans_line = text(page, "span:has-text('span')")
                shown = num(spans_line)
                want = len(o["spans"])
                probs = []
                if shown != want:
                    probs.append(f"spans line {spans_line!r} vs live {want}")
                link = page.locator(PANEL + "a:has-text('open in')")
                href = link.first.get_attribute("href") if link.count() else None
                if o.get("trace_url") and href != o["trace_url"]:
                    probs.append(f"open-in href {href} vs trace_url {o['trace_url']}")
                if self.live or self.entry.get("logs"):
                    btn = page.locator(PANEL + "button:has-text('Logs')")
                    if btn.count():
                        btn.last.click()
                        time.sleep(4)
                        page.screenshot(
                            path=str(self.out / f"detail-logs-{rid}.png"), full_page=True
                        )
                        line = (
                            text(page, "span:has-text('carry this trace id')")
                            or text(page, "div:has-text('No log lines carry')")
                            or text(page, "[role='alert']")
                        )
                        want_logs = len(o.get("backend_logs", o["logs"]))
                        got = num(line) if "carry" in line else (0 if "No log" in line else None)
                        if got != want_logs:
                            probs.append(f"logs sub-tab {line[:60]!r} vs API {want_logs}")
                    else:
                        probs.append("no Logs sub-tab")
                record(f"detail-{rid}")
                self.r.add(
                    "B2",
                    not probs,
                    f"{rid}: "
                    + (
                        "; ".join(probs)
                        if probs
                        else f"{want} spans, open-in href ok, logs sub-tab = API"
                    ),
                )
            # B3 filters by URL

            def cards_now():
                return page.locator(PANEL + '[role="button"][aria-label^="open trace"]').count()

            goto(page, "traces-status-error", tab="traces", lookback=hours, status="error")
            n_err = cards_now()
            ignored = text(page, "span:has-text('ignored:')")
            lvl = self.level("status_error")
            probs = []
            if lvl == "none" and "status" not in ignored:
                probs.append("status=error declared none but no 'ignored:' chip")
            if lvl != "none" and "status" in ignored:
                probs.append("status filter wrongly reported as ignored")
            goto(page, "traces-kind-tool", tab="traces", lookback=hours, kind="tool")
            n_tool = cards_now()
            first = (
                page.locator(PANEL + '[role="button"][aria-label^="open trace"]')
                .first.inner_text()
                .replace("\n", " ")
                if n_tool
                else ""
            )
            if self.level("name") != "none" and n_tool == 0:
                probs.append("kind=tool shows no cards")
            if n_tool and not self.live and "tool." not in first:
                probs.append(f"kind=tool first card is not a tool card: {first[:80]}")
            record("traces-filters")
            self.r.add(
                "B3",
                not probs,
                (
                    "; ".join(probs)
                    if probs
                    else f"status=error → {n_err} cards (ignored: {ignored or '-'}); kind=tool → {n_tool} cards, first: {first[:70]}"
                ),
            )
            # B4 metrics
            if self.live or self.entry.get("metrics"):
                goto(page, "metrics", tab="metrics", range=rng)
                time.sleep(5)
                page.screenshot(path=str(self.out / "metrics.png"), full_page=True)
                res = getattr(self, "resolved", {})
                probs = []
                for key, label, agg_live, agg_backend in (
                    ("tokens", "TOKENS", "sum", "sum"),
                    ("calls", "MODEL CALLS", "count", "sum"),
                    ("toolcalls", "TOOL CALLS", "count", "sum"),
                ):
                    name = res.get(key)
                    if not name:
                        probs.append(f"{label}: instrument unresolved")
                        continue
                    _, resp = self.api(
                        self.tpath("/metrics/query"),
                        name=name,
                        agg=(
                            "count"
                            if key == "toolcalls"
                            and not self.live
                            and not re.search(r"[_.]count$", name)
                            else (agg_live if self.live else agg_backend)
                        ),
                        lookback_hours=rng,
                        bucket_s=bucket,
                    )
                    api_total = sum(series_totals(resp or {}).values())
                    shown = tile(page, label)
                    if "not recorded" in shown or not shown:
                        probs.append(f"{label} tile reads {shown!r}")
                    elif not close(num(shown), api_total, 0.5) and not (
                        shown.endswith("k") and close(num(shown) * 1000, api_total, 60)
                    ):
                        probs.append(f"{label} tile {shown!r} vs API {api_total:g}")
                record("metrics")
                self.r.add(
                    "B4",
                    not probs,
                    (
                        "; ".join(probs)
                        if probs
                        else f"TOKENS {tile(page, 'TOKENS')}, MODEL CALLS {tile(page, 'MODEL CALLS')}, TOOL CALLS {tile(page, 'TOOL CALLS')} = API over {rng}h"
                    ),
                )
            else:
                self.r.add("B4", None, "source serves no metrics")
            # B5 logs
            if self.live or self.entry.get("logs"):
                goto(page, "logs", tab="logs", lookback=hours, size=100)
                time.sleep(3)
                line = text(page, "span:has-text('line')")
                _, resp = self.api(self.tpath("/logs/search"), limit=100, lookback_hours=hours)
                api_n = len((resp or {}).get("logs") or [])
                probs = []
                if num(line) != api_n:
                    probs.append(f"line count {line!r} vs API {api_n}")
                older = page.locator(PANEL + "button:has-text('Older')")
                older_on = older.count() > 0 and older.first.is_enabled()
                if bool((resp or {}).get("has_more")) != older_on:
                    probs.append(
                        f"Older enabled={older_on} but API has_more={(resp or {}).get('has_more')}"
                    )
                if older_on:
                    older.first.click()
                    time.sleep(4)
                    p2 = text(page, "span:has-text('page 2')")
                    if not p2:
                        probs.append("Older did not reach page 2")
                goto(page, "logs-warn", tab="logs", lookback=hours, size=100, level=30)
                line_w = text(page, "span:has-text('line')")
                _, resp = self.api(
                    self.tpath("/logs/search"), limit=100, lookback_hours=hours, min_level=30
                )
                if num(line_w) != len((resp or {}).get("logs") or []):
                    probs.append(
                        f"level=30 count {line_w!r} vs API {len((resp or {}).get('logs') or [])}"
                    )
                record("logs")
                self.r.add(
                    "B5",
                    not probs,
                    (
                        "; ".join(probs)
                        if probs
                        else f"{line} = API; Older enabled={older_on} = has_more; level=30 → {line_w} = API"
                    ),
                )
            else:
                self.r.add("B5", None, "source serves no logs")
            # B6 sessions + settings
            goto(page, "sessions", tab="traces", view="sessions", lookback=hours)
            body = text(page, "div")
            sids = {
                o["row"].get("session") for o in self.oracle.values() if o["row"].get("session")
            }
            missing = [s for s in sids if s not in body]
            goto(page, "settings", tab="settings")
            settings = text(page, "div")
            secret_leak = any(tok in settings for tok in ("Complexpass", "password: ") if tok)
            probs = []
            if missing:
                probs.append(f"sessions view misses {missing}")
            if not self.live and self.src not in settings:
                probs.append("settings tab does not list the source")
            if secret_leak:
                probs.append("a secret is printed in clear on the settings tab")
            record("settings")
            self.r.add(
                "B6",
                not probs,
                (
                    "; ".join(probs)
                    if probs
                    else f"sessions view lists {len(sids)} batch session(s); settings lists the source, secrets masked"
                ),
            )
            browser.close()
        # B7 console / failed requests
        bad = {k: v for k, v in page_errors.items() if v}
        self.r.add(
            "B7",
            not bad,
            (
                f"console errors / failed plugin requests: {json.dumps(bad)[:300]}"
                if bad
                else f"no console errors, no failed plugin requests across {len(page_errors)} pages"
            ),
        )

    # ── run
    def run(self, settle_s: int, browser: bool) -> Report:
        print(f"=== {self.src}: window {self.start_s}..{self.end_s} ({self.end_s - self.start_s}s)")
        if not self.check_status():
            return self.r
        if not self.load_oracle():
            return self.r
        if not self.live:
            self.wait_for_settle(settle_s)
        self.check_traces()
        self.check_filters()
        self.check_metrics(0 if self.live else settle_s)
        self.check_logs()
        if browser:
            self.check_browser()
        return self.r


# ── report ───────────────────────────────────────────────────────────────


def write_report(
    rep: Report, out: Path, token: str, profile: str, runs: List[Dict[str, Any]]
) -> None:
    counts = rep.counts()
    lines = [
        f"# Dashboard verification: `{rep.source}`",
        "",
        f"batch `{token}` · profile `{profile}` · {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
        + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())),
        "",
        "| check | status | detail |",
        "|---|---|---|",
    ]
    for c in rep.checks:
        detail = c.detail.replace("|", "\\|")
        lines.append(f"| {c.id} | {c.status} | {detail} |")
    lines += ["", "## Turns", ""]
    for r in runs:
        lines.append(
            f"- `{r.get('id')}` · marker `{r.get('marker')}` · {r.get('duration_s')}s · exit {r.get('exit_code')}"
        )
    if rep.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in rep.notes]
    shots = sorted(p.name for p in out.glob("*.png"))
    if shots:
        lines += ["", "## Screenshots", ""] + [f"- `{s}`" for s in shots]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    (out / "report.json").write_text(
        json.dumps(
            {
                "source": rep.source,
                "batch": token,
                "profile": profile,
                "counts": counts,
                "checks": [c.__dict__ for c in rep.checks],
                "notes": rep.notes,
            },
            indent=2,
        )
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base", default="http://127.0.0.1:9119", help="dashboard base URL")
    ap.add_argument(
        "--profile",
        default="minimal",
        help="Hermes profile that runs the turns and whose live store is the oracle",
    )
    ap.add_argument(
        "--source",
        action="append",
        default=[],
        help="source name (a backend's name in hermes_otel.yaml, or 'live'); repeatable",
    )
    ap.add_argument(
        "--batch", default=None, help="re-score this batch token instead of running turns"
    )
    ap.add_argument("--run-batch", action="store_true", help="run the workload first")
    ap.add_argument(
        "--prompts",
        default=",".join(DEFAULT_PROMPTS),
        help="comma-separated prompt ids from scripts/turn_batch/prompts.yaml",
    )
    ap.add_argument(
        "--settle",
        type=int,
        default=SETTLE_S,
        help="seconds to wait for the backend to list every turn",
    )
    ap.add_argument("--no-browser", action="store_true", help="skip the Playwright checks")
    ap.add_argument("--out", default=str(HERE / "out"), help="report root directory")
    args = ap.parse_args(argv)
    if not args.source:
        ap.error("at least one --source")
    if not args.batch and not args.run_batch:
        ap.error("--batch TOKEN or --run-batch")

    import yaml

    expect = (yaml.safe_load((HERE / "backends.yaml").read_text()) or {}).get("types") or {}
    prompts = load_prompts()
    token = args.batch or datetime.now().strftime("B%y%m%d-%H%M%S")
    if args.run_batch:
        manifest = run_batch(args.profile, args.prompts.split(","), token)
    else:
        manifest = batch_dir(token) / "runs.json"
    if not manifest.exists():
        print(f"no manifest at {manifest}", file=sys.stderr)
        return 2
    runs = [r for r in load_manifest(manifest) if r.get("started_at")]
    print(f"batch {token}: {[r['id'] for r in runs]}")
    host = Host(args.base, args.profile)
    if not host.token:
        print(
            "no session token in the dashboard index page; is the dashboard running?",
            file=sys.stderr,
        )
    rc = 0
    for src in args.source:
        out = Path(args.out) / token / src
        out.mkdir(parents=True, exist_ok=True)
        v = Verifier(host, src, runs, prompts, out, expect)
        rep = v.run(args.settle, not args.no_browser)
        write_report(rep, out, token, args.profile, runs)
        print(f"--- {src}: {rep.counts()} → {out / 'report.md'}")
        if rep.failed():
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
