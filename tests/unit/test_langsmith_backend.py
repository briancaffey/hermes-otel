"""Tests for the LangSmith backend module.

All HTTP calls are mocked via urllib.request.urlopen — no real network I/O.
"""

import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest
from hermes_otel.langsmith_backend import (
    LangSmithBackend,
    _coerce_int,
    _uuid_to_str,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

class TestCoerceInt:
    def test_int(self):
        assert _coerce_int(42) == 42

    def test_float(self):
        assert _coerce_int(3.7) == 3

    def test_string(self):
        assert _coerce_int("100") == 100

    def test_empty_string(self):
        assert _coerce_int("") is None

    def test_none(self):
        assert _coerce_int(None) is None

    def test_bool(self):
        assert _coerce_int(True) is None

    def test_non_numeric(self):
        assert _coerce_int("abc") is None


class TestUuidToStr:
    def test_uuid_with_hex(self):
        import uuid
        u = uuid.uuid4()
        assert _uuid_to_str(u) == u.hex

    def test_string_with_dashes(self):
        assert _uuid_to_str("a1b2c3d4-e5f6-7890-abcd-ef1234567890") == "a1b2c3d4e5f67890abcdef1234567890"


# ── from_env ─────────────────────────────────────────────────────────────────

class TestFromEnv:
    def test_returns_backend_when_configured(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")
        monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://custom.smith.com")
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        monkeypatch.setenv("LANGSMITH_WORKSPACE_ID", "ws-123")

        backend = LangSmithBackend.from_env()
        assert backend is not None
        assert backend.api_key == "lsv2_test"
        assert backend.endpoint == "https://custom.smith.com"
        assert backend.project == "my-project"
        assert backend.workspace == "ws-123"

    def test_returns_none_when_tracing_disabled(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "false")
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")
        assert LangSmithBackend.from_env() is None

    def test_returns_none_when_no_api_key(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        assert LangSmithBackend.from_env() is None

    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")
        monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
        monkeypatch.delenv("LANGSMITH_WORKSPACE_ID", raising=False)

        backend = LangSmithBackend.from_env()
        assert backend.endpoint == "https://api.smith.langchain.com"
        assert backend.project == "hermes-langsmith-otel"
        assert backend.workspace is None


# ── Headers ──────────────────────────────────────────────────────────────────

class TestHeaders:
    def test_basic_headers(self):
        backend = LangSmithBackend(api_key="key123", endpoint="https://x.com",
                                   project="proj")
        headers = backend._headers()
        assert headers["x-api-key"] == "key123"
        assert headers["Content-Type"] == "application/json"
        assert "x-tenant-id" not in headers

    def test_workspace_header(self):
        backend = LangSmithBackend(api_key="key", endpoint="https://x.com",
                                   project="proj", workspace="ws-1")
        headers = backend._headers()
        assert headers["x-tenant-id"] == "ws-1"


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _mock_urlopen_success():
    """Return a mock urlopen that returns a 200 response."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = b""
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return patch("hermes_otel.langsmith_backend.urllib.request.urlopen",
                 return_value=mock_resp)


def _mock_urlopen_error(code=400, reason="Bad Request", body=b"error"):
    """Return a mock urlopen that raises HTTPError."""
    error = HTTPError(
        url="https://x.com/runs",
        code=code,
        msg=reason,
        hdrs={},
        fp=io.BytesIO(body),
    )
    return patch("hermes_otel.langsmith_backend.urllib.request.urlopen",
                 side_effect=error)


def _captured_payload(mock_urlopen) -> dict:
    """Extract the JSON payload from the Request passed to urlopen."""
    req = mock_urlopen.call_args[0][0]
    return json.loads(req.data.decode("utf-8"))


class TestPost:
    def test_success(self):
        backend = LangSmithBackend(api_key="k", endpoint="https://api.smith.com",
                                   project="p")
        with _mock_urlopen_success() as mock:
            assert backend._post("/runs", {"id": "abc"}) is True
            req = mock.call_args[0][0]
            assert req.full_url == "https://api.smith.com/runs"
            assert req.method == "POST"

    def test_http_error_returns_false(self):
        backend = LangSmithBackend(api_key="k", endpoint="https://api.smith.com",
                                   project="p")
        with _mock_urlopen_error(400):
            assert backend._post("/runs", {"id": "abc"}) is False


class TestPatch:
    def test_success(self):
        backend = LangSmithBackend(api_key="k", endpoint="https://api.smith.com",
                                   project="p")
        with _mock_urlopen_success() as mock:
            assert backend._patch("/runs/abc", {"end_time": "t"}) is True
            req = mock.call_args[0][0]
            assert req.full_url == "https://api.smith.com/runs/abc"
            assert req.method == "PATCH"

    def test_http_error_returns_false(self):
        backend = LangSmithBackend(api_key="k", endpoint="https://api.smith.com",
                                   project="p")
        with _mock_urlopen_error(500, "Server Error"):
            assert backend._patch("/runs/abc", {}) is False


# ── start_span ───────────────────────────────────────────────────────────────

class TestStartSpan:
    def _backend(self):
        return LangSmithBackend(api_key="key", endpoint="https://api.smith.com",
                                project="test-proj")

    def test_creates_run_and_returns_dict(self):
        backend = self._backend()
        with _mock_urlopen_success() as mock:
            result = backend.start_span("llm.gpt-4", "llm:s1", kind="llm",
                                        attributes={"model": "gpt-4"})

        assert result is not None
        assert "id" in result
        assert result["run_type"] == "llm"
        assert result["parent_id"] is None

        payload = _captured_payload(mock)
        assert payload["name"] == "llm.gpt-4"
        assert payload["run_type"] == "llm"
        assert payload["session_name"] == "test-proj"
        assert payload["inputs"]["model"] == "gpt-4"
        assert payload["extra"]["metadata"]["hermes_key"] == "llm:s1"

    def test_tool_kind_preserved(self):
        backend = self._backend()
        with _mock_urlopen_success() as mock:
            result = backend.start_span("tool.bash", "bash:t1", kind="tool")

        payload = _captured_payload(mock)
        assert payload["run_type"] == "tool"

    def test_unknown_kind_becomes_chain(self):
        backend = self._backend()
        with _mock_urlopen_success() as mock:
            backend.start_span("agent", "session:s1", kind="agent")

        payload = _captured_payload(mock)
        assert payload["run_type"] == "chain"

    def test_parent_run_sets_parent_run_id(self):
        backend = self._backend()
        parent = {"id": "parent123", "run_type": "chain"}
        with _mock_urlopen_success() as mock:
            result = backend.start_span("api.gpt-4", "api:t1", kind="llm",
                                        parent_run=parent)

        assert result["parent_id"] == "parent123"
        payload = _captured_payload(mock)
        assert payload["parent_run_id"] == "parent123"

    def test_no_parent_omits_parent_run_id(self):
        backend = self._backend()
        with _mock_urlopen_success() as mock:
            backend.start_span("agent", "session:s1", kind="agent")

        payload = _captured_payload(mock)
        assert "parent_run_id" not in payload

    def test_returns_none_on_http_error(self):
        backend = self._backend()
        with _mock_urlopen_error(400):
            result = backend.start_span("llm.gpt-4", "llm:s1", kind="llm")
        assert result is None


# ── end_span ─────────────────────────────────────────────────────────────────

class TestEndSpan:
    def _backend(self):
        return LangSmithBackend(api_key="key", endpoint="https://api.smith.com",
                                project="test-proj")

    def test_patches_run_with_end_time(self):
        backend = self._backend()
        run = {"id": "abc123", "run_type": "llm"}
        with _mock_urlopen_success() as mock:
            backend.end_span(run, attributes={"output.value": "hello"})

        req = mock.call_args[0][0]
        assert "/runs/abc123" in req.full_url
        assert req.method == "PATCH"
        payload = _captured_payload(mock)
        assert "end_time" in payload
        assert payload["outputs"]["output.value"] == "hello"

    def test_error_status_sets_error_field(self):
        backend = self._backend()
        run = {"id": "abc", "run_type": "tool"}
        with _mock_urlopen_success() as mock:
            backend.end_span(run, status="error", error_message="tool crashed")

        payload = _captured_payload(mock)
        assert payload["error"] == "tool crashed"

    def test_ok_status_no_error_field(self):
        backend = self._backend()
        run = {"id": "abc", "run_type": "llm"}
        with _mock_urlopen_success() as mock:
            backend.end_span(run, status="ok")

        payload = _captured_payload(mock)
        assert "error" not in payload

    def test_token_mapping_from_openinference(self):
        backend = self._backend()
        run = {"id": "abc", "run_type": "llm"}
        attrs = {
            "llm.token_count.prompt": 100,
            "llm.token_count.completion": 50,
            "llm.token_count.total": 150,
        }
        with _mock_urlopen_success() as mock:
            backend.end_span(run, attributes=attrs)

        payload = _captured_payload(mock)
        assert payload["prompt_tokens"] == 100
        assert payload["completion_tokens"] == 50
        assert payload["total_tokens"] == 150

    def test_token_mapping_from_gen_ai(self):
        backend = self._backend()
        run = {"id": "abc", "run_type": "llm"}
        attrs = {
            "gen_ai.usage.input_tokens": 200,
            "gen_ai.usage.output_tokens": 80,
            "gen_ai.usage.total_tokens": 280,
        }
        with _mock_urlopen_success() as mock:
            backend.end_span(run, attributes=attrs)

        payload = _captured_payload(mock)
        assert payload["prompt_tokens"] == 200
        assert payload["completion_tokens"] == 80
        assert payload["total_tokens"] == 280

    def test_usage_metadata_populated(self):
        backend = self._backend()
        run = {"id": "abc", "run_type": "llm"}
        attrs = {
            "gen_ai.usage.input_tokens": 100,
            "gen_ai.usage.output_tokens": 50,
            "gen_ai.usage.total_tokens": 150,
        }
        with _mock_urlopen_success() as mock:
            backend.end_span(run, attributes=attrs)

        payload = _captured_payload(mock)
        um = payload["usage_metadata"]
        assert um["input_tokens"] == 100
        assert um["output_tokens"] == 50
        assert um["total_tokens"] == 150

    def test_cache_tokens_in_usage_metadata(self):
        backend = self._backend()
        run = {"id": "abc", "run_type": "llm"}
        attrs = {
            "gen_ai.usage.input_tokens": 100,
            "gen_ai.usage.output_tokens": 50,
            "gen_ai.usage.total_tokens": 150,
            "gen_ai.usage.cache_read_input_tokens": 30,
            "gen_ai.usage.cache_creation_input_tokens": 10,
        }
        with _mock_urlopen_success() as mock:
            backend.end_span(run, attributes=attrs)

        payload = _captured_payload(mock)
        details = payload["usage_metadata"]["input_token_details"]
        assert details["cache_read"] == 30
        assert details["cache_creation"] == 10

    def test_no_crash_on_missing_run_id(self):
        backend = self._backend()
        backend.end_span({}, attributes={"output.value": "x"})  # no "id" key

    def test_no_crash_on_http_error(self):
        backend = self._backend()
        run = {"id": "abc", "run_type": "llm"}
        with _mock_urlopen_error(500, "Server Error"):
            backend.end_span(run, attributes={"output.value": "x"})
