"""End-to-end plugin test for the LangSmith backend.

LangSmith uses its own HTTP Run API rather than OTLP, so it bypasses
the TracerProvider / BatchSpanProcessor path entirely. This test drives
the full hook chain (``on_session_start`` → ``on_pre_llm_call`` →
``on_pre_api_request`` → ``on_pre_tool_call`` → ``on_post_*`` →
``on_session_end``) and asserts that the resulting POST ``/runs`` and
PATCH ``/runs/{id}`` requests carry the expected payloads *and* parent
linkage.

``urllib.request.urlopen`` is mocked at the LangSmith-backend level so
no real network I/O happens.
"""

from __future__ import annotations

import json
from typing import Dict, List, Tuple
from unittest.mock import MagicMock, patch

import pytest
from hermes_otel.hooks import (
    on_post_api_request,
    on_post_llm_call,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_llm_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)
from hermes_otel.langsmith_backend import LangSmithBackend
from hermes_otel.tracer import HermesOTelPlugin


class _RecordingUrlopen:
    """Callable stand-in for ``urllib.request.urlopen`` that records calls.

    Returns a fake 200 response; tests inspect ``.requests`` to see what
    the backend sent, including method, URL, and JSON payload.
    """

    def __init__(self):
        self.requests: List[Tuple[str, str, Dict]] = []

    def __call__(self, req, timeout=None):
        try:
            payload = json.loads(req.data.decode("utf-8")) if req.data else {}
        except Exception:
            payload = {}
        self.requests.append((req.method, req.full_url, payload))
        resp = MagicMock()
        resp.read.return_value = b""
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    # Filtering helpers for readable assertions.
    def posts(self) -> List[Dict]:
        return [body for (m, _, body) in self.requests if m == "POST"]

    def patches(self) -> List[Dict]:
        return [body for (m, _, body) in self.requests if m == "PATCH"]


@pytest.fixture()
def langsmith_plugin():
    """Plugin wired to a LangSmith backend with mocked HTTP.

    Returns ``(recorder, plugin)``. The recorder captures every POST /
    PATCH the LangSmith backend emits.
    """
    import hermes_otel.tracer as tracer_mod

    recorder = _RecordingUrlopen()
    backend = LangSmithBackend(
        api_key="lsv2_test_key",
        endpoint="https://api.smith.test",
        project="hermes-otel-test",
    )
    plugin = HermesOTelPlugin()
    plugin._langsmith = backend
    plugin._initialized = True
    tracer_mod._tracer = plugin

    with patch("hermes_otel.langsmith_backend.urllib.request.urlopen", recorder):
        yield recorder, plugin


class TestLangSmithPluginFlow:
    """The full session hook chain produces the expected LangSmith API calls."""

    def _fire_complete_session(self, session_id: str = "s1") -> None:
        """Drive one session through every hook the plugin registers."""
        on_session_start(session_id=session_id, model="gpt-4", platform="cli")
        on_pre_llm_call(
            session_id=session_id,
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
        )
        on_pre_api_request(
            task_id="t1",
            session_id=session_id,
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            message_count=1,
            tool_count=1,
            approx_input_tokens=100,
            request_char_count=500,
            max_tokens=512,
        )
        on_pre_tool_call(
            tool_name="bash", args={"command": "ls"}, task_id="tool1", session_id=session_id
        )
        on_post_tool_call(
            tool_name="bash",
            args={"command": "ls"},
            result="file.txt",
            task_id="tool1",
            session_id=session_id,
        )
        on_post_api_request(
            task_id="t1",
            session_id=session_id,
            platform="cli",
            model="gpt-4",
            provider="openai",
            base_url="",
            api_mode="chat",
            api_call_count=1,
            api_duration=0.25,
            finish_reason="stop",
            message_count=1,
            response_model="gpt-4",
            usage={"prompt_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            assistant_content_chars=20,
            assistant_tool_call_count=1,
        )
        on_post_llm_call(
            session_id=session_id,
            user_message="hello",
            assistant_response="done",
            conversation_history=[],
            model="gpt-4",
            platform="cli",
        )
        on_session_end(
            session_id=session_id,
            completed=True,
            interrupted=False,
            model="gpt-4",
            platform="cli",
        )

    def test_posts_one_run_per_span(self, langsmith_plugin):
        """Each start_span → one POST /runs; 4 spans = 4 creates."""
        recorder, _ = langsmith_plugin
        self._fire_complete_session()

        posts = recorder.posts()
        # agent (session) + llm + api + tool = 4 runs created.
        assert len(posts) == 4
        names = [p["name"] for p in posts]
        assert "agent" in names
        assert "llm.gpt-4" in names
        assert "api.gpt-4" in names
        assert "tool.bash" in names

    def test_patches_one_per_span(self, langsmith_plugin):
        """Each end_span → one PATCH /runs/{id}; 4 spans = 4 updates."""
        recorder, _ = langsmith_plugin
        self._fire_complete_session()
        assert len(recorder.patches()) == 4

    def test_parent_run_id_chain(self, langsmith_plugin):
        """Child runs reference their parent via ``parent_run_id``.

        Expected chain: agent (root, no parent) → llm → api → tool.
        """
        recorder, _ = langsmith_plugin
        self._fire_complete_session()
        posts = recorder.posts()
        by_name = {p["name"]: p for p in posts}

        agent_id = by_name["agent"]["id"]
        llm = by_name["llm.gpt-4"]
        api = by_name["api.gpt-4"]
        tool = by_name["tool.bash"]

        # Root span has no parent_run_id.
        assert "parent_run_id" not in by_name["agent"]
        # llm is rooted under agent.
        assert llm["parent_run_id"] == agent_id
        # api is rooted under llm.
        assert api["parent_run_id"] == llm["id"]
        # tool fires while api is the current parent.
        assert tool["parent_run_id"] == api["id"]

    def test_token_usage_on_api_patch(self, langsmith_plugin):
        """Token counts land on the api span's PATCH via LangSmith's usage_metadata."""
        recorder, _ = langsmith_plugin
        self._fire_complete_session()

        posts = recorder.posts()
        api_id = next(p["id"] for p in posts if p["name"] == "api.gpt-4")

        # Find the PATCH for that run_id in the request log.
        api_patch = None
        for method, url, body in recorder.requests:
            if method == "PATCH" and url.endswith(f"/runs/{api_id}"):
                api_patch = body
                break
        assert api_patch is not None, "no PATCH for api span"

        # Legacy top-level fields.
        assert api_patch["prompt_tokens"] == 100
        assert api_patch["completion_tokens"] == 50
        assert api_patch["total_tokens"] == 150
        # Modern usage_metadata field.
        assert api_patch["usage_metadata"]["input_tokens"] == 100
        assert api_patch["usage_metadata"]["output_tokens"] == 50
        assert api_patch["usage_metadata"]["total_tokens"] == 150
