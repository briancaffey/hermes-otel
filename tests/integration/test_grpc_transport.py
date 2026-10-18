"""Real local OTLP/gRPC exports, including TLS and client-certificate rejection.

Certificates are generated in pytest's temporary directory with OpenSSL (also
available on the CI runners); no private keys or external service are required.
"""

import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import grpc
import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.sdk.trace.export import SpanExportResult

from hermes_otel.backend_config import BackendConfig
from hermes_otel.backends import resolve
from hermes_otel.otlp_transport import create_exporter
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    root = tmp_path_factory.mktemp("grpc-tls")

    def openssl(*args):
        subprocess.run(["openssl", *args], cwd=root, check=True, capture_output=True)

    def ca(name):
        openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={name}",
            "-keyout",
            f"{name}.key",
            "-out",
            f"{name}.pem",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
        )

    def issue(name, authority, extensions):
        openssl(
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            f"/CN={name}",
            "-keyout",
            f"{name}.key",
            "-out",
            f"{name}.csr",
        )
        (root / f"{name}.ext").write_text(extensions)
        openssl(
            "x509",
            "-req",
            "-in",
            f"{name}.csr",
            "-CA",
            f"{authority}.pem",
            "-CAkey",
            f"{authority}.key",
            "-CAcreateserial",
            "-days",
            "1",
            "-extfile",
            f"{name}.ext",
            "-out",
            f"{name}.pem",
        )

    ca("ca")
    ca("untrusted-ca")
    issue("server", "ca", "subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\n")
    issue("client", "ca", "extendedKeyUsage=clientAuth\n")
    issue("untrusted-client", "untrusted-ca", "extendedKeyUsage=clientAuth\n")
    return root


@pytest.fixture
def receiver(certificates):
    servers = []

    def start(mode):
        received = {signal: [] for signal in ("traces", "metrics", "logs")}
        metadata = []
        server = grpc.server(ThreadPoolExecutor(max_workers=3))
        for signal, service, request, response in (
            (
                "traces",
                "opentelemetry.proto.collector.trace.v1.TraceService",
                ExportTraceServiceRequest,
                ExportTraceServiceResponse,
            ),
            (
                "metrics",
                "opentelemetry.proto.collector.metrics.v1.MetricsService",
                ExportMetricsServiceRequest,
                ExportMetricsServiceResponse,
            ),
            (
                "logs",
                "opentelemetry.proto.collector.logs.v1.LogsService",
                ExportLogsServiceRequest,
                ExportLogsServiceResponse,
            ),
        ):

            def export(request, context, signal=signal, response=response):
                received[signal].append(request)
                metadata.append(dict(context.invocation_metadata()))
                return response()

            server.add_generic_rpc_handlers(
                (
                    grpc.method_handlers_generic_handler(
                        service,
                        {
                            "Export": grpc.unary_unary_rpc_method_handler(
                                export,
                                request_deserializer=request.FromString,
                                response_serializer=response.SerializeToString,
                            )
                        },
                    ),
                )
            )
        if mode == "plaintext":
            port = server.add_insecure_port("127.0.0.1:0")
        else:
            creds = grpc.ssl_server_credentials(
                [
                    (
                        (certificates / "server.key").read_bytes(),
                        (certificates / "server.pem").read_bytes(),
                    )
                ],
                root_certificates=(certificates / "ca.pem").read_bytes(),
                require_client_auth=mode == "mtls",
            )
            port = server.add_secure_port("127.0.0.1:0", creds)
        assert port
        server.start()
        servers.append(server)
        return f"localhost:{port}", received, metadata

    yield start
    for server in servers:
        server.stop(0).wait()


@pytest.fixture(autouse=True)
def clean_export_env(monkeypatch):
    for name in os.environ:
        if name.startswith("OTEL_EXPORTER_OTLP"):
            monkeypatch.delenv(name)


