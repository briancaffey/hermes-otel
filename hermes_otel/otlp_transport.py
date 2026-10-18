"""OTLP transport selection and TLS credentials shared by all three signals.

HTTP exporters remain the default. Import gRPC only for a gRPC destination,
so an HTTP-only installation does not depend on importing grpcio at startup.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

from .backends import _ResolvedBackend
from .debug_utils import logger

_DARWIN_WARNED = False


def _warn_darwin_fork() -> None:
    """Say once that loading grpcio on macOS makes ``fork()`` children
    crash-prone (grpcio >= 1.78.1, Network.framework's atfork handler)."""
    global _DARWIN_WARNED
    _DARWIN_WARNED = True
    logger.warning(
        "[hermes-otel] ⚠ protocol: grpc on macOS: grpcio can make forked subprocesses "
        "(tools, git) crash with SIGSEGV; prefer http/protobuf where the collector accepts it "
        "(https://briancaffey.github.io/hermes-otel/backends/otlp#grpc-on-macos)"
    )


def _file_option(
    backend: _ResolvedBackend, field: str, env_suffix: str, signal: str, use_env: bool = True
) -> Optional[str]:
    value = getattr(backend, field)
    if value is None and use_env:
        value = os.getenv(f"OTEL_EXPORTER_OTLP_{signal.upper()}_{env_suffix}")
    if value is None and use_env:
        value = os.getenv(f"OTEL_EXPORTER_OTLP_{env_suffix}")
    if value is None:
        return None
    if not value.strip():
        raise ValueError(f"{field} must name a PEM file")
    path = Path(value).expanduser()
    # Fail before creating any exporter/channel. In particular, never let an
    # unreadable CA file fall back to system trust in the SDK's env loader.
    if not path.read_bytes():
        raise ValueError(f"{field} must not be empty")
    return str(path)


def create_exporter(
    backend: _ResolvedBackend, signal: str, http_exporter: Any, **kwargs: Any
) -> Any:
    """Build the selected SDK exporter, retaining existing HTTP constructor seams.

    Explicit per-backend TLS files take precedence over signal-specific SDK
    environment variables, which take precedence over the global SDK variables.
    TLS files on the entry for a plaintext destination are an error, not a
    downgrade; the SDK variables apply to TLS destinations only, as in the SDK,
    so a global ``OTEL_EXPORTER_OTLP_CERTIFICATE`` for one HTTPS collector
    leaves an ``http://`` entry exporting as before.
    """
    if backend.protocol not in ("http/protobuf", "grpc"):
        raise ValueError(f"unsupported OTLP protocol {backend.protocol!r}")
    endpoint = kwargs["endpoint"]
    parsed = urlparse(endpoint if "://" in endpoint else "//" + endpoint)
    explicit_tls = any(
        getattr(backend, f) is not None
        for f in ("certificate_file", "client_key_file", "client_certificate_file")
    )

    def tls_files(use_env: bool) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        certificate = _file_option(backend, "certificate_file", "CERTIFICATE", signal, use_env)
        key = _file_option(backend, "client_key_file", "CLIENT_KEY", signal, use_env)
        client = _file_option(
            backend, "client_certificate_file", "CLIENT_CERTIFICATE", signal, use_env
        )
        if (key and not client) or (backend.protocol == "grpc" and bool(key) != bool(client)):
            raise ValueError(
                "client_key_file and client_certificate_file must be supplied together"
            )
        return certificate, key, client

    if backend.protocol == "http/protobuf":
        if backend.insecure is not None:
            raise ValueError("insecure is a gRPC-only option; HTTP always verifies TLS")
        if parsed.scheme != "https":
            if explicit_tls:
                raise ValueError("TLS files require an HTTPS endpoint")
            return http_exporter(**kwargs)
        certificate, key, client = tls_files(use_env=True)
        if certificate:
            kwargs["certificate_file"] = certificate
        if key:
            kwargs["client_key_file"] = key
        if client:
            # The HTTP SDK also accepts a combined certificate + key PEM.
            kwargs["client_certificate_file"] = client
        return http_exporter(**kwargs)

    if parsed.scheme not in ("", "http", "https") or not parsed.netloc:
        raise ValueError("gRPC endpoint must be host:port or an http(s) origin")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("gRPC endpoint must not contain a path, query, fragment or user info")
    insecure = backend.insecure
    if insecure is not None and not isinstance(insecure, bool):
        raise ValueError("insecure must be a boolean")
    if insecure is None:
        raw = os.getenv(f"OTEL_EXPORTER_OTLP_{signal.upper()}_INSECURE")
        if raw is None:
            raw = os.getenv("OTEL_EXPORTER_OTLP_INSECURE")
        if raw is not None:
            if raw.lower() not in ("true", "false"):
                raise ValueError("OTLP INSECURE must be true or false")
            insecure = raw.lower() == "true"
        else:
            insecure = parsed.scheme == "http"
    if parsed.scheme == "https" and insecure:
        raise ValueError("insecure: true conflicts with an HTTPS endpoint")
    if insecure and explicit_tls:
        raise ValueError("TLS files cannot be used with an insecure gRPC connection")
    certificate, key, client = tls_files(use_env=not insecure)
    has_tls_files = bool(certificate or key or client)

    if sys.platform == "darwin" and not _DARWIN_WARNED:
        _warn_darwin_fork()
    import grpc
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    exporter = {
        "traces": OTLPSpanExporter,
        "metrics": OTLPMetricExporter,
        "logs": OTLPLogExporter,
    }[signal]
    # Pass the bare target with an explicit mode, so the SDK cannot override
    # that mode based on a URL scheme or silently discard an HTTP path.
    kwargs["endpoint"] = parsed.netloc
    kwargs["insecure"] = insecure
    if kwargs.get("headers"):
        # HTTP header names are case-insensitive; gRPC metadata requires lowercase.
        kwargs["headers"] = {k.lower(): v for k, v in kwargs["headers"].items()}
    if has_tls_files:
        kwargs["credentials"] = grpc.ssl_channel_credentials(
            root_certificates=Path(certificate).read_bytes() if certificate else None,
            private_key=Path(key).read_bytes() if key else None,
            certificate_chain=Path(client).read_bytes() if client else None,
        )
    return exporter(**kwargs)
