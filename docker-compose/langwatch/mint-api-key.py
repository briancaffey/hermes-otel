"""Create the first LangWatch user, organisation and project and print its API key.

LangWatch's sign-up and onboarding are a multi-step browser flow (Better Auth
plus a wizard) with no headless route, so this drives it with Playwright
against the bundled compose stack and reads the key back from the UI's own
Postgres. Idempotent: an existing project's key is printed without touching
the browser.

    uv run --with playwright python -m playwright install chromium   # once
    export LANGWATCH_API_KEY=$(uv run --with playwright python docker-compose/langwatch/mint-api-key.py)

Environment: LANGWATCH_URL (default http://localhost:5560), LANGWATCH_SIGNUP_EMAIL,
LANGWATCH_SIGNUP_PASSWORD (defaults: hermes@example.com / a fixed local password).
"""

from __future__ import annotations

import os
import subprocess
import sys

URL = os.getenv("LANGWATCH_URL", "http://localhost:5560").rstrip("/")
EMAIL = os.getenv("LANGWATCH_SIGNUP_EMAIL", "hermes@example.com")
PASSWORD = os.getenv("LANGWATCH_SIGNUP_PASSWORD", "HermesOtel-local-123!")
CONTAINER = os.getenv("LANGWATCH_POSTGRES_CONTAINER", "hermes-otel-langwatch-postgres")


def existing_key() -> str:
    out = subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER,
            "psql",
            "-U",
            "prisma",
            "-d",
            "mydb",
            "-At",
            "-c",
            'select "apiKey" from mydb."Project" order by "createdAt" limit 1',
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def main() -> int:
    key = existing_key()
    if key:
        print(key)
        return 0
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context().new_page()
        page.goto(f"{URL}/auth/signup", wait_until="networkidle")
        page.fill("input[name=email]", EMAIL)
        page.click("button[type=submit]")
        page.wait_for_selector("input[name=password]", timeout=60_000)
        page.fill("input[name=password]", PASSWORD)
        try:
            page.wait_for_selector("input[name=confirmPassword]", timeout=5_000)
            page.fill("input[name=confirmPassword]", PASSWORD)
        except Exception:
            pass
        page.click("button[type=submit]")
        page.wait_for_url(lambda u: "/onboarding" in u, timeout=60_000)
        # Wizard step 1: organisation name + terms, Next.
        page.wait_for_selector("input[placeholder*='name' i]", timeout=30_000)
        page.fill("input[placeholder*='name' i]", "hermes-otel")
        box = page.query_selector("input[type=checkbox]")
        if box and not box.is_checked():
            box.check(force=True)
        page.click("button:has-text('Next')")
        # Step 2: starting point, Finish creates the project.
        page.wait_for_selector("[role=radio]", timeout=30_000)
        page.query_selector_all("[role=radio]")[0].click()
        page.click("button:has-text('Finish')")
        page.wait_for_url(
            lambda u: "projectSlug=" in u or "/onboarding/welcome" not in u, timeout=60_000
        )
        browser.close()
    key = existing_key()
    if not key:
        print(
            "no project found after onboarding; open the UI and finish the wizard", file=sys.stderr
        )
        return 1
    print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
