"""Capture the Hermes dashboard's OTel tab for one source, page by page.

    uv run --with playwright python marketing/tour/capture/dashboard.py --source lgtm-local --token V261010-lgtm

Writes marketing/tour/assets/shots/<source>/<page>.png at 2x device pixels (1600x1000 CSS px) and
a manifest.json with the trace ids and totals it saw, read from the plugin API with the live store
as the reference. The host's banners ("Help improve Hermes", "Managing profile") are hidden so the
frames read cleanly in the video; nothing about the data is touched.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:9119"
ROOT = Path(__file__).resolve().parents[1]
HIDE_CSS = """
[class*="otel"] {}
"""


def token() -> str:
    html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8", "replace")
    m = re.search(r'__HERMES_SESSION_TOKEN__="([^"]+)"', html)
    return m.group(1) if m else ""


def api(path: str, tok: str, **params) -> dict:
    q = {k: v for k, v in params.items() if v is not None and v != ""}
    q.setdefault("profile", "minimal")
    req = urllib.request.Request(
        f"{BASE}/api/plugins/hermes_otel{path}?{urllib.parse.urlencode(q)}",
        headers={"Authorization": "Bearer " + tok},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def hide_host_chrome(page) -> None:
    page.evaluate(
        """() => {
          for (const el of document.querySelectorAll('div, section')) {
            const t = (el.innerText || '').trim();
            if (!t) continue;
            if (el.children.length < 12 && (t.startsWith('Managing profile') || t.startsWith('Help improve Hermes?'))) {
              let node = el;
              // climb only through thin wrappers (banner-sized), never into the app shell
              for (let i = 0; i < 3; i++) {
                const par = node.parentElement;
                if (!par || par.children.length !== 1 || par.getBoundingClientRect().height > 140) break;
                node = par;
              }
              if (node.getBoundingClientRect().height <= 140) node.style.display = 'none';
            }
          }
        }"""
    )


def shot(page, path: Path, name: str, url: str, settle: float = 2.5, full: bool = False) -> None:
    page.goto(url, wait_until="networkidle")
    try:
        page.wait_for_selector(".otel-root", timeout=20000)
    except Exception:
        pass
    time.sleep(settle)
    hide_host_chrome(page)
    time.sleep(0.4)
    page.screenshot(path=str(path / f"{name}.png"), full_page=full)
    print(f"  {name}.png")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--token", required=True, help="batch token whose turns to feature")
    ap.add_argument("--lookback", default="24")
    ap.add_argument("--out", default=str(ROOT / "assets" / "shots"))
    a = ap.parse_args()
    tok = token()
    out = Path(a.out) / a.source
    out.mkdir(parents=True, exist_ok=True)

    # The turns of this batch, from the live store (the oracle), newest first.
    by_id = {}
    for pid in ("hero-research", "multi-tool", "missing-file", "web-fetch", "skill-load", "slow-tools", "file-roundtrip", "shell-echo", "no-tools", "long-output", "shell-nonzero"):
        rows = api("/live/traces", tok, lookback_hours=a.lookback, text=f"{a.token}-{pid}", limit=3)["traces"]
        if rows:
            by_id[pid] = rows[0]["traceId"]
    marked = [{"traceId": t} for t in by_id.values()]
    manifest = {"source": a.source, "token": a.token, "traces": by_id, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    hero = by_id.get("hero-research") or by_id.get("multi-tool") or (marked[0]["traceId"] if marked else None)
    errored = by_id.get("missing-file")
    st = api("/status", tok, backend=a.source)
    manifest["status"] = {k: st.get(k) for k in ("type", "metrics", "logs", "query_url")}
    # the backend's own links, for the backend capture step
    links = {}
    for pid, tid in by_id.items():
        try:
            det = api(f"/traces/{tid}", tok, backend=a.source)
            links[pid] = {"trace_id": tid, "ui_url": det.get("ui_url"), "span_count": det.get("span_count")}
        except Exception as e:  # noqa: BLE001
            links[pid] = {"trace_id": tid, "error": str(e)[:120]}
    manifest["backend_links"] = links

    q = f"profile=minimal&source={a.source}&lookback={a.lookback}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
        page = ctx.new_page()
        shot(page, out, "live", f"{BASE}/otel?tab=live&profile=minimal&lookback={a.lookback}")
        shot(page, out, "traces", f"{BASE}/otel?tab=traces&{q}")
        shot(page, out, "traces-kind-tool", f"{BASE}/otel?tab=traces&{q}&kind=tool")
        shot(page, out, "sessions", f"{BASE}/otel?tab=traces&view=sessions&{q}")
        if hero:
            shot(page, out, "detail-hero", f"{BASE}/otel?tab=traces&{q}&trace={hero}", settle=3.5)
            shot(page, out, "detail-hero-full", f"{BASE}/otel?tab=traces&{q}&trace={hero}", settle=3.5, full=True)
        if errored:
            shot(page, out, "detail-error", f"{BASE}/otel?tab=traces&{q}&trace={errored}", settle=3.5)
        if st.get("metrics"):
            shot(page, out, "metrics", f"{BASE}/otel?tab=metrics&{q}", settle=4)
        if st.get("logs"):
            shot(page, out, "logs", f"{BASE}/otel?tab=logs&{q}", settle=3.5)
            shot(page, out, "logs-warn", f"{BASE}/otel?tab=logs&{q}&level=30", settle=3.5)
            if hero:
                shot(page, out, "logs-trace", f"{BASE}/otel?tab=logs&{q}&trace={hero}", settle=3.5)
        shot(page, out, "settings", f"{BASE}/otel?tab=settings&profile=minimal")
        browser.close()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({k: v for k, v in manifest.items() if k != "backend_links"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
