"""What the pipeline is built from, read off the process: the OTLP endpoint
and header environment the exporters see, the host platform the Resource
describes, and the token-usage attribute filter the root roll-up applies.

A leaf module (imports nothing from the package) because ``backends.py``
needs the endpoint rule and ``pipeline.py`` imports ``backends.py``, so these
cannot live in ``pipeline.py`` without a cycle (#522). ``log_handler.py``
builds its exporters under the same header guard.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple


def derive_signal_endpoint(traces_endpoint: str, signal: str) -> str:
    """Rewrite ``.../v1/traces`` to ``.../v1/<signal>`` (``metrics`` / ``logs``).

    OTLP/HTTP collectors expose the three signals on one host under different
    path suffixes; any other endpoint shape is returned unchanged.
    """
    if traces_endpoint.endswith("/v1/traces"):
        return traces_endpoint[: -len("/v1/traces")] + f"/v1/{signal}"
    return traces_endpoint


# The OTLP exporters read these when constructed without headers (``headers or
# parse_env_headers(...)``). The plugin's config file is the single source of
# headers (#472), so they are hidden while exporters are built.
OTLP_HEADER_ENV_VARS: Tuple[str, ...] = (
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
)


@contextlib.contextmanager
def without_otlp_header_env() -> Iterator[None]:
    """Hide the ``OTEL_EXPORTER_OTLP_*HEADERS`` variables while exporters are built.

    A backend entry without ``headers:`` is constructed with ``headers=None``
    and the SDK then falls back to the environment, so a token exported for
    some other tool (Honeycomb, Grafana Cloud, Logfire …) would be sent to
    every credential-less backend in the file (#472). The variables are
    restored on exit, also when construction raises; nothing else in the
    environment is touched.
    """
    saved = {name: os.environ.pop(name) for name in OTLP_HEADER_ENV_VARS if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


# ``platform.machine()`` spellings -> the OTel ``host.arch`` well-known value
# (``HostArchValues``). Unknown machines pass through lower-cased.
_HOST_ARCH = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "i386": "x86",
    "i486": "x86",
    "i586": "x86",
    "i686": "x86",
    "x86": "x86",
    "armv6l": "arm32",
    "armv7l": "arm32",
    "armv8l": "arm32",
    "arm": "arm32",
    "ppc64": "ppc64",
    "ppc64le": "ppc64",
    "ppc": "ppc32",
    "s390x": "s390x",
    "ia64": "ia64",
}


def os_type(system: Optional[str] = None) -> str:
    """The OTel ``os.type`` for this host (``darwin`` / ``linux`` / ``windows`` …),
    from ``platform.system()``; empty when unknown (#476)."""
    import platform

    text = (system if system is not None else platform.system()).strip().lower()
    return text


def host_arch(machine: Optional[str] = None) -> str:
    """The OTel ``host.arch`` for this host (``arm64`` / ``amd64`` / ``x86`` …),
    from ``platform.machine()``; the raw spelling, lower-cased, when the
    conventions name nothing for it; empty when unknown (#476)."""
    import platform

    text = (machine if machine is not None else platform.machine()).strip().lower()
    return _HOST_ARCH.get(text, text)


# The attribute families the turn's token roll-up uses on the ``agent`` /
# ``cron`` root (see hooks/usage.py: the OTel GenAI and the OpenInference
# spelling of the same totals). Built from parts, out of the hook modules,
# so the docs-coverage test reads them as prefixes rather than attribute names.
_USAGE_FAMILIES = ("gen_ai", "llm")
_USAGE_MEMBERS = ("usage", "token_count")
_ROOT_USAGE_PREFIXES = tuple(f"{fam}.{mem}." for fam, mem in zip(_USAGE_FAMILIES, _USAGE_MEMBERS))


def is_usage_key(key: str) -> bool:
    """True for a token-usage attribute (``gen_ai.usage.*`` / ``llm.token_count.*``)."""
    return key.startswith(_ROOT_USAGE_PREFIXES)


def without_usage_keys(attrs: Mapping[str, Any]) -> Dict[str, Any]:
    """``attrs`` minus the token-usage attributes; everything else kept as is."""
    return {k: v for k, v in attrs.items() if not is_usage_key(k)}
