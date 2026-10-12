"""Derive an ``opentelemetry-mcp`` server configuration from the configured backends (#304).

Traceloop's `opentelemetry-mcp <https://github.com/traceloop/opentelemetry-mcp-server>`_
lets any MCP client (Hermes itself, Claude Code, Cursor) search traces and sum
GenAI token usage. It reads a Jaeger v1 or a Tempo query API, and the plugin
already knows where every configured backend lives, so ``otel.py mcp-config``
prints the ``mcp_servers:`` block and the ``hermes mcp add`` command instead of
making the user look the query port up. Only the standard library is needed to
render; ``--apply`` shells out to ``hermes mcp add``.

Everything here is pure except :func:`apply` (subprocess) and
:func:`existing_server_names` (reads ``config.yaml``), so the mapping is unit
tested without Hermes.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from .backend_specs import BY_TYPE

#: The one place the pinned server version lives (docs and the generated
#: commands quote it). The caveats on the docs page are specific to it.
MCP_PACKAGE = "opentelemetry-mcp"
MCP_PACKAGE_VERSION = "0.2.2"
MCP_PACKAGE_SPEC = f"{MCP_PACKAGE}=={MCP_PACKAGE_VERSION}"
DEFAULT_SERVER_NAME = "otel"
#: ``uvx`` downloads the package on the first start; Hermes's default connect
#: timeout is too short for that.
CONNECT_TIMEOUT_S = 60

# Query ports of the stacks the server was verified against (2026-10-11, the
# repo's docker-compose folders): Jaeger's UI port and Tempo's HTTP port. The
# ``grafana/otel-lgtm`` image serves Tempo on the same 3200.
_DEFAULT_QUERY_PORT = {t: BY_TYPE[t].default_query_port for t in ("jaeger", "tempo", "lgtm")}
_SERVER_BACKEND_TYPE = {"jaeger": "jaeger", "tempo": "tempo", "lgtm": "tempo"}

# Types whose read API is Jaeger- or Tempo-compatible but were not run against
# the server; listed with the reason instead of a config block.
_COMPATIBLE_UNVERIFIED = {
    "gigapipe": "serves the Tempo API on its OTLP port, not verified with the server",
    "victoria": "serves the Jaeger API under /select/jaeger, not verified with the server",
    "greptime": "serves the Jaeger API under /v1/jaeger, not verified with the server",
}


@dataclass(frozen=True)
class QueryTarget:
    """What the MCP server needs for one configured backend entry."""

    name: str
    backend_type: str  # the entry's ``type``
    server_backend_type: str  # ``BACKEND_TYPE`` for the server
    backend_url: str  # ``BACKEND_URL`` for the server

    def env(self) -> Dict[str, str]:
        return {"BACKEND_TYPE": self.server_backend_type, "BACKEND_URL": self.backend_url}


@dataclass(frozen=True)
class NotQueryable:
    name: str
    backend_type: str
    reason: str


def _label(entry: Dict[str, Any]) -> str:
    return str(entry.get("name") or entry.get("type") or "")


def query_target(entry: Dict[str, Any]) -> Any:
    """:class:`QueryTarget` for a Jaeger / Tempo / LGTM entry, else :class:`NotQueryable`.

    The host and scheme come from the entry's OTLP ``endpoint``; the port is
    the entry's ``query_port`` (the same key the dashboard adapters read) or
    the stack default. No credentials are ever copied into the result: the
    server's env gets ``BACKEND_TYPE`` and ``BACKEND_URL`` only.
    """
    btype = str(entry.get("type") or "").strip().lower()
    name = _label(entry)
    if btype not in _DEFAULT_QUERY_PORT:
        reason = _COMPATIBLE_UNVERIFIED.get(
            btype, "traces are not served over a Jaeger or Tempo query API"
        )
        return NotQueryable(name=name, backend_type=btype, reason=reason)
    if btype == "jaeger" and str(entry.get("query_api") or "").strip().lower() == "v3":
        return NotQueryable(
            name=name,
            backend_type=btype,
            reason=f"Jaeger v2 serves only /api/v3, which {MCP_PACKAGE} {MCP_PACKAGE_VERSION} does not speak",
        )
    parsed = urlparse(str(entry.get("endpoint") or ""))
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "localhost"
    port = entry.get("query_port") or _DEFAULT_QUERY_PORT[btype]
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = _DEFAULT_QUERY_PORT[btype]
    return QueryTarget(
        name=name,
        backend_type=btype,
        server_backend_type=_SERVER_BACKEND_TYPE[btype],
        backend_url=f"{scheme}://{host}:{port}",
    )


def classify(entries: Sequence[Dict[str, Any]]) -> List[Any]:
    """One :class:`QueryTarget` or :class:`NotQueryable` per configured entry, in order."""
    return [query_target(e) for e in entries]


def pick_target(
    results: Sequence[Any], wanted: Optional[str] = None, pin: Optional[str] = None
) -> Optional[QueryTarget]:
    """The entry to render: ``wanted`` (a ``--source``), else the ``query_backend``
    pin when it is queryable, else the first queryable entry. ``wanted`` that
    names nothing queryable is ``None`` too; the caller reports it."""
    targets = [r for r in results if isinstance(r, QueryTarget)]
    for key in (wanted, pin):
        if not key:
            continue
        for t in targets:
            if t.name == key or t.backend_type == key:
                return t
        if key is wanted:
            return None
    return targets[0] if targets else None


def render_yaml(target: QueryTarget, server_name: str = DEFAULT_SERVER_NAME) -> str:
    """The ``mcp_servers:`` block ``hermes mcp add`` writes into ``config.yaml``."""
    return (
        "mcp_servers:\n"
        f"  {server_name}:\n"
        "    command: uvx\n"
        "    args:\n"
        f"      - {MCP_PACKAGE_SPEC}\n"
        "    env:\n"
        f"      BACKEND_TYPE: {target.server_backend_type}\n"
        f"      BACKEND_URL: {target.backend_url}\n"
        f"    connect_timeout: {CONNECT_TIMEOUT_S}.0\n"
        "    enabled: true\n"
    )


def hermes_add_argv(target: QueryTarget, server_name: str = DEFAULT_SERVER_NAME) -> List[str]:
    """``hermes mcp add …`` as an argv list (``--args`` must stay last)."""
    return [
        "hermes",
        "mcp",
        "add",
        server_name,
        "--command",
        "uvx",
        "--connect-timeout",
        str(CONNECT_TIMEOUT_S),
        "--env",
        f"BACKEND_TYPE={target.server_backend_type}",
        f"BACKEND_URL={target.backend_url}",
        "--args",
        MCP_PACKAGE_SPEC,
    ]


def render_hermes_add(target: QueryTarget, server_name: str = DEFAULT_SERVER_NAME) -> str:
    return " ".join(hermes_add_argv(target, server_name))


def render_claude_code_add(target: QueryTarget, server_name: str = DEFAULT_SERVER_NAME) -> str:
    return (
        f"claude mcp add {server_name} -e BACKEND_TYPE={target.server_backend_type} "
        f"-e BACKEND_URL={target.backend_url} -- uvx {MCP_PACKAGE_SPEC}"
    )


def render_mcp_json(target: QueryTarget, server_name: str = DEFAULT_SERVER_NAME) -> Dict[str, Any]:
    """The ``mcpServers`` shape Cursor and Claude Desktop read."""
    return {
        "mcpServers": {
            server_name: {
                "command": "uvx",
                "args": [MCP_PACKAGE_SPEC],
                "env": target.env(),
            }
        }
    }


def existing_server_names(hermes_home: Path) -> List[str]:
    """Names under ``mcp_servers:`` in ``<home>/config.yaml`` (empty when unreadable)."""
    path = Path(hermes_home) / "config.yaml"
    try:
        import yaml

        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return []
    servers = data.get("mcp_servers") if isinstance(data, dict) else None
    return sorted(str(k) for k in servers) if isinstance(servers, dict) else []


class ApplyRefused(Exception):
    """``--apply`` would overwrite an existing server and ``--force`` was not given."""


def apply(
    target: QueryTarget,
    hermes_home: Path,
    server_name: str = DEFAULT_SERVER_NAME,
    force: bool = False,
    runner: Any = subprocess.run,
) -> List[List[str]]:
    """Run ``hermes mcp add`` (after ``hermes mcp remove`` with ``force``).

    Refuses when ``server_name`` already exists unless ``force``. Answers the
    "Enable all N tools?" prompt with ``Y`` on stdin. Returns the argv lists it
    ran, for the caller's report; ``runner`` is injectable for tests.
    """
    if server_name in existing_server_names(hermes_home):
        if not force:
            raise ApplyRefused(
                f"MCP server {server_name!r} already exists in {Path(hermes_home) / 'config.yaml'}; "
                "pass --force to replace it or --name for another name"
            )
        ran = [["hermes", "mcp", "remove", server_name]]
        runner(ran[0], input="y\n", text=True, check=True)
    else:
        ran = []
    argv = hermes_add_argv(target, server_name)
    runner(argv, input="Y\n", text=True, check=True)
    ran.append(argv)
    return ran
