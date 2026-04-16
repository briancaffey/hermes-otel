"""LangSmith backend — exports spans via LangSmith's HTTP Run API.

Unlike Phoenix and Langfuse which use OTLP, LangSmith has its own REST API:
  POST  /runs          — create a run (span start)
  PATCH /runs/{run_id} — update a run with outputs/end_time (span end)

This module is used by tracer.HermesOTelPlugin when LangSmith is the
configured backend (LANGSMITH_TRACING=true).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .debug_utils import debug_log

# Prefer langsmith uuid7 for time-ordered run IDs
try:
    from langsmith import uuid7 as _ls_uuid7
    UUID7_AVAILABLE = True
except ImportError:
    UUID7_AVAILABLE = False
    _ls_uuid7 = None


def _uuid_to_str(run_id) -> str:
    """Convert a UUID/uuid7 object to a hex string (no dashes)."""
    if hasattr(run_id, "hex"):
        return run_id.hex
    return str(run_id).replace("-", "")


def _coerce_int(value) -> Optional[int]:
    """Best-effort int conversion for usage values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


class LangSmithBackend:
    """Manages span export to LangSmith via its HTTP Run API."""

    def __init__(self, api_key: str, endpoint: str, project: str,
                 workspace: Optional[str] = None):
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self.project = project
        self.workspace = workspace

    @classmethod
    def from_env(cls) -> Optional["LangSmithBackend"]:
        """Create a LangSmithBackend from environment variables, or None."""
        api_key = os.getenv("LANGSMITH_API_KEY", "").strip()
        tracing = os.getenv("LANGSMITH_TRACING", "").strip().lower()

        if tracing != "true" or not api_key:
            return None

        endpoint = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com").strip()
        project = os.getenv("LANGSMITH_PROJECT", "hermes-langsmith-otel").strip()
        workspace = os.getenv("LANGSMITH_WORKSPACE_ID", "").strip() or None

        debug_log(f"LangSmith: endpoint={endpoint}, project={project}, uuid7={UUID7_AVAILABLE}")

        return cls(api_key=api_key, endpoint=endpoint, project=project,
                   workspace=workspace)

    # ── HTTP helpers ─────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        """Build HTTP headers for LangSmith API requests."""
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        if self.workspace:
            headers["x-tenant-id"] = self.workspace
        return headers

    def _post(self, path: str, payload: dict) -> bool:
        """POST JSON to LangSmith. Returns True on success."""
        url = f"{self.endpoint}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(),
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            return True
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            debug_log(f"LangSmith POST {path} failed: {e.code} {e.reason} — {body[:200]}")
            return False

    def _patch(self, path: str, payload: dict) -> bool:
        """PATCH JSON to LangSmith. Returns True on success."""
        url = f"{self.endpoint}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(),
                                     method="PATCH")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            return True
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            debug_log(f"LangSmith PATCH {path} failed: {e.code} {e.reason} — {body[:200]}")
            return False

    # ── Span lifecycle ───────────────────────────────────────────────────

    def start_span(self, name: str, key: str, kind: str = "general",
                   attributes: dict = None,
                   parent_run: Optional[dict] = None) -> Optional[dict]:
        """Create a LangSmith run. Returns run dict or None on failure.

        The caller is responsible for storing the returned dict in SpanTracker
        and managing the parent stack.
        """
        try:
            attrs = dict(attributes or {})

            if UUID7_AVAILABLE:
                run_id = _ls_uuid7()
            else:
                run_id = uuid.uuid4()

            run_id_str = _uuid_to_str(run_id)
            start_time = datetime.now(timezone.utc).isoformat()

            payload = {
                "id": run_id_str,
                "name": name,
                "run_type": kind if kind in ["llm", "tool"] else "chain",
                "inputs": attrs,
                "start_time": start_time,
                "session_name": self.project,
                "extra": {
                    "metadata": {
                        "hermes_key": key,
                    }
                },
            }

            if parent_run and isinstance(parent_run, dict) and "id" in parent_run:
                payload["parent_run_id"] = parent_run["id"]

            if not self._post("/runs", payload):
                return None

            run_obj = {
                "id": run_id_str,
                "run_type": kind,
                "parent_id": parent_run.get("id") if parent_run else None,
            }
            debug_log(f"LangSmith start_span: {name} (key={key}, run_id={run_id_str[:8]}...)")
            return run_obj

        except Exception as e:
            debug_log(f"Error starting LangSmith span '{name}': {e}")
            return None

    def end_span(self, run: dict, attributes: dict = None,
                 status: str = None, error_message: str = None) -> None:
        """Update (close) a LangSmith run via PATCH.

        The caller is responsible for removing the run from SpanTracker.
        """
        run_id = run.get("id")
        if not run_id:
            return

        try:
            outputs = dict(attributes or {})

            payload: Dict[str, Any] = {
                "end_time": datetime.now(timezone.utc).isoformat(),
                "outputs": outputs,
            }

            if status == "error" and error_message:
                payload["error"] = error_message

            # Map OTel-style token attributes to LangSmith's top-level fields.
            for src_key, dst_key in [
                ("llm.token_count.prompt", "prompt_tokens"),
                ("llm.token_count.completion", "completion_tokens"),
                ("llm.token_count.total", "total_tokens"),
                ("gen_ai.usage.input_tokens", "prompt_tokens"),
                ("gen_ai.usage.output_tokens", "completion_tokens"),
                ("gen_ai.usage.total_tokens", "total_tokens"),
            ]:
                if src_key in outputs:
                    val = _coerce_int(outputs.get(src_key))
                    if val is not None:
                        payload[dst_key] = val

            # Newer LangSmith ingestion path: usage_metadata
            usage_metadata: Dict[str, Any] = {}
            token_map = {
                "input_tokens": payload.get("prompt_tokens"),
                "output_tokens": payload.get("completion_tokens"),
                "total_tokens": payload.get("total_tokens"),
            }
            for k, v in token_map.items():
                if v is not None:
                    usage_metadata[k] = v

            cache_read = _coerce_int(outputs.get("gen_ai.usage.cache_read_input_tokens"))
            cache_write = _coerce_int(outputs.get("gen_ai.usage.cache_creation_input_tokens"))
            if cache_read is not None:
                usage_metadata["input_token_details"] = {"cache_read": cache_read}
            if cache_write is not None:
                usage_metadata.setdefault("input_token_details", {})
                usage_metadata["input_token_details"]["cache_creation"] = cache_write

            if usage_metadata:
                payload["usage_metadata"] = usage_metadata

            debug_log(f"LangSmith PATCH run_id={run_id[:8]}... "
                      f"payload_keys={list(payload.keys())}")
            if "prompt_tokens" in payload:
                debug_log(f"  tokens: prompt={payload['prompt_tokens']}, "
                          f"completion={payload.get('completion_tokens')}, "
                          f"total={payload.get('total_tokens')}")

            self._patch(f"/runs/{run_id}", payload)

        except Exception as e:
            debug_log(f"Error ending LangSmith span (run_id={run_id[:8]}...): {e}")
