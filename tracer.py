"""OpenTelemetry tracer for Hermes plugin.

Provides singleton tracer manager with span tracking between
pre/post hook calls.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from .debug_utils import debug_log
except ImportError:  # pragma: no cover - flat-module fallback for packaging
    from debug_utils import debug_log

debug_log("tracer.py module loaded")

# Try langsmith uuid7 (better time-ordered UUIDs) if the package is installed
try:
    from langsmith import uuid7 as _ls_uuid7
    _LANGSMITH_UUID7_AVAILABLE = True
except ImportError:
    _LANGSMITH_UUID7_AVAILABLE = False
    _ls_uuid7 = None


def _uuid_to_str(run_id) -> str:
    """Convert a UUID/uuid7 object to a hex string (no dashes)."""
    if hasattr(run_id, 'hex'):
        return run_id.hex
    return str(run_id).replace("-", "")

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode, set_span_in_context
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    print("[hermes-otel] OpenTelemetry packages not installed. Run: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http")


class NoopSpan:
    """No-op span fallback."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status_code, description: str = "") -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass

    def end(self) -> None:
        pass


class SpanTracker:
    """Track active spans between pre/post hook calls.

    Since hooks fire independently, we need a registry to look up
    the active span when the post_* hook fires. Also tracks the
    current "parent" span so child spans nest correctly.
    """

    def __init__(self):
        # key = f"{tool_name}:{task_id}" or f"llm:{session_id}"
        self._active_spans: Dict[str, Any] = {}
        # Stack of parent span contexts (LLM spans push here)
        self._parent_stack: list = []

    def start_span(self, key: str, span, parent=None) -> None:
        """Store an active span by key."""
        self._active_spans[key] = span
        if parent:
            span._otel_parent = parent

    def push_parent(self, span) -> None:
        """Mark a span as the current parent (e.g., LLM call)."""
        self._parent_stack.append(span)

    def pop_parent(self) -> None:
        """Remove the current parent span."""
        if self._parent_stack:
            self._parent_stack.pop()

    def get_current_parent(self):
        """Get the active parent span, or None."""
        return self._parent_stack[-1] if self._parent_stack else None

    def end_span(self, key: str, attributes: dict = None, status: str = None, error_message: str = None) -> None:
        """End and remove a tracked span.

        Args:
            key: The tracking key for the span
            attributes: Final attributes to set before ending
            status: "ok" or "error" (defaults to "ok" if None)
            error_message: Error description if status is "error"
        """
        span = self._active_spans.pop(key, None)
        if span:
            if attributes:
                for k, v in attributes.items():
                    span.set_attribute(k, v)

            # Set status
            if status == "error":
                span.set_status(Status(status_code=StatusCode.ERROR, description=error_message or ""))
            elif status == "ok":
                # Set an explicit empty description so backends don't render "None".
                span.set_status(Status(status_code=StatusCode.OK, description=""))

            span.end()

    def get_span(self, key):
        """Get an active span by key."""
        return self._active_spans.get(key)

    def end_all(self) -> None:
        """End all remaining spans (cleanup)."""
        for key in list(self._active_spans.keys()):
            self.end_span(key)
        self._active_spans.clear()
        self._parent_stack.clear()