@pytest.mark.parametrize("mode", ["plaintext", "tls", "mtls"])
def test_all_three_signals_reach_real_grpc_receiver(receiver, certificates, mode):
    target, received, metadata = receiver(mode)
    backend = BackendConfig(
        type="otlp",
        protocol="grpc",
        endpoint=target,
        insecure=mode == "plaintext",
        headers={"Authorization": "test-token"},
    )
    if mode != "plaintext":
        backend = replace(backend, certificate_file=str(certificates / "ca.pem"))
    if mode == "mtls":
        backend = replace(
            backend,
            client_key_file=str(certificates / "client.key"),
            client_certificate_file=str(certificates / "client.pem"),
        )
    plugin = HermesOTelPlugin(
        config=HermesOtelConfig(
            backends=(backend,),
            dashboard_live=False,
            content_capture="off",
            capture_logs=True,
            log_attach_logger="hermes-grpc-test",
            flush_interval_ms=60000,
        )
    )
    try:
        assert plugin.init()
        plugin.start_span(name="grpc-test", key="test", kind="general")
        plugin.end_span("test", status="ok")
        plugin.record_metric("session_count", 1, {"platform": "test"})
        logging.getLogger("hermes-grpc-test").warning("gRPC log test")
        plugin.pipeline.force_flush(timeout_millis=5000)
        assert all(received.values()), {signal: len(items) for signal, items in received.items()}
        spans = [
            span
            for request in received["traces"]
            for resource in request.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        ]
        assert any(span.name == "grpc-test" for span in spans)
        logs = [
            record
            for request in received["logs"]
            for resource in request.resource_logs
            for scope in resource.scope_logs
            for record in scope.log_records
        ]
        assert any(record.body.string_value == "gRPC log test" for record in logs)
        assert all(item.get("authorization") == "test-token" for item in metadata)
    finally:
        plugin.shutdown()


@pytest.mark.parametrize("identity", [None, "untrusted-client"])
def test_mtls_receiver_rejects_missing_or_untrusted_client(receiver, certificates, identity):
    target, received, _ = receiver("mtls")
    backend = resolve(
        BackendConfig(
            type="otlp",
            protocol="grpc",
            endpoint=target,
            certificate_file=str(certificates / "ca.pem"),
            client_key_file=str(certificates / f"{identity}.key") if identity else None,
            client_certificate_file=str(certificates / f"{identity}.pem") if identity else None,
        )
    )
    exporter = create_exporter(backend, "traces", None, endpoint=target, timeout=0.2)
    try:
        assert exporter.export([]) == SpanExportResult.FAILURE
        assert not any(received.values())
    finally:
        exporter.shutdown()


@pytest.mark.parametrize("ca", ["untrusted-ca.pem", "malformed.pem"])
def test_tls_exporter_rejects_untrusted_server_and_malformed_ca(receiver, certificates, ca):
    target, received, _ = receiver("tls")
    (certificates / "malformed.pem").write_bytes(b"not a certificate")
    backend = resolve(
        BackendConfig(
            type="otlp",
            protocol="grpc",
            endpoint=target,
            certificate_file=str(certificates / ca),
        )
    )
    exporter = create_exporter(backend, "traces", None, endpoint=target, timeout=0.2)
    try:
        assert exporter.export([]) == SpanExportResult.FAILURE
        assert not any(received.values())
    finally:
        exporter.shutdown()


def test_exporter_init_failure_does_not_block_another_backend(receiver, certificates):
    target, received, _ = receiver("plaintext")
    plugin = HermesOTelPlugin(
        config=HermesOtelConfig(
            backends=(
                BackendConfig(
                    type="otlp",
                    protocol="grpc",
                    endpoint="https://localhost:443",
                    certificate_file=str(certificates / "missing-ca.pem"),
                ),
                BackendConfig(
                    type="otlp",
                    protocol="grpc",
                    endpoint=f"http://{target}",
                    metrics=False,
                    logs=False,
                ),
            ),
            dashboard_live=False,
            content_capture="off",
        )
    )
    try:
        assert plugin.init()
        assert len(plugin.pipeline.span_processors) == 1
        plugin.start_span(name="healthy-backend", key="test", kind="general")
        plugin.end_span("test", status="ok")
        plugin.pipeline.force_flush(timeout_millis=5000)
        assert received["traces"]
    finally:
        plugin.shutdown()
