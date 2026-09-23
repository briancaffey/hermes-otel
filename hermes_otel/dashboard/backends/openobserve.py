"""OpenObserve adapter — SQL over ``/api/{org}/_search?type=traces``.

OpenObserve stores traces as rows in a stream (default ``default``).
Attributes are flattened into columns with dots replaced by
underscores (``llm.model_name`` → ``llm_model_name``); the adapter maps them
back through a table of the plugin's known attribute names so the UI sees the
same keys it does on Tempo.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import register
from .base import (
    BackendAdapter,
    LogFilter,
    StructuredFilter,
    bucketize,
    counter_increases,
    http_post_json,
    otlp_attrs_from_dict,
    otlp_status,
    resolve_env_or_literal,
    rewrite_host_for_docker,
)

_DEFAULT_OO_PORT = 5080

# Columns the card renderer cares about. Expressed in Otel dot form;
# ``_oo_col`` translates to OpenObserve's underscore form on the wire.
_CARD_ATTR_KEYS = (
    "llm.model_name",
    "llm.provider",
    "llm.api_mode",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
    "llm.response.finish_reason",
    "llm.response.tool_calls",
    "tool.name",
    "input.value",
    "output.value",
    "llm.output.content",
    "hermes.session_id",
    "hermes.turn.number",
)


def _oo_col(dotted: str) -> str:
    return dotted.replace(".", "_")


# Every attribute name the plugin emits (the ``span-attributes.md`` reference
# plus the OTel / OpenInference standard keys it sets). OpenObserve flattens
# ``a.b_c`` and ``a.b.c`` to the same ``a_b_c`` column, so the only way to give
# a column its real name back is a table; ``tests/unit/test_openobserve_columns.py``
# fails when the docs list a name that is missing here (#158).
_KNOWN_ATTRIBUTES = (
    "correlation.id",
    "error.message",
    "error.type",
    "exception.escaped",
    "exception.message",
    "exception.type",
    "gen_ai.agent.name",
    "gen_ai.conversation.id",
    "gen_ai.input.messages",
    "gen_ai.operation.name",
    "gen_ai.output.messages",
    "gen_ai.provider.name",
    "gen_ai.request.choice.count",
    "gen_ai.request.frequency_penalty",
    "gen_ai.request.max_tokens",
    "gen_ai.request.model",
    "gen_ai.request.presence_penalty",
    "gen_ai.request.reasoning.level",
    "gen_ai.request.stop_sequences",
    "gen_ai.request.stream",
    "gen_ai.request.temperature",
    "gen_ai.request.top_k",
    "gen_ai.request.top_p",
    "gen_ai.response.finish_reasons",
    "gen_ai.response.id",
    "gen_ai.response.model",
    "gen_ai.response.status_code",
    "gen_ai.skill.name",
    "gen_ai.system",
    "gen_ai.system_instructions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.id",
    "gen_ai.tool.call.result",
    "gen_ai.tool.name",
    "gen_ai.usage.cache_creation.input_tokens",
    "gen_ai.usage.cache_creation_input_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_read_input_tokens",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.reasoning.output_tokens",
    "gen_ai.usage.total_tokens",
    "hermes.approval.choice",
    "hermes.approval.command",
    "hermes.approval.decided_by",
    "hermes.approval.description",
    "hermes.approval.duration_ms",
    "hermes.approval.granted",
    "hermes.approval.pattern_key",
    "hermes.approval.pattern_keys",
    "hermes.approval.surface",
    "hermes.approval.timed_out",
    "hermes.conversation.message_count",
    "hermes.cron.job_id",
    "hermes.max_retries",
    "hermes.platform",
    "hermes.profile",
    "hermes.retry.count",
    "hermes.retryable",
    "hermes.sender.id",
    "hermes.session.completed",
    "hermes.session.failed",
    "hermes.session.interrupted",
    "hermes.session.is_subagent",
    "hermes.session.kind",
    "hermes.session.synthesized",
    "hermes.session_id",
    "hermes.skill.name",
    "hermes.skill.path",
    "hermes.skill.result_status",
    "hermes.skill.source",
    "hermes.span_kind",
    "hermes.subagent.child_id",
    "hermes.subagent.child_session_id",
    "hermes.subagent.duration_ms",
    "hermes.subagent.goal",
    "hermes.subagent.parent_id",
    "hermes.subagent.parent_session_id",
    "hermes.subagent.parent_turn_id",
    "hermes.subagent.role",
    "hermes.subagent.status",
    "hermes.subagent.summary",
    "hermes.tool.blocked_by",
    "hermes.tool.command",
    "hermes.tool.cpu.utilization.avg",
    "hermes.tool.cpu.utilization.peak",
    "hermes.tool.decided_by",
    "hermes.tool.gpu.utilization.avg",
    "hermes.tool.gpu.utilization.peak",
    "hermes.content.input_chars",
    "hermes.content.output_chars",
    "hermes.preview.input.original_chars",
    "hermes.preview.input.truncated",
    "hermes.preview.output.original_chars",
    "hermes.preview.output.truncated",
    "hermes.link",
    "hermes.session.duration_s",
    "hermes.session.finalize_reason",
    "hermes.session.previous_id",
    "hermes.session.reset_reason",
    "hermes.session.turn_count",
    "hermes.tool.outcome",
    "hermes.tool.target",
    "hermes.turn.api_call_count",
    "hermes.turn.exit_reason",
    "hermes.turn.final_status",
    "hermes.turn.number",
    "hermes.turn.skill_count",
    "hermes.turn.skills",
    "hermes.turn.tool_commands",
    "hermes.turn.tool_count",
    "hermes.turn.tool_outcomes",
    "hermes.turn.tool_targets",
    "hermes.turn.tools",
    "host.name",
    "http.response.status_code",
    "input.mime_type",
    "input.value",
    "llm.api_mode",
    "llm.input_messages",
    "llm.model_name",
    "llm.output.content",
    "llm.output.tool_calls",
    "llm.provider",
    "llm.request.approx_input_tokens",
    "llm.request.max_tokens",
    "llm.request.message_count",
    "llm.response.duration_ms",
    "llm.response.finish_reason",
    "llm.response.output_chars",
    "llm.response.tool_calls",
    "llm.system_prompt",
    "llm.token_count.completion",
    "llm.token_count.completion_details.reasoning",
    "llm.token_count.prompt",
    "llm.token_count.prompt_details.cache_read",
    "llm.token_count.prompt_details.cache_write",
    "llm.token_count.total",
    "openinference.project.name",
    "openinference.span.kind",
    "output.mime_type",
    "output.value",
    "process.pid",
    "service.instance.id",
    "service.name",
    "service.version",
    "session.id",
    "telemetry.sdk.language",
    "telemetry.sdk.name",
    "telemetry.sdk.version",
    "tool.name",
    "traceloop.span.kind",
    "user.id",
    "wandb.entity",
    "wandb.is_turn",
    "wandb.project",
    "wandb.thread_id",
    "weave.agent.version",
)


def _build_column_table() -> Dict[str, str]:
    table: Dict[str, str] = {}
    # Two names can flatten to one column (``gen_ai.usage.cache_read.input_tokens``
    # and its legacy alias ``gen_ai.usage.cache_read_input_tokens``; ``session.id``
    # and ``session_id``): keep the current dotted spelling, i.e. the one with
    # more dots, and on a tie the shorter name.
    for attr in sorted(_KNOWN_ATTRIBUTES, key=lambda a: (-a.count("."), len(a), a)):
        table.setdefault(_oo_col(attr), attr)
    return table


_COLUMN_TO_ATTRIBUTE = _build_column_table()


def _dotted(underscored: str) -> str:
    """The attribute name behind an OpenObserve column, or the column name itself.

    Not a rule: ``llm_model_name`` is ``llm.model_name`` and
    ``gen_ai_usage_input_tokens`` is ``gen_ai.usage.input_tokens``, which no
    underscore-to-dot rewrite can recover (the old one produced
    ``llm.model.name``). Unknown columns keep OpenObserve's own name rather
    than an invented one.
    """
    return _COLUMN_TO_ATTRIBUTE.get(underscored, underscored)


def _sql_escape(v: Any) -> str:
    return str(v).replace("\\", "\\\\").replace("'", "''")


@register
class OpenObserveAdapter(BackendAdapter):
    handles = frozenset({"openobserve", "openobserver"})
    query_lang_label = "SQL WHERE"
    raw_placeholder = "llm_model_name = 'gpt-4'"
    # OpenObserve keeps every OTLP metric as its own stream (``type=metrics``)
    # and logs in a logs stream, both queryable with the same SQL API (#182).
    supports_metrics = True
    supports_logs = True

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        endpoint = cfg.get("endpoint") or ""
        parsed = _urlparse.urlparse(endpoint)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        port = cfg.get("query_port") or _DEFAULT_OO_PORT
        self.query_url = f"{scheme}://{rewrite_host_for_docker(host)}:{port}"
        # Org is the first path segment in the endpoint, e.g.
        # ``/api/default/v1/traces`` → ``default``.
        self.org = cfg.get("org") or _extract_org(endpoint) or "default"
        self.stream = cfg.get("stream_name") or cfg.get("stream") or "default"
        self.user = resolve_env_or_literal(cfg, "user", "user_env")
        self.password = resolve_env_or_literal(cfg, "password", "password_env")

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["org"] = self.org
        base["stream"] = self.stream
        base["auth_required"] = not (self.user and self.password)
        return base

    def _headers(self) -> Dict[str, str]:
        if not (self.user and self.password):
            from .base import HTTPException

            raise HTTPException(
                status_code=502,
                detail=(
                    "OpenObserve requires basic auth credentials. Set "
                    "user + password (or *_env) on the openobserve "
                    "backend entry in config.yaml."
                ),
            )
        token = base64.b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    # ── Query construction ───────────────────────────────────────────

    def _build_where(self, f: StructuredFilter) -> str:
        clauses: List[str] = []
        if f.service:
            clauses.append(f"service_name = '{_sql_escape(f.service)}'")
        if f.name_regex:
            # OpenObserve SQL supports LIKE; wrap user text in %%.
            pat = f.name_regex.replace("%", "\\%")
            clauses.append(f"operation_name LIKE '%{_sql_escape(pat)}%'")
        if f.status == "error":
            clauses.append("status_code = 2")
        elif f.status == "ok":
            clauses.append("status_code = 1")
        if f.min_duration_ms:
            # duration stored in microseconds in OpenObserve traces stream.
            clauses.append(f"duration >= {int(f.min_duration_ms) * 1000}")
        for k, v in f.attr_equals.items():
            clauses.append(f"{_oo_col(k)} = '{_sql_escape(str(v))}'")
        if f.free_text:
            clauses.append(f"llm_input LIKE '%{_sql_escape(f.free_text)}%'")

        raw = (f.raw or "").strip()
        if raw:
            clauses.append(f"({raw})")

        return " AND ".join(clauses) if clauses else "1=1"

    # ── Public API ───────────────────────────────────────────────────

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        where = self._build_where(f)
        url = f"{self.query_url}/api/{self.org}/_search?type=traces"

        # When roots_only is False we want ALL matched spans (still
        # deduped to one per trace client-side); when it's True we try
        # version-specific root-span filters first and fall back to
        # client-side dedupe.
        if f.roots_only:
            attempts = [
                f"SELECT * FROM {self.stream} WHERE {where} AND "
                f"(reference_parent_span_id IS NULL OR reference_parent_span_id = '') "
                f"ORDER BY _timestamp DESC LIMIT {int(limit)}",
                f"SELECT * FROM {self.stream} WHERE {where} AND "
                f"(reference IS NULL OR reference = '') "
                f"ORDER BY _timestamp DESC LIMIT {int(limit)}",
                f"SELECT * FROM {self.stream} WHERE {where} "
                f"ORDER BY _timestamp DESC LIMIT {int(limit) * 4}",
            ]
        else:
            attempts = [
                f"SELECT * FROM {self.stream} WHERE {where} "
                f"ORDER BY _timestamp DESC LIMIT {int(limit) * 4}",
            ]

        rows: List[Dict[str, Any]] = []
        last_err: Optional[Exception] = None
        for sql in attempts:
            body = {
                "query": {
                    "sql": sql,
                    "start_time": int(start_s) * 1_000_000,  # µs
                    "end_time": int(end_s) * 1_000_000,
                    "size": int(limit) * 4,
                }
            }
            try:
                data = http_post_json(url, body, headers=self._headers(), timeout=15.0)
            except Exception as e:  # HTTPException or network
                last_err = e
                continue
            hits = data.get("hits") if isinstance(data, dict) else None
            if isinstance(hits, list) and hits:
                rows = hits
                break

        if not rows and last_err:
            raise last_err

        traces: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            trace_id = row.get("trace_id") or row.get("traceId")
            if not trace_id or trace_id in traces:
                continue
            start_ns = int(row.get("start_time") or 0)
            duration_us = int(row.get("duration") or 0)
            duration_ms = duration_us // 1000 if duration_us else 0

            attrs_map = _row_to_card_attrs(row)
            traces[trace_id] = {
                "traceID": trace_id,
                "rootServiceName": row.get("service_name") or "",
                "rootTraceName": row.get("operation_name") or row.get("name") or "",
                "startTimeUnixNano": str(start_ns) if start_ns else "0",
                "durationMs": duration_ms,
                "spanSets": [
                    {
                        "spans": [
                            {
                                "spanID": row.get("span_id"),
                                "name": row.get("operation_name") or row.get("name") or "",
                                "attributes": otlp_attrs_from_dict(attrs_map),
                            }
                        ],
                        "matched": 1,
                    }
                ],
            }
        self._attach_span_counts(traces, start_s, end_s)
        return {"traces": list(traces.values())}

    def _attach_span_counts(
        self, traces: Dict[str, Dict[str, Any]], start_s: int, end_s: int
    ) -> None:
        """Add ``spanCount`` (spans per trace) with one grouped query.

        The search query returns one row per trace, so the card would
        otherwise show "1 spans" for every trace (#179). A failed count
        query leaves ``spanCount`` unset; the UI then shows no number.
        """
        if not traces:
            return
        ids = ", ".join(f"'{_sql_escape(t)}'" for t in traces)
        sql = (
            f"SELECT trace_id, COUNT(*) AS n FROM {self.stream} "
            f"WHERE trace_id IN ({ids}) GROUP BY trace_id"
        )
        body = {
            "query": {
                "sql": sql,
                "start_time": int(start_s) * 1_000_000,
                "end_time": int(end_s) * 1_000_000,
                "size": len(traces),
            }
        }
        url = f"{self.query_url}/api/{self.org}/_search?type=traces"
        try:
            data = http_post_json(url, body, headers=self._headers(), timeout=15.0)
        except Exception:
            return
        hits = data.get("hits") if isinstance(data, dict) else None
        for row in hits or []:
            if not isinstance(row, dict):
                continue
            tid = row.get("trace_id")
            n = row.get("n")
            if tid in traces and isinstance(n, (int, float)) and n > 0:
                traces[tid]["spanCount"] = int(n)

    # ── metrics (#182) ───────────────────────────────────────────────
    #
    # The plugin's exporter names streams ``hermes_<instrument>`` for its own
    # counters and ``gen_ai_client_token_usage`` etc. for the semconv ones.
    # Histograms arrive split into ``_sum`` / ``_count`` / ``_bucket`` streams;
    # counters arrive cumulative, so per-bucket values are increases between
    # consecutive samples of a series.

    def _search(
        self, sql: str, start_s: int, end_s: int, size: int, stream_type: str
    ) -> List[Dict[str, Any]]:
        url = f"{self.query_url}/api/{self.org}/_search?type={stream_type}"
        body = {
            "query": {
                "sql": sql,
                "start_time": int(start_s) * 1_000_000,
                "end_time": int(end_s) * 1_000_000,
                "size": int(size),
            }
        }
        data = http_post_json(url, body, headers=self._headers(), timeout=60.0)
        hits = data.get("hits") if isinstance(data, dict) else None
        return [h for h in hits or [] if isinstance(h, dict)]

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        url = f"{self.query_url}/api/{self.org}/streams?type=metrics"
        from .base import http_get_json

        data = http_get_json(url, headers=self._headers(), timeout=30.0)
        out: List[Dict[str, Any]] = []
        for s in (data.get("list") if isinstance(data, dict) else None) or []:
            name = s.get("name") if isinstance(s, dict) else None
            if not name or name.startswith("otel_sdk_"):
                continue
            if name.endswith(("_bucket", "_min", "_max")):
                continue  # histogram internals; _sum and _count stay
            docs = ((s.get("stats") or {}).get("doc_num")) if isinstance(s, dict) else None
            out.append({"name": name, "count": int(docs or 0)})
        return out

    def metrics_query(
        self,
        name: str,
        start_s: int,
        end_s: int,
        bucket_s: int,
        group_by: Optional[str] = None,
        agg: str = "sum",
    ) -> Dict[str, Any]:
        stream = name.replace('"', "")
        rows = self._search(
            f'SELECT * FROM "{stream}" ORDER BY _timestamp ASC LIMIT 10000',
            start_s,
            end_s,
            10000,
            "metrics",
        )
        cumulative = any(
            str(r.get("aggregation_temporality", "")).endswith("CUMULATIVE")
            and str(r.get("is_monotonic")) == "true"
            for r in rows[:1]
        )
        # Series identity is every label except the OTel/OpenObserve bookkeeping columns.
        skip = {
            "_timestamp",
            "value",
            "__hash__",
            "__name__",
            "aggregation_temporality",
            "flag",
            "is_monotonic",
            "start_time",
            "instrumentation_library_name",
            "instrumentation_library_version",
            "telemetry_sdk_language",
            "telemetry_sdk_name",
            "telemetry_sdk_version",
            "service_instance_id",
            "process_pid",
            "exemplars",
        }
        samples = []
        for r in rows:
            v = _maybe_num(r.get("value"))
            if not isinstance(v, (int, float)):
                continue
            ident = "|".join(f"{k}={r[k]}" for k in sorted(r) if k not in skip)
            samples.append((int(r.get("_timestamp") or 0) * 1000, float(v), ident))
        if cumulative:
            samples = counter_increases(samples)

        # Collapse the series identity to the requested group_by label.
        def label_of(ident: str) -> str:
            if not group_by:
                return "_"
            for part in ident.split("|"):
                k, _, val = part.partition("=")
                if k == group_by or k == group_by.replace(".", "_"):
                    return val
            return "—"

        points = [(ts, v, label_of(ident)) for ts, v, ident in samples]
        out = bucketize(
            points, int(start_s) * 1_000_000_000, int(end_s) * 1_000_000_000, bucket_s, agg
        )
        out["name"] = name
        out["cumulative"] = cumulative
        return out

    # ── logs (#182) ──────────────────────────────────────────────────

    _LOG_STREAM_DEFAULT = "default"

    def _log_stream(self) -> str:
        return str(self.cfg.get("logs_stream") or self._LOG_STREAM_DEFAULT)

    @staticmethod
    def _level_name(row: Dict[str, Any]) -> str:
        sev = row.get("severity") or row.get("severity_text") or row.get("level") or "INFO"
        return str(sev).upper()

    def logs_search(
        self, f: LogFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        where: List[str] = []
        if f.trace_id:
            where.append(f"trace_id = '{_sql_escape(f.trace_id)}'")
        if f.session:
            where.append(f"session_id = '{_sql_escape(f.session)}'")
        if f.logger:
            where.append(f"instrumentation_library_name = '{_sql_escape(f.logger)}'")
        if f.text:
            where.append(f"body LIKE '%{_sql_escape(f.text)}%'")
        sql = f'SELECT * FROM "{self._log_stream()}"'
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY _timestamp DESC LIMIT {int(limit) * 3}"
        rows = self._search(sql, start_s, end_s, int(limit) * 3, "logs")
        levels = {
            "DEBUG": 10,
            "INFO": 20,
            "WARNING": 30,
            "WARN": 30,
            "ERROR": 40,
            "CRITICAL": 50,
            "FATAL": 50,
        }
        out: List[Dict[str, Any]] = []
        for r in rows:
            level = self._level_name(r)
            if f.min_level and levels.get(level, 20) < f.min_level:
                continue
            out.append(
                {
                    "level": level,
                    # The OTLP logs exporter records the Python logger name as
                    # the instrumentation scope; that is the column OpenObserve keeps.
                    "logger": r.get("instrumentation_library_name") or r.get("logger_name") or "",
                    "body": r.get("body") or "",
                    "time_unix_nano": int(r.get("_timestamp") or 0) * 1000,
                    "trace_id": r.get("trace_id"),
                    "session_id": r.get("session_id") or r.get("hermes_session_id"),
                }
            )
            if len(out) >= limit:
                break
        return out

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        rows = self._search(
            f'SELECT instrumentation_library_name AS logger, COUNT(*) AS n FROM "{self._log_stream()}" '
            "GROUP BY instrumentation_library_name ORDER BY n DESC LIMIT 200",
            start_s,
            end_s,
            200,
            "logs",
        )
        return [
            {"logger": r.get("logger") or "", "count": int(r.get("n") or 0)}
            for r in rows
            if r.get("logger")
        ]

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        where = f"trace_id = '{_sql_escape(trace_id)}'"
        sql = f"SELECT * FROM {self.stream} WHERE {where} ORDER BY start_time ASC LIMIT 500"
        # 24h window either side of the trace — trace_id is unique so
        # we don't need a tight window but OpenObserve requires one.
        import time

        now = int(time.time())
        body = {
            "query": {
                "sql": sql,
                "start_time": (now - 7 * 86400) * 1_000_000,
                "end_time": (now + 3600) * 1_000_000,
                "size": 500,
            }
        }
        url = f"{self.query_url}/api/{self.org}/_search?type=traces"
        data = http_post_json(url, body, headers=self._headers(), timeout=20.0)
        hits = data.get("hits") if isinstance(data, dict) else None
        rows = hits if isinstance(hits, list) else []
        return _rows_to_otlp(rows)


def _extract_org(endpoint: str) -> Optional[str]:
    try:
        parsed = _urlparse.urlparse(endpoint)
        # Path looks like ``/api/default/v1/traces``.
        parts = [p for p in (parsed.path or "").split("/") if p]
        if len(parts) >= 2 and parts[0] == "api":
            return parts[1]
    except Exception:
        pass
    return None


_CARD_ATTR_OO_COLS = tuple(_oo_col(k) for k in _CARD_ATTR_KEYS)


def _row_to_card_attrs(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": row.get("operation_name") or row.get("name") or ""}
    status_code = row.get("status_code")
    if status_code is not None:
        out["status"] = "error" if status_code == 2 else "ok"
    for dotted, col in zip(_CARD_ATTR_KEYS, _CARD_ATTR_OO_COLS):
        v = row.get(col)
        if v is not None and v != "":
            out[dotted] = _maybe_num(v)
    return out


def _maybe_num(v: Any) -> Any:
    """OpenObserve sometimes returns numeric columns as strings
    (``"13283"``). Pull those back to ints for the card renderer's
    token-count formatting."""
    if isinstance(v, str) and v.isdigit():
        try:
            return int(v)
        except Exception:
            return v
    return v


_SKIP_OTLP_COLS = frozenset(
    {
        "_timestamp",
        "trace_id",
        "span_id",
        "parent_span_id",
        "reference",
        "reference_parent_span_id",
        "reference_parent_trace_id",
        "reference_ref_type",
        "start_time",
        "end_time",
        "duration",
        "operation_name",
        "name",
        "service_name",
        "span_kind",
        "status_code",
        "status_message",
        "flags",
        "events",
        "links",
        "input_mime_type",
        "output_mime_type",
    }
)


def _rows_to_otlp(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_service: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        start_ns = int(row.get("start_time") or 0)
        end_ns = int(row.get("end_time") or 0)
        attrs: Dict[str, Any] = {}
        for k, v in row.items():
            if k in _SKIP_OTLP_COLS or v is None or v == "":
                continue
            attrs[_dotted(k)] = _maybe_num(v)

        parent = (
            row.get("reference_parent_span_id")
            or _extract_parent_from_reference(row.get("reference"))
            or row.get("parent_span_id")
        )
        otlp_span = {
            "traceId": row.get("trace_id"),
            "spanId": row.get("span_id"),
            "parentSpanId": parent or None,
            "name": row.get("operation_name") or row.get("name") or "",
            "kind": int(row.get("span_kind") or 1),
            "startTimeUnixNano": str(start_ns) if start_ns else "0",
            "endTimeUnixNano": str(end_ns) if end_ns else "0",
            "attributes": otlp_attrs_from_dict(attrs),
            "status": otlp_status(row.get("status_code"), row.get("status_message") or ""),
        }
        service = row.get("service_name") or ""
        by_service.setdefault(service, []).append(otlp_span)

    batches = []
    for service, spans in by_service.items():
        resource_attrs = otlp_attrs_from_dict({"service.name": service} if service else {})
        batches.append(
            {
                "resource": {"attributes": resource_attrs},
                "scopeSpans": [{"spans": spans}],
            }
        )
    return {"batches": batches}


def _extract_parent_from_reference(ref: Any) -> Optional[str]:
    """OpenObserve encodes parent-links as a JSON string in ``reference``.

    Example: ``[{"refType":"CHILD_OF","traceId":"…","spanId":"…"}]``.
    We return the first ``spanId`` we find.
    """
    if not ref:
        return None
    if isinstance(ref, list):
        for r in ref:
            if isinstance(r, dict) and r.get("spanId"):
                return r["spanId"]
    if isinstance(ref, str):
        try:
            import json

            parsed = json.loads(ref)
            if isinstance(parsed, list):
                for r in parsed:
                    if isinstance(r, dict) and r.get("spanId"):
                        return r["spanId"]
        except Exception:
            pass
    return None