class HermesOTelPlugin:
    """OpenTelemetry tracer manager for the Hermes plugin.

    Uses a global TracerProvider set up once at plugin registration.
    All hooks share this instance via the module-level get_tracer().
    """

    _instance: Optional["HermesOTelPlugin"] = None

    # OpenInference semantic convention values — Phoenix recognizes these
    _KIND_MAP = {
        "tool": "TOOL",
        "llm": "LLM",
        "general": "GENERAL",
        "agent": "AGENT",
    }

    def __init__(self):
        self.tracer = None
        self.spans = SpanTracker()
        self._initialized = False
        self._span_processor = None
        # LangSmith — HTTP only
        self._langsmith_api_key = None
        self._langsmith_endpoint = None
        self._langsmith_project = None
        self._langsmith_workspace = None

    def init(self, endpoint: str = None) -> bool:
        """Initialize OTel tracer with the configured endpoint.

        Supports three configurations:
          - Phoenix (default): OTEL_ENDPOINT env var
          - Langfuse: OTEL_LANGFUSE_* or standard LANGFUSE_* env vars
          - LangSmith: LANGSMITH_TRACING=true + LANGSMITH_API_KEY

        Args:
            endpoint: OTLP trace endpoint from OTEL_ENDPOINT env var (Phoenix mode).

        Returns:
            True if initialized, False otherwise.
        """
        if not _OTEL_AVAILABLE:
            print("[hermes-otel] ✗ OpenTelemetry packages not available")
            return False

        # Detect LangSmith configuration
        langsmith_tracing = os.getenv("LANGSMITH_TRACING", "").strip().lower()
        langsmith_api_key = os.getenv("LANGSMITH_API_KEY", "").strip()

        use_langsmith = langsmith_tracing == "true" and bool(langsmith_api_key)

        if use_langsmith:
            return self._init_langsmith()

        # Detect Langfuse configuration.
        # Supports both plugin-specific OTEL_LANGFUSE_* variables and
        # Langfuse-standard LANGFUSE_* variables from the official docs.
        langfuse_endpoint = os.getenv("OTEL_LANGFUSE_ENDPOINT", "").strip()
        langfuse_public_key = os.getenv("OTEL_LANGFUSE_PUBLIC_API_KEY", "").strip()
        langfuse_secret_key = os.getenv("OTEL_LANGFUSE_SECRET_API_KEY", "").strip()

        if not langfuse_public_key:
            langfuse_public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        if not langfuse_secret_key:
            langfuse_secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        if not langfuse_endpoint:
            base_url = os.getenv("LANGFUSE_BASE_URL", "").strip().rstrip("/")
            if base_url:
                langfuse_endpoint = f"{base_url}/api/public/otel"

        use_langfuse = bool(langfuse_public_key and langfuse_secret_key)

        if use_langfuse:
            return self._init_langfuse(langfuse_endpoint, langfuse_public_key, langfuse_secret_key)

        # Phoenix mode (default)
        if not endpoint:
            endpoint = os.getenv("OTEL_ENDPOINT", "")

        if not endpoint:
            print("[hermes-otel] ✗ Neither OTEL_ENDPOINT, Langfuse credentials, nor LangSmith config found")
            return False

        return self._init_phoenix(endpoint)

    def _init_langsmith(self) -> bool:
        """Initialize LangSmith tracer via HTTP API.

        Environment variables used:
          - LANGSMITH_TRACING=true (required)
          - LANGSMITH_API_KEY (required)
          - LANGSMITH_ENDPOINT (optional, defaults to https://api.smith.langchain.com)
          - LANGSMITH_PROJECT (optional, project name)
          - LANGSMITH_WORKSPACE_ID (optional, for multi-workspace API keys)
        """
        try:
            api_key = os.getenv("LANGSMITH_API_KEY", "").strip()
            endpoint = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com").strip()
            project = os.getenv("LANGSMITH_PROJECT", "hermes-langsmith-otel").strip()
            workspace = os.getenv("LANGSMITH_WORKSPACE_ID", "").strip()

            print(f"[hermes-otel] Initializing LangSmith at {endpoint}...")
            print(f"[hermes-otel]   Project: {project}")
            print(f"[hermes-otel]   uuid7 available: {_LANGSMITH_UUID7_AVAILABLE}")

            self._langsmith_api_key = api_key
            self._langsmith_endpoint = endpoint.rstrip("/")
            self._langsmith_project = project
            self._langsmith_workspace = workspace if workspace else None
            self._initialized = True

            print(f"[hermes-otel] ✓ LangSmith initialized")
            return True

        except Exception as e:
            print(f"[hermes-otel] ✗ LangSmith initialization failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _init_langfuse(self, endpoint: str, public_key: str, secret_key: str) -> bool:
        """Initialize Langfuse tracer."""
        try:
            if not endpoint:
                # Default to Langfuse Cloud EU endpoint
                endpoint = "https://cloud.langfuse.com/api/public/otel"

            print(f"[hermes-otel] Connecting to Langfuse at {endpoint}...")

            # Build Basic Auth header: base64(public_key:secret_key)
            auth_string = f"{public_key}:{secret_key}"
            auth_b64 = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")

            # Build resource attributes
            resource_attrs = {"service.name": "hermes-agent"}

            project_name = os.getenv("OTEL_PROJECT_NAME", "").strip()
            if project_name:
                resource_attrs["openinference.project.name"] = project_name
                print(f"[hermes-otel] Project: {project_name}")

            # Create tracer provider
            resource = Resource.create(resource_attrs)
            provider = TracerProvider(resource=resource)

            # Configure OTLP exporter with Langfuse headers
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "x-langfuse-ingestion-version": "4",
            }
            exporter = OTLPSpanExporter(
                endpoint=endpoint,
                headers=headers,
            )

            # Use SimpleSpanProcessor — exports synchronously on span end
            processor = SimpleSpanProcessor(exporter)
            provider.add_span_processor(processor)
            self._span_processor = processor

            # Set as global provider
            trace.set_tracer_provider(provider)

            # Get tracer
            self.tracer = trace.get_tracer("hermes-otel-plugin")
            self._initialized = True

            print(f"[hermes-otel] ✓ Connected to Langfuse at {endpoint}")
            return True

        except Exception as e:
            print(f"[hermes-otel] ✗ Langfuse initialization failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _init_phoenix(self, endpoint: str) -> bool:
        """Initialize Phoenix tracer (default OTLP mode)."""
        try:
            print(f"[hermes-otel] Connecting to {endpoint}...")

            # Build resource attributes
            resource_attrs = {"service.name": "hermes-agent"}

            project_name = os.getenv("OTEL_PROJECT_NAME", "").strip()
            if project_name:
                resource_attrs["openinference.project.name"] = project_name
                print(f"[hermes-otel] Project: {project_name}")

            # Create tracer provider
            resource = Resource.create(resource_attrs)
            provider = TracerProvider(resource=resource)

            # Configure OTLP exporter
            exporter = OTLPSpanExporter(endpoint=endpoint)

            # Use SimpleSpanProcessor — exports synchronously on span end
            processor = SimpleSpanProcessor(exporter)
            provider.add_span_processor(processor)
            self._span_processor = processor

            # Set as global provider
            trace.set_tracer_provider(provider)

            # Get tracer
            self.tracer = trace.get_tracer("hermes-otel-plugin")
            self._initialized = True

            print(f"[hermes-otel] ✓ Connected to {endpoint}")
            return True

        except Exception as e:
            print(f"[hermes-otel] ✗ Initialization failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def start_span(self, name: str, key: str, kind: str = "general", attributes: dict = None):
        """Create and track a new span."""
        if not self._initialized:
            print(f"[hermes-otel] Cannot start span: not initialized (name={name})")
            return NoopSpan()

        # LangSmith mode — HTTP only
        if self._langsmith_api_key:
            return self._start_langsmith_span(name, key, kind, attributes)

        # OTLP mode (Phoenix/Langfuse)
        if not self.tracer:
            print(f"[hermes-otel] Cannot start span: tracer not available (name={name})")
            return NoopSpan()

        print(f"[hermes-otel] start_span: name={name}, key={key}, kind={kind}")
        try:
            attrs = dict(attributes or {})

            # OpenInference semantic conventions — Phoenix recognizes these
            kind_value = self._KIND_MAP.get(kind, "GENERAL")
            attrs["traceloop.span.kind"] = kind_value
            attrs["openinference.span.kind"] = kind_value

            # Check for active parent — enables nesting
            parent = self.spans.get_current_parent()
            print(f"[hermes-otel] start_span parent check: name={name}, parent={type(parent).__name__ if parent else None}")
            span_ctx = None
            if parent is not None and hasattr(parent, "get_span_context"):
                print(f"[hermes-otel] Setting parent context for {name}")
                span_ctx = set_span_in_context(parent)

            span = self.tracer.start_span(name, attributes=attrs, context=span_ctx)
            self.spans.start_span(key, span)
            print(f"[hermes-otel] Started span: {name} (key={key}, kind={kind_value})")
            return span
        except Exception as e:
            print(f"[hermes-otel] Error starting span '{name}': {e}")
            import traceback
            traceback.print_exc()
            return NoopSpan()

    # ─── LangSmith HTTP ─────────────────────────────────────────────────

    def _start_langsmith_span(self, name: str, key: str, kind: str = "general", attributes: dict = None):
        """Start a span using LangSmith's HTTP API."""
        import urllib.request
        import urllib.error

        try:
            attrs = dict(attributes or {})
            parent_run = self.spans.get_current_parent()

            # Generate UUID v7 for time-ordered runs (uses langsmith.uuid7 if available)
            if _LANGSMITH_UUID7_AVAILABLE:
                run_id = _ls_uuid7()
            else:
                run_id = uuid.uuid4()

            start_time = datetime.now(timezone.utc).isoformat()
            run_id_str = _uuid_to_str(run_id)

            # Build run payload per LangSmith API docs
            payload = {
                "id": run_id_str,
                "name": name,
                "run_type": kind if kind in ["llm", "tool"] else "chain",
                "inputs": attrs,
                "start_time": start_time,
                "session_name": self._langsmith_project,
                "extra": {
                    "metadata": {
                        "hermes_key": key,
                    }
                },
            }

            # Add parent if exists
            if parent_run and isinstance(parent_run, dict) and "id" in parent_run:
                payload["parent_run_id"] = parent_run["id"]
                print(f"[hermes-otel] LangSmith nesting: {name} -> parent_run_id={parent_run['id'][:8]}...")
            else:
                print(f"[hermes-otel] LangSmith: no parent for {name} (parent_run={parent_run})")

            # Build headers
            headers = {
                "x-api-key": self._langsmith_api_key,
                "Content-Type": "application/json",
            }
            if self._langsmith_workspace:
                headers["x-tenant-id"] = self._langsmith_workspace

            # POST to create run
            runs_url = f"{self._langsmith_endpoint}/runs"
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                runs_url,
                data=data,
                headers=headers,
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                print(f"[hermes-otel] ✗ LangSmith run creation failed: {e.code} {e.reason} — {body[:200]}")
                return NoopSpan()

            # Store run info for later update, including parent ID for nesting
            parent_id = parent_run.get("id") if parent_run else None
            run_obj = {
                "id": run_id_str,
                "run_type": kind,
                "parent_id": parent_id,
            }
            self.spans.start_span(key, run_obj)
            print(f"[hermes-otel] Started LangSmith span: {name} (key={key}, run_id={run_id_str[:8]}...)")
            return run_obj

        except Exception as e:
            print(f"[hermes-otel] Error starting LangSmith span '{name}': {e}")
            import traceback
            traceback.print_exc()
            return NoopSpan()

    def _end_langsmith_span(self, key: str, attributes: dict = None, status: str = None, error_message: str = None):
        """End a LangSmith span via HTTP PATCH."""
        import urllib.request
        import urllib.error

        run = self.spans.get_span(key)
        if not run:
            print(f"[hermes-otel] LangSmith span not found: key={key}")
            return

        run_id = run.get("id")
        if not run_id:
            print(f"[hermes-otel] LangSmith span missing run_id: key={key}")
            return

        try:
            end_time = datetime.now(timezone.utc).isoformat()
            outputs = dict(attributes or {}) if attributes else {}
            usage_metadata = {}

            def _coerce_int(value):
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

            # Build PATCH payload per LangSmith API docs
            payload = {
                "end_time": end_time,
                "outputs": outputs,
            }

            if status == "error" and error_message:
                payload["error"] = error_message

            # Extract LangSmith token usage fields from attributes.
            # The hooks set OpenInference-style attributes (llm.token_count.*),
            # but LangSmith expects top-level integer fields:
            #   prompt_tokens, completion_tokens, total_tokens
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

            # Also send usage metadata (newer LangSmith ingestion path).
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

            # Debug: log what we're sending
            debug_log(f"LangSmith PATCH run_id={run_id[:8]}... payload_keys={list(payload.keys())}")
            if "prompt_tokens" in payload:
                debug_log(f"  tokens: prompt={payload['prompt_tokens']}, completion={payload.get('completion_tokens')}, total={payload.get('total_tokens')}")

            # Build headers
            headers = {
                "x-api-key": self._langsmith_api_key,
                "Content-Type": "application/json",
            }
            if self._langsmith_workspace:
                headers["x-tenant-id"] = self._langsmith_workspace

            # PATCH to update run
            runs_url = f"{self._langsmith_endpoint}/runs/{run_id}"
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                runs_url,
                data=data,
                headers=headers,
                method="PATCH",
            )

            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                print(f"[hermes-otel] ✗ LangSmith run update failed: {e.code} {e.reason} — {body[:200]}")

            # Remove from active spans directly (bypass SpanTracker.end_span
            # which tries to call .end() on the span — but our stored object
            # is a dict, not an OTel span)
            self.spans._active_spans.pop(key, None)

        except Exception as e:
            print(f"[hermes-otel] Error ending LangSmith span (key={key}): {e}")
            import traceback
            traceback.print_exc()

    def end_span(self, key: str, attributes: dict = None, status: str = None, error_message: str = None):
        """End a tracked span by its key."""
        try:
            # LangSmith mode — HTTP
            if self._langsmith_api_key:
                self._end_langsmith_span(key, attributes=attributes, status=status, error_message=error_message)
            else:
                # OTLP mode
                self.spans.end_span(key, attributes=attributes, status=status, error_message=error_message)
                self._force_flush()
            print(f"[hermes-otel] Ended span: key={key}")
        except Exception as e:
            print(f"[hermes-otel] Error ending span (key={key}): {e}")
            import traceback
            traceback.print_exc()

    def _force_flush(self):
        """Force export of all buffered spans."""
        if self._span_processor:
            try:
                self._span_processor.force_flush(timeout_millis=2000)
            except Exception:
                pass

    @property
    def is_enabled(self) -> bool:
        return self._initialized


# Module-level singleton
_tracer = None


def get_tracer() -> HermesOTelPlugin:
    """Get or create the singleton tracer instance."""
    global _tracer
    if _tracer is None:
        _tracer = HermesOTelPlugin()
    return _tracer
