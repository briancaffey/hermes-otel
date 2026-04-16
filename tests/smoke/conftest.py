"""Smoke test fixtures.

Smoke tests exercise the full pipeline: send a real chat to a running
hermes-agent API server and verify that traces land in the observability
backend.  They require external services to be running and are skipped
otherwise.
"""

import base64
import os
import time

import pytest
import requests


def _load_dotenv(path):
    """Read key=value pairs from a .env file into a dict."""
    result = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    result[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return result


# ── Hermes API server ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def hermes_api():
    """Connect to a running hermes-agent API server.

    Skips if the server is not reachable.  Does NOT start hermes — the user
    must have it running (e.g. `API_SERVER_ENABLED=true hermes gateway`).

    Yields a dict with: base_url, api_key (may be None).
    """
    hermes_env = _load_dotenv(os.path.expanduser("~/.hermes/.env"))

    host = os.environ.get("API_SERVER_HOST") or hermes_env.get("API_SERVER_HOST") or "127.0.0.1"
    port = os.environ.get("API_SERVER_PORT") or hermes_env.get("API_SERVER_PORT") or "8642"
    api_key = os.environ.get("API_SERVER_KEY") or hermes_env.get("API_SERVER_KEY") or None

    base_url = f"http://{host}:{port}"
    health_url = f"{base_url}/health"

    try:
        resp = requests.get(health_url, timeout=5)
        resp.raise_for_status()
    except Exception:
        pytest.skip(
            f"Hermes API server not reachable at {base_url}. "
            "Start it with: API_SERVER_ENABLED=true hermes gateway"
        )

    yield {
        "base_url": base_url,
        "api_key": api_key,
    }


# ── Langfuse REST API ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def langfuse_api():
    """Connect to a running Langfuse instance for trace verification.

    Reads keys from env vars or ~/.hermes/.env.  Skips if Langfuse is not
    reachable or no keys are configured.

    Yields a dict with: base_url, public_key, secret_key, auth_header.
    """
    hermes_env = _load_dotenv(os.path.expanduser("~/.hermes/.env"))

    public_key = (
        os.environ.get("OTEL_LANGFUSE_PUBLIC_API_KEY")
        or os.environ.get("LANGFUSE_PUBLIC_KEY")
        or hermes_env.get("OTEL_LANGFUSE_PUBLIC_API_KEY")
        or hermes_env.get("LANGFUSE_PUBLIC_KEY")
    )
    secret_key = (
        os.environ.get("OTEL_LANGFUSE_SECRET_API_KEY")
        or os.environ.get("LANGFUSE_SECRET_KEY")
        or hermes_env.get("OTEL_LANGFUSE_SECRET_API_KEY")
        or hermes_env.get("LANGFUSE_SECRET_KEY")
    )

    if not public_key or not secret_key:
        pytest.skip("Langfuse API keys not configured")

    # Derive base URL from the OTEL endpoint or default to localhost:3000
    otel_endpoint = (
        os.environ.get("OTEL_LANGFUSE_ENDPOINT")
        or hermes_env.get("OTEL_LANGFUSE_ENDPOINT")
        or ""
    )
    if otel_endpoint:
        # e.g. http://localhost:3000/api/public/otel/v1/traces -> http://localhost:3000
        base_url = otel_endpoint.split("/api/")[0]
    else:
        base_url = "http://localhost:3000"

    creds = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    auth_header = {"Authorization": f"Basic {creds}"}

    # Verify Langfuse is reachable
    try:
        resp = requests.get(f"{base_url}/api/public/health", timeout=5)
        resp.raise_for_status()
    except Exception:
        pytest.skip(f"Langfuse not reachable at {base_url}")

    yield {
        "base_url": base_url,
        "public_key": public_key,
        "secret_key": secret_key,
        "auth_header": auth_header,
    }
