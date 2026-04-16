"""Quick smoke test for the hermes-agent OpenAI-compatible API server.

Verifies the API server is reachable, lists models, and completes a chat.

Prerequisites:
  1. Add to ~/.hermes/.env:
       API_SERVER_ENABLED=true
  2. Start hermes:
       hermes gateway

The server runs on http://127.0.0.1:8642 by default.
"""

import pytest

requests = pytest.importorskip("requests")
openai = pytest.importorskip("openai")
from openai import OpenAI


@pytest.fixture(scope="module")
def api_base_url():
    """Return the hermes API server base URL, skip if not reachable."""
    url = "http://127.0.0.1:8642"
    try:
        resp = requests.get(f"{url}/health", timeout=5)
        resp.raise_for_status()
    except Exception:
        pytest.skip(
            "Hermes API server not running. To enable:\n"
            "  1. Add API_SERVER_ENABLED=true to ~/.hermes/.env\n"
            "  2. Run: hermes gateway"
        )
    return url


@pytest.fixture(scope="module")
def client(api_base_url):
    """Return an OpenAI client pointed at hermes."""
    return OpenAI(base_url=f"{api_base_url}/v1", api_key="not-required")


@pytest.mark.smoke
class TestHermesAPI:

    def test_health_endpoint(self, api_base_url):
        """GET /health returns 200."""
        resp = requests.get(f"{api_base_url}/health", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"

    def test_list_models(self, client):
        """GET /v1/models returns at least one model."""
        models = client.models.list()
        model_ids = [m.id for m in models.data]
        assert len(model_ids) >= 1, "No models returned"

    def test_simple_chat_completion(self, client):
        """POST /v1/chat/completions with a trivial prompt returns a response."""
        response = client.chat.completions.create(
            model="hermes-agent",
            messages=[
                {"role": "user", "content": "What is 2+2? Reply with just the number."},
            ],
            max_tokens=10,
        )
        assert len(response.choices) >= 1
        reply = response.choices[0].message.content
        assert reply is not None and len(reply.strip()) > 0
        assert response.usage is not None
