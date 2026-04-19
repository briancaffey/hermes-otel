"""Smoke tests: send a real chat to hermes-agent, verify traces in Phoenix.

Prerequisites:
  - hermes-agent API server running:  API_SERVER_ENABLED=true hermes gateway
  - Phoenix running on port 6006 (docker compose -f docker-compose/phoenix.yaml up -d)
  - OTEL_PHOENIX_ENDPOINT set in ~/.hermes/.env

These tests are skipped automatically if either service is not reachable.
"""

import time
from datetime import datetime, timezone

import pytest

requests = pytest.importorskip("requests")
openai = pytest.importorskip("openai")
from openai import OpenAI

# ── Phoenix GraphQL helpers ──────────────────────────────────────────────────

def _phoenix_graphql(base_url, query, variables=None):
    """POST a GraphQL query to Phoenix and return the data dict."""
    resp = requests.post(
        f"{base_url}/graphql",
        json={"query": query, "variables": variables or {}},
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    if "errors" in result:
        raise RuntimeError(f"GraphQL errors: {result['errors']}")
    return result["data"]


def _find_project_id(base_url, project_name="hermes-agent"):
    """Find a Phoenix project ID by name, or None."""
    data = _phoenix_graphql(base_url, """
        { projects { edges { node { id name } } } }
    """)
    for edge in data["projects"]["edges"]:
        if edge["node"]["name"] == project_name:
            return edge["node"]["id"]
    return None


def _query_spans_since(base_url, project_id, since, limit=20):
    """Query spans from a Phoenix project that started after *since* (ISO 8601)."""
    data = _phoenix_graphql(base_url, """
        query ($projectId: ID!, $first: Int!, $start: DateTime!) {
          node(id: $projectId) {
            ... on Project {
              spans(
                first: $first,
                sort: { col: startTime, dir: desc },
                timeRange: { start: $start }
              ) {
                edges {
                  node {
                    name
                    statusCode
                    parentId
                    context { traceId spanId }
                    startTime
                  }
                }
              }
            }
          }
        }
    """, variables={"projectId": project_id, "first": limit, "start": since})
    edges = data.get("node", {}).get("spans", {}).get("edges", [])
    return [e["node"] for e in edges]


def _wait_for_spans(base_url, project_id, since, expected_min=1,
                    timeout=30, interval=3):
    """Poll Phoenix until at least expected_min spans appear after *since*."""
    deadline = time.time() + timeout
    spans = []
    while time.time() < deadline:
        spans = _query_spans_since(base_url, project_id, since)
        if len(spans) >= expected_min:
            return spans
        time.sleep(interval)
    return spans


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def phoenix_api():
    """Connect to a running Phoenix instance. Skip if not reachable."""
    base_url = "http://localhost:6006"
    try:
        resp = requests.get(base_url, timeout=5)
        resp.raise_for_status()
    except Exception:
        pytest.skip("Phoenix not reachable at http://localhost:6006")

    project_id = _find_project_id(base_url)
    if not project_id:
        pytest.skip("No 'hermes-agent' project found in Phoenix")

    return {"base_url": base_url, "project_id": project_id}


@pytest.fixture(scope="module")
def hermes_client():
    """Connect to a running hermes API server. Skip if not reachable."""
    import os

    base_url = "http://127.0.0.1:8642"
    try:
        resp = requests.get(f"{base_url}/health", timeout=5)
        resp.raise_for_status()
    except Exception:
        pytest.skip("Hermes API server not reachable at http://127.0.0.1:8642")

    def _load_dotenv(path):
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

    hermes_env = _load_dotenv(os.path.expanduser("~/.hermes/.env"))
    api_key = (os.environ.get("API_SERVER_KEY")
               or hermes_env.get("API_SERVER_KEY")
               or "not-required")

    return OpenAI(base_url=f"{base_url}/v1", api_key=api_key)


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
class TestHermesPhoenixSmoke:

    def test_chat_produces_trace_in_phoenix(self, hermes_client, phoenix_api):
        """Send a chat via OpenAI SDK and verify spans appear in Phoenix."""
        before_time = datetime.now(timezone.utc).isoformat()

        response = hermes_client.chat.completions.create(
            model="hermes-agent",
            messages=[
                {"role": "user", "content": "Reply with exactly: PHOENIX_SMOKE_OK"},
            ],
            max_tokens=50,
        )

        reply = response.choices[0].message.content
        assert reply is not None and len(reply) > 0

        # Phoenix exports synchronously (SimpleSpanProcessor), so spans
        # should appear quickly — but give a small buffer.
        spans = _wait_for_spans(
            phoenix_api["base_url"], phoenix_api["project_id"],
            since=before_time, expected_min=1, timeout=30,
        )

        span_names = [s["name"] for s in spans]
        has_api_or_llm = any("api." in n or "llm." in n for n in span_names)
        assert has_api_or_llm, (
            f"Expected api.* or llm.* spans in Phoenix after chat, got: {span_names}"
        )

    def test_tool_use_produces_tool_span(self, hermes_client, phoenix_api):
        """Send a prompt that triggers tool use and verify tool spans in Phoenix."""
        before_time = datetime.now(timezone.utc).isoformat()

        response = hermes_client.chat.completions.create(
            model="hermes-agent",
            messages=[
                {"role": "user", "content": "Run the command `echo hello_phoenix_test` in the terminal and tell me the output."},
            ],
            max_tokens=200,
        )

        reply = response.choices[0].message.content
        assert reply is not None and len(reply) > 0

        spans = _wait_for_spans(
            phoenix_api["base_url"], phoenix_api["project_id"],
            since=before_time, expected_min=2, timeout=45,
        )

        span_names = [s["name"] for s in spans]
        has_tool = any("tool." in n for n in span_names)
        assert has_tool, (
            f"Expected tool.* spans in Phoenix from tool use, got: {span_names}"
        )

    def test_spans_have_parent_hierarchy(self, hermes_client, phoenix_api):
        """Verify that spans from a chat have correct parent-child nesting."""
        before_time = datetime.now(timezone.utc).isoformat()

        hermes_client.chat.completions.create(
            model="hermes-agent",
            messages=[
                {"role": "user", "content": "Say hi."},
            ],
            max_tokens=20,
        )

        spans = _wait_for_spans(
            phoenix_api["base_url"], phoenix_api["project_id"],
            since=before_time, expected_min=1, timeout=30,
        )

        # At least one span should have a parent (child span under session or LLM)
        has_child = any(s["parentId"] is not None for s in spans)
        assert has_child, (
            f"Expected nested spans (parentId set), but all are root spans: "
            f"{[(s['name'], s['parentId']) for s in spans]}"
        )
