#!/usr/bin/env python3
"""Render the config-field and env-var reference tables from HermesOtelConfig.

The tables live between ``[//]: # (generated:<name>:start)`` / ``:end`` marker lines in
``website/docs/reference/config-schema.md`` and ``env-vars.md``. Run with
``--write`` to update them, ``--check`` (the default; also run by
``tests/unit/test_config_surface.py``) to fail when they drift from the code.

    uv run --extra dev python scripts/gen_config_docs.py --write
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from hermes_otel.plugin_config import HermesOtelConfig, field_kinds  # noqa: E402

DOCS = REPO_ROOT / "website" / "docs" / "reference"

# One line per field. A field without an entry fails the test — that is the
# point: adding a knob means documenting it.
FIELD_DOCS = {
    "enabled": "Master kill switch; `false` unloads every hook",
    "sample_rate": "Parent-based trace-ID ratio 0.0–1.0; `null` = AlwaysOn (no sampling)",
    "root_span_ttl_ms": "Orphan-sweep TTL: a turn root older than this with no end hook is closed",
    "flush_interval_ms": "Metrics export cadence (PeriodicExportingMetricReader)",
    "preview_max_chars": "Cap on preview strings (tool args/results, user message, assistant response)",
    "capture_previews": "`false` suppresses every input/output preview; metadata still recorded",
    "tool_input_preview_max_chars": "Per-category cap for tool args previews; `null` = `preview_max_chars`",
    "tool_output_preview_max_chars": "Per-category cap for tool result previews; `null` = `preview_max_chars`",
    "llm_input_preview_max_chars": "Per-category cap for LLM input previews; `null` = `preview_max_chars`",
    "llm_output_preview_max_chars": "Per-category cap for LLM output previews; `null` = `preview_max_chars`",
    "headers": "Extra HTTP headers on every OTLP request; per-backend `headers:` are merged onto these",
    "global_tags": "Merged into the OTel Resource; overridden by `resource_attributes` on key conflict",
    "resource_attributes": "Merged into the Resource on top of the defaults `service.name=hermes-agent`, `service.instance.id` (per-process UUID), `service.version`, `process.pid`",
    "project_name": "`openinference.project.name` on the Resource (Phoenix project); overrides `OTEL_PROJECT_NAME`",
    "span_batch_max_queue_size": "Max buffered spans per backend before drops",
    "span_batch_schedule_delay_ms": "BatchSpanProcessor worker wake-up cadence",
    "span_batch_max_export_batch_size": "Max spans per OTLP POST",
    "span_batch_export_timeout_ms": "Per-export HTTP timeout",
    "force_flush_on_session_end": "Synchronously flush every backend at the end of each turn",
    "capture_conversation_history": "Attach the full message JSON to `llm.*` spans",
    "conversation_history_max_chars": "JSON cap when conversation capture is on",
    "capture_full_prompts": "Full-fidelity prompt capture (`llm.input_messages`, `gen_ai.input.messages`); respects `capture_previews`",
    "capture_full_responses": "Full-fidelity response capture (`llm.output.content`, `gen_ai.output.messages`)",
    "capture_sender_id": "Gateway sessions add `hermes.sender.id` and `user.id` (`platform:sender`)",
    "capture_logs": "Attach an OTel LoggingHandler to Python logging; see [OTel logs](/configuration/logs)",
    "log_level": "Handler level: `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`",
    "log_attach_logger": "Logger to attach to; `null` = root, `hermes_otel` = the plugin only",
    "emit_genai_metrics": "Also emit the OTel GenAI spec metrics (`gen_ai.client.*`, `gen_ai.agent.*`)",
    "skill_spans": "Open a `skill.<name>` span on each successful skill load, closed at turn end",
    "discovery_prompt": "Register a system-prompt section advertising `hermes_otel:observability` (changes what the model sees every turn; opt-in)",
    "dashboard_live": "Keep recent spans/metrics/logs in `$HERMES_HOME/hermes_otel_live.db` for the dashboard's Live mode",
    "dashboard_live_max_spans": "Rows kept per kind (spans, metrics, logs) in the live store",
    "host_metrics": "Sample CPU/GPU and emit `process.*` / `system.*` / `hw.*` metrics; see [Host & GPU metrics](/configuration/host-metrics)",
    "host_metrics_gpu": "`auto` · `amd` · `nvidia` · `off` — which GPU SDK to probe",
    "host_metrics_interval_ms": "Host sampling cadence (floor 50 ms)",
    "suppress_mcp_ping_spans": "Drop successful MCP keepalive `ping` spans before export",
    "backends": "Multi-backend fan-out list; see the `backends[]` section",
}

_TYPE_LABEL = {
    "bool": "bool",
    "int": "int",
    "float": "float",
    "str": "string",
    "map": "map",
    "backends": "list",
}


def _default_label(field: dataclasses.Field, kind: str) -> str:
    d = field.default
    if d is None or d is dataclasses.MISSING:
        return "*(unset)*" if kind in ("map", "backends", "str") else "`null`"
    if isinstance(d, bool):
        return f"`{'true' if d else 'false'}`"
    if isinstance(d, str):
        return f'`"{d}"`'
    return f"`{d}`"


def render_fields_table() -> str:
    kinds = field_kinds()
    rows = ["| Field | Type | Default | Description |", "|---|---|---|---|"]
    for f in dataclasses.fields(HermesOtelConfig):
        kind = kinds[f.name]
        typ = _TYPE_LABEL[kind]
        if kind in ("float", "int", "str") and str(f.type).startswith("Optional"):
            typ += " \\| null"
        rows.append(f"| `{f.name}` | {typ} | {_default_label(f, kind)} | {FIELD_DOCS[f.name]} |")
    return "\n".join(rows)


def render_env_table() -> str:
    kinds = field_kinds()
    rows = ["| Env var | Maps to | Type | Default |", "|---|---|---|---|"]
    for f in dataclasses.fields(HermesOtelConfig):
        kind = kinds[f.name]
        if kind not in ("bool", "int", "float", "str"):
            continue
        rows.append(
            f"| `HERMES_OTEL_{f.name.upper()}` | `{f.name}` | {_TYPE_LABEL[kind]} | {_default_label(f, kind)} |"
        )
    return "\n".join(rows)


BLOCKS = {
    (DOCS / "config-schema.md", "config-fields"): render_fields_table,
    (DOCS / "env-vars.md", "env-overrides"): render_env_table,
}


def _splice(text: str, name: str, body: str) -> str:
    start, end = f"[//]: # (generated:{name}:start)", f"[//]: # (generated:{name}:end)"
    pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pat.search(text):
        raise SystemExit(f"markers for {name!r} not found")
    return pat.sub(lambda _m: f"{start}\n\n{body}\n\n{end}", text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="update the docs in place")
    args = ap.parse_args(argv)
    missing = [f.name for f in dataclasses.fields(HermesOtelConfig) if f.name not in FIELD_DOCS]
    if missing:
        print(f"FIELD_DOCS is missing: {missing}")
        return 1
    drift = 0
    for (path, name), render in BLOCKS.items():
        current = path.read_text(encoding="utf-8")
        updated = _splice(current, name, render())
        if updated != current:
            drift += 1
            if args.write:
                path.write_text(updated, encoding="utf-8")
                print(f"updated {path.relative_to(REPO_ROOT)} [{name}]")
            else:
                print(f"DRIFT: {path.relative_to(REPO_ROOT)} [{name}] — run with --write")
    if args.write:
        return 0
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
