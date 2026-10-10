"""Capture each backend's own UI showing the same turns the Hermes dashboard showed.

    uv run --with playwright python marketing/tour/capture/backend.py --source lgtm-local

Reads marketing/tour/assets/shots/<source>/manifest.json (written by dashboard.py) for the trace
ids and the backend's own trace links, logs in where the UI needs it, and writes
marketing/tour/assets/shots/<source>/ui-<page>.png. One recipe per backend type; a page that a
backend does not have is simply skipped. Credentials are the compose files' development defaults.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def shot(page, out: Path, name: str, settle: float = 3.0, full: bool = False) -> None:
    time.sleep(settle)
    page.screenshot(path=str(out / f"ui-{name}.png"), full_page=full)
    print(f"  ui-{name}.png")


def grafana_explore(base: str, pane: dict) -> str:
    return f"{base}/explore?schemaVersion=1&panes=" + urllib.parse.quote(json.dumps({"a": pane})) + "&orgId=1"


# ── LGTM: Grafana Explore on Tempo, Loki and Prometheus ──────────────────
def capture_lgtm(page, out: Path, m: dict, base: str = "http://localhost:3001") -> None:
    hero = (m["backend_links"].get("hero-research") or m["backend_links"].get("multi-tool") or {})
    tid = hero.get("trace_id")
    rng = {"from": "now-24h", "to": "now"}
    if tid:
        page.goto(grafana_explore(base, {"datasource": "tempo", "range": rng, "queries": [
            {"refId": "A", "datasource": {"type": "tempo", "uid": "tempo"}, "queryType": "traceql", "query": tid}]}), wait_until="networkidle")
        shot(page, out, "trace", settle=6)
        page.goto(grafana_explore(base, {"datasource": "loki", "range": rng, "queries": [
            {"refId": "A", "datasource": {"type": "loki", "uid": "loki"}, "expr": '{service_name="hermes-agent"} | trace_id="%s"' % tid}]}), wait_until="networkidle")
        shot(page, out, "logs", settle=6)
    page.goto(grafana_explore(base, {"datasource": "tempo", "range": rng, "queries": [
        {"refId": "A", "datasource": {"type": "tempo", "uid": "tempo"}, "queryType": "traceql",
         "query": '{ resource.service.name = "hermes-agent" && name = "agent" }', "limit": 20, "tableType": "traces"}]}), wait_until="networkidle")
    shot(page, out, "traces", settle=6)
    page.goto(grafana_explore(base, {"datasource": "prometheus", "range": rng, "queries": [
        {"refId": "A", "datasource": {"type": "prometheus", "uid": "prometheus"}, "expr": "sum by (token_type) (increase(hermes_token_usage_total[5m]))", "legendFormat": "{{token_type}}"}]}), wait_until="networkidle")
    shot(page, out, "metrics", settle=6)


# ── OpenObserve: traces, logs and metrics pages (login form) ─────────────
def capture_openobserve(page, out: Path, m: dict, base: str = "http://localhost:5080") -> None:
    page.goto(base + "/web/login", wait_until="networkidle")
    try:
        page.fill('input[type="email"], input[aria-label="Email *"], input[name="email"]', "root@example.com")
        page.fill('input[type="password"]', "Complexpass#123")
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")
        time.sleep(3)
    except Exception as e:  # noqa: BLE001
        print("  login:", str(e)[:100])
    hero = (m["backend_links"].get("hero-research") or m["backend_links"].get("multi-tool") or {})
    page.goto(base + "/web/traces?org_identifier=default&stream=default&period=24h", wait_until="networkidle")
    shot(page, out, "traces", settle=6)
    if hero.get("ui_url"):
        page.goto(hero["ui_url"], wait_until="networkidle")
        shot(page, out, "trace", settle=7)
    page.goto(base + "/web/logs?org_identifier=default&stream=default&period=24h", wait_until="networkidle")
    time.sleep(4)
    tid = hero.get("trace_id")
    if tid:
        try:  # filter the stream to the hero trace, then run
            ed = page.locator(".monaco-editor, .cm-content, textarea").first
            ed.click()
            page.keyboard.press("Meta+A")
            page.keyboard.type(f"trace_id='{tid}'")
            page.get_by_role("button", name="Run query").first.click()
        except Exception as e:  # noqa: BLE001
            print("  logs query:", str(e)[:100])
    shot(page, out, "logs", settle=6)
    page.goto(base + "/web/metrics?org_identifier=default&stream=hermes_token_usage&period=15m", wait_until="networkidle")
    time.sleep(4)
    try:
        page.get_by_role("button", name="Run query").first.click()
    except Exception as e:  # noqa: BLE001
        print("  metrics run:", str(e)[:100])
    shot(page, out, "metrics", settle=7)


# ── SigNoz: login, trace detail, traces explorer, metrics, logs ───────────
def capture_signoz(page, out: Path, m: dict, base: str = "http://localhost:3301") -> None:
    page.goto(base + "/login", wait_until="networkidle")
    try:  # two-step sign-in: email → Next → password
        page.fill('input[type="email"], #email', "admin@signoz.local")
        page.get_by_role("button", name="Next").click()
        page.wait_for_selector('input[type="password"]', timeout=15000)
        page.fill('input[type="password"]', "HermesOtel#local-2026")
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")
        time.sleep(5)
    except Exception as e:  # noqa: BLE001
        print("  login:", str(e)[:100])
    hero = (m["backend_links"].get("hero-research") or m["backend_links"].get("multi-tool") or {})
    tid = hero.get("trace_id")

    def dismiss() -> None:  # the "Edit your quick filters" tour card
        try:
            page.get_by_role("button", name="Okay").click(timeout=3000)
        except Exception:  # noqa: BLE001
            pass

    page.goto(base + "/traces-explorer?relativeTime=24h", wait_until="networkidle")
    time.sleep(5)
    dismiss()
    shot(page, out, "traces", settle=3)
    if hero.get("ui_url"):
        page.goto(hero["ui_url"], wait_until="networkidle")
        time.sleep(6)
        try:  # expand the collapsed root so the waterfall shows every span
            page.locator("button.collapse-uncollapse-button").first.click(timeout=5000)
        except Exception as e:  # noqa: BLE001
            print("  expand:", str(e)[:80])
        shot(page, out, "trace", settle=4)
    page.goto(base + "/metrics-explorer/explorer?relativeTime=24h", wait_until="networkidle")
    time.sleep(5)
    try:  # pick the token counter and run
        sel = page.locator(".ant-select-selection-search-input").first
        sel.click()
        page.keyboard.type("hermes.token.usage")
        time.sleep(2.5)
        page.keyboard.press("Enter")
        time.sleep(1)
        page.get_by_role("button", name="Run Query").first.click()
    except Exception as e:  # noqa: BLE001
        print("  metric:", str(e)[:80])
    shot(page, out, "metrics", settle=8)
    page.goto(base + "/logs/logs-explorer?relativeTime=24h", wait_until="networkidle")
    time.sleep(5)
    dismiss()
    if tid:
        try:  # filter the explorer to the hero trace
            ed = page.locator(".cm-content").first
            ed.click()
            page.keyboard.type(f"trace_id = '{tid}'")
            time.sleep(1)
            page.keyboard.press("Escape")
            page.get_by_role("button", name="Run Query").first.click()
        except Exception as e:  # noqa: BLE001
            print("  logs filter:", str(e)[:80])
    shot(page, out, "logs", settle=7)


# ── Uptrace: no login on the dev seed; traces, trace, metrics, logs ───────
def capture_uptrace(page, out: Path, m: dict, base: str = "http://localhost:14318") -> None:
    hero = (m["backend_links"].get("hero-research") or m["backend_links"].get("multi-tool") or {})
    page.goto(base + "/auth/login", wait_until="networkidle")
    time.sleep(2)
    try:  # seeded admin from uptrace.yml (Vuetify inputs: a text field and a password field)
        page.locator('input[type="text"]').first.fill("admin@uptrace.local")
        page.locator('input[type="password"]').first.fill("admin")
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")
        time.sleep(4)
    except Exception as e:  # noqa: BLE001
        print("  login:", str(e)[:100])
    page.goto(base + "/overview/1?time_dur=86400", wait_until="networkidle")
    shot(page, out, "overview", settle=6)
    q = urllib.parse.quote('perMin(count()) | quantiles(_dur_ms) | _error_rate | group by _group_id | where _parent_id = 0 | where service_name = "hermes-agent"')
    page.goto(base + f"/traces/1?time_dur=86400&system=funcs&query={q}", wait_until="networkidle")
    shot(page, out, "traces", settle=6)
    if hero.get("ui_url"):
        page.goto(hero["ui_url"], wait_until="networkidle")
        time.sleep(6)
        try:  # expand the llm span's children (the chip shows the child count)
            page.locator("button.bg-indigo-lighten-3").nth(1).click(timeout=5000)
        except Exception as e:  # noqa: BLE001
            print("  expand:", str(e)[:80])
        shot(page, out, "trace", settle=4)
        try:  # the trace's own logs tab
            page.get_by_text(re.compile(r"Logs & Errors \(\d+\)")).first.click(timeout=5000)
            shot(page, out, "logs", settle=6)
        except Exception as e:  # noqa: BLE001
            print("  logs tab:", str(e)[:80])
    page.goto(base + "/metrics/1/explore?time_dur=3600", wait_until="networkidle")
    time.sleep(4)
    try:
        page.get_by_text("hermes_token_usage", exact=True).first.click(timeout=5000)
    except Exception as e:  # noqa: BLE001
        print("  metric:", str(e)[:80])
    shot(page, out, "metrics", settle=7)


# ── Jaeger: search + trace detail ─────────────────────────────────────────
def capture_jaeger(page, out: Path, m: dict, base: str = "http://localhost:16686") -> None:
    hero = (m["backend_links"].get("hero-research") or m["backend_links"].get("multi-tool") or {})
    page.goto(base + "/search?service=hermes-agent&limit=20&lookback=1d", wait_until="networkidle")
    shot(page, out, "traces", settle=6)
    if hero.get("ui_url"):
        page.goto(hero["ui_url"], wait_until="networkidle")
        shot(page, out, "trace", settle=6)


# ── Phoenix: project traces + trace detail ─────────────────────────────────
def capture_phoenix(page, out: Path, m: dict, base: str = "http://localhost:6006") -> None:
    hero = (m["backend_links"].get("hero-research") or m["backend_links"].get("multi-tool") or {})
    page.goto(base + "/projects", wait_until="networkidle")
    shot(page, out, "projects", settle=5)
    if hero.get("ui_url"):
        project = hero["ui_url"].split("/traces/")[0]
        page.goto(project + "/traces", wait_until="networkidle")
        shot(page, out, "traces", settle=6)
        page.goto(hero["ui_url"], wait_until="networkidle")
        shot(page, out, "trace", settle=7)


# ── Langfuse: login, traces list, trace detail, sessions ──────────────────
def capture_langfuse(page, out: Path, m: dict, base: str = "http://localhost:3002") -> None:
    page.goto(base + "/auth/sign-in", wait_until="networkidle")
    try:
        page.fill('input[name="email"]', "test@test.com")
        page.fill('input[name="password"]', "testpassword123")
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")
        time.sleep(4)
    except Exception as e:  # noqa: BLE001
        print("  login:", str(e)[:100])
    hero = (m["backend_links"].get("hero-research") or m["backend_links"].get("multi-tool") or {})
    page.goto(base + "/project/test-project/traces", wait_until="networkidle")
    shot(page, out, "traces", settle=7)
    if hero.get("ui_url"):
        page.goto(hero["ui_url"], wait_until="networkidle")
        shot(page, out, "trace", settle=8)
    page.goto(base + "/project/test-project/sessions", wait_until="networkidle")
    shot(page, out, "sessions", settle=6)


RECIPES = {
    "lgtm": capture_lgtm,
    "tempo": capture_lgtm,
    "openobserve": capture_openobserve,
    "signoz": capture_signoz,
    "uptrace": capture_uptrace,
    "jaeger": capture_jaeger,
    "phoenix": capture_phoenix,
    "langfuse": capture_langfuse,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--base", default=None, help="override the backend UI base URL")
    a = ap.parse_args()
    out = ROOT / "assets" / "shots" / a.source
    m = json.loads((out / "manifest.json").read_text())
    kind = m["status"]["type"]
    recipe = RECIPES[kind]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
        page = ctx.new_page()
        if a.base:
            recipe(page, out, m, a.base)
        else:
            recipe(page, out, m)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
