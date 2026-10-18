"""Backend-neutral OTLP transport, endpoint and credential contracts."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from hermes_otel.backend_config import BackendConfig, coerce_backends
from hermes_otel.backends import _ResolvedBackend, resolve
from hermes_otel.otlp_transport import create_exporter


@pytest.fixture(autouse=True)
def clean_transport_env(monkeypatch):
    import os

    for name in os.environ:
        if name.startswith("OTEL_EXPORTER_OTLP"):
            monkeypatch.delenv(name)


@pytest.fixture
def backend():
    return resolve(
        BackendConfig(type="otlp", endpoint="https://telemetry.example:443", protocol="grpc")
    )


@pytest.fixture
def grpc_exporters(monkeypatch):
    mocks = {}
    for signal, module, cls in (
        ("traces", "trace_exporter", "OTLPSpanExporter"),
        ("metrics", "metric_exporter", "OTLPMetricExporter"),
        ("logs", "_log_exporter", "OTLPLogExporter"),
    ):
        factory = Mock()
        monkeypatch.setattr(f"opentelemetry.exporter.otlp.proto.grpc.{module}.{cls}", factory)
        mocks[signal] = factory
    return mocks


@pytest.mark.parametrize("signal", ["traces", "metrics", "logs"])
def test_grpc_uses_same_target_and_selected_exporter(backend, grpc_exporters, signal):
    http = Mock()
    endpoint = backend.target(signal).endpoint
    result = create_exporter(
        backend, signal, http, endpoint=endpoint, headers={"Authorization": "token"}
    )
    grpc_exporters[signal].assert_called_once_with(
        endpoint="telemetry.example:443", insecure=False, headers={"authorization": "token"}
    )
    assert result is grpc_exporters[signal].return_value
    http.assert_not_called()


@pytest.mark.parametrize("signal", ["traces", "metrics", "logs"])
def test_http_defaults_preserve_constructor_arguments(signal):
    backend = resolve(BackendConfig(type="otlp", endpoint="http://localhost:4318/v1/traces"))
    endpoint = backend.target(signal).endpoint
    assert endpoint == f"http://localhost:4318/v1/{signal}"
    http = Mock()
    create_exporter(backend, signal, http, endpoint=endpoint, headers=None)
    http.assert_called_once_with(endpoint=endpoint, headers=None)


@pytest.mark.parametrize("protocol", ["grpc", "http/protobuf"])
@pytest.mark.parametrize("signal", ["metrics", "logs"])
def test_signal_endpoint_overrides(protocol, signal):
    backend = _ResolvedBackend(type="otlp", endpoint="https://traces:443", protocol=protocol)
    backend = replace(backend, **{f"{signal}_endpoint": "https://other:443"})
    assert backend.target(signal).endpoint == "https://other:443"


def test_yaml_loads_transport_fields(monkeypatch):
    monkeypatch.setenv("CLIENT_CERT", "~/certs/client.pem")
    entries = coerce_backends(
        [
            {
                "type": "otlp",
                "protocol": "grpc",
                "endpoint": "https://telemetry:443",
                "insecure": "false",
                "certificate_file": "~/certs/ca.pem",
                "client_certificate_file": "${CLIENT_CERT}",
                "client_key_file": "~/certs/client.key",
            }
        ]
    )
    backend = resolve(entries[0])
    assert backend.protocol == "grpc" and backend.insecure is False
    assert backend.certificate_file == "~/certs/ca.pem"
    assert backend.client_certificate_file == "~/certs/client.pem"
    assert backend.client_key_file == "~/certs/client.key"


@pytest.mark.parametrize("invalid", ["maybe", 1, [], {}])
def test_yaml_rejects_invalid_insecure_instead_of_inferring_plaintext(invalid, caplog):
    assert coerce_backends([{"type": "otlp", "protocol": "grpc", "insecure": invalid}]) is None
    assert "insecure must be a boolean" in caplog.text


@pytest.mark.parametrize(
    "config",
    [
        BackendConfig(type="otlp", endpoint="localhost:4317", protocol="unknown"),
        BackendConfig(type="langfuse", protocol="grpc"),
    ],
)
def test_resolution_rejects_unsupported_protocol(config):
    with pytest.raises(ValueError):
        resolve(config)


@pytest.mark.parametrize("signal", ["traces", "metrics", "logs"])
def test_tls_files_pass_to_grpc_credentials(backend, grpc_exporters, tmp_path, monkeypatch, signal):
    paths = {}
    for field in ("certificate_file", "client_key_file", "client_certificate_file"):
        path = tmp_path / field
        path.write_bytes(field.encode())
        paths[field] = str(path)
    credentials = Mock()
    monkeypatch.setattr("grpc.ssl_channel_credentials", credentials)
    backend = replace(backend, **paths)
    create_exporter(backend, signal, Mock(), endpoint=backend.endpoint)
    credentials.assert_called_once_with(
        root_certificates=b"certificate_file",
        private_key=b"client_key_file",
        certificate_chain=b"client_certificate_file",
    )
    assert grpc_exporters[signal].call_args.kwargs["credentials"] is credentials.return_value


def test_mtls_with_system_roots_does_not_require_private_ca(
    backend, grpc_exporters, tmp_path, monkeypatch
):
    key, cert = tmp_path / "key", tmp_path / "cert"
    key.write_bytes(b"key")
    cert.write_bytes(b"cert")
    backend = replace(backend, client_key_file=str(key), client_certificate_file=str(cert))
    credentials = Mock()
    monkeypatch.setattr("grpc.ssl_channel_credentials", credentials)
    create_exporter(backend, "traces", Mock(), endpoint=backend.endpoint)
    credentials.assert_called_once_with(
        root_certificates=None, private_key=b"key", certificate_chain=b"cert"
    )


@pytest.mark.parametrize("signal", ["traces", "metrics", "logs"])
def test_tls_file_precedence_and_home_expansion(
    backend, grpc_exporters, tmp_path, monkeypatch, signal
):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("global", "signal", "explicit"):
        (tmp_path / name).write_bytes(name.encode())
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_CERTIFICATE", str(tmp_path / "global"))
    monkeypatch.setenv(f"OTEL_EXPORTER_OTLP_{signal.upper()}_CERTIFICATE", str(tmp_path / "signal"))
    credentials = Mock()
    monkeypatch.setattr("grpc.ssl_channel_credentials", credentials)
    for configured, expected in ((None, b"signal"), ("~/explicit", b"explicit")):
        create_exporter(
            replace(backend, certificate_file=configured), signal, Mock(), endpoint=backend.endpoint
        )
        assert credentials.call_args.kwargs["root_certificates"] == expected
    monkeypatch.delenv(f"OTEL_EXPORTER_OTLP_{signal.upper()}_CERTIFICATE")
    create_exporter(backend, signal, Mock(), endpoint=backend.endpoint)
    assert credentials.call_args.kwargs["root_certificates"] == b"global"


def test_http_tls_files_are_passed_to_sdk(backend, tmp_path):
    path = tmp_path / "ca"
    path.write_bytes(b"CA")
    backend = replace(
        backend,
        protocol="http/protobuf",
        certificate_file=str(path),
        client_key_file=str(path),
        client_certificate_file=str(path),
    )
    http = Mock()
    create_exporter(backend, "traces", http, endpoint=backend.endpoint)
    http.assert_called_once_with(
        endpoint=backend.endpoint,
        certificate_file=str(path),
        client_key_file=str(path),
        client_certificate_file=str(path),
    )


@pytest.mark.parametrize("field", ["client_key_file", "client_certificate_file"])
def test_incomplete_mtls_is_rejected(backend, tmp_path, field):
    path = tmp_path / "pem"
    path.write_bytes(b"PEM")
    with pytest.raises(ValueError, match="supplied together"):
        create_exporter(
            replace(backend, **{field: str(path)}), "traces", Mock(), endpoint=backend.endpoint
        )


@pytest.mark.parametrize("filename", ["missing", "empty", " "])
def test_bad_ca_file_never_falls_back_to_system_trust(backend, tmp_path, filename):
    (tmp_path / "empty").touch()
    path = " " if filename == " " else str(tmp_path / filename)
    with pytest.raises((ValueError, OSError)):
        create_exporter(
            replace(backend, certificate_file=path), "traces", Mock(), endpoint=backend.endpoint
        )


@pytest.mark.parametrize(
    "endpoint,insecure,expected",
    [
        ("http://localhost:4317", None, True),
        ("localhost:4317", None, False),
        ("https://localhost:443", None, False),
        ("http://localhost:4317", False, False),
        ("localhost:4317", True, True),
    ],
)
def test_grpc_security_modes(backend, grpc_exporters, endpoint, insecure, expected):
    create_exporter(replace(backend, insecure=insecure), "traces", Mock(), endpoint=endpoint)
    assert grpc_exporters["traces"].call_args.kwargs["insecure"] is expected


def test_grpc_insecure_env_precedence(backend, grpc_exporters, monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_INSECURE", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_INSECURE", "false")
    create_exporter(backend, "traces", Mock(), endpoint="localhost:4317")
    assert grpc_exporters["traces"].call_args.kwargs["insecure"] is False
    create_exporter(replace(backend, insecure=True), "traces", Mock(), endpoint="localhost:4317")
    assert grpc_exporters["traces"].call_args.kwargs["insecure"] is True
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_INSECURE")
    create_exporter(backend, "traces", Mock(), endpoint="localhost:4317")
    assert grpc_exporters["traces"].call_args.kwargs["insecure"] is True


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://collector/v1/traces",
        "https://collector?secret=x",
        "https://collector#fragment",
        "https://user:pass@collector:443",
        "ftp://collector:443",
        "",
        "/path",
    ],
)
def test_grpc_rejects_http_paths_and_invalid_targets(backend, endpoint):
    with pytest.raises(ValueError):
        create_exporter(backend, "traces", Mock(), endpoint=endpoint)


def test_rejects_security_conflicts(backend, tmp_path, monkeypatch):
    path = tmp_path / "ca"
    path.write_bytes(b"CA")
    for b, endpoint in (
        (replace(backend, insecure=True), "https://collector:443"),
        (replace(backend, insecure="false"), "collector:443"),
        (replace(backend, insecure=True, certificate_file=str(path)), "http://collector:4317"),
        (
            replace(backend, protocol="http/protobuf", certificate_file=str(path)),
            "http://collector:4318/v1/traces",
        ),
        (replace(backend, protocol="http/protobuf", insecure=False), "https://collector/v1/traces"),
        (replace(backend, protocol="invalid"), "collector:443"),
    ):
        with pytest.raises(ValueError):
            create_exporter(b, "traces", Mock(), endpoint=endpoint)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_INSECURE", "maybe")
    with pytest.raises(ValueError, match="true or false"):
        create_exporter(backend, "traces", Mock(), endpoint=backend.endpoint)


@pytest.mark.parametrize("signal", ["traces", "metrics", "logs"])
def test_http_combined_client_pem(signal, tmp_path, monkeypatch):
    combined = tmp_path / "combined.pem"
    combined.write_bytes(b"combined cert and key")
    monkeypatch.setenv(f"OTEL_EXPORTER_OTLP_{signal.upper()}_CLIENT_CERTIFICATE", str(combined))
    backend = resolve(BackendConfig(type="otlp", endpoint="https://collector/v1/traces"))
    factory = Mock()
    create_exporter(backend, signal, factory, endpoint=backend.endpoint)
    factory.assert_called_once_with(
        endpoint=backend.endpoint, client_certificate_file=str(combined)
    )


@pytest.mark.parametrize("signal", ["traces", "metrics", "logs"])
def test_grpc_client_identity_from_sdk_env(backend, grpc_exporters, tmp_path, monkeypatch, signal):
    key, cert = tmp_path / "key", tmp_path / "cert"
    key.write_bytes(b"key")
    cert.write_bytes(b"cert")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_CLIENT_KEY", str(key))
    monkeypatch.setenv(f"OTEL_EXPORTER_OTLP_{signal.upper()}_CLIENT_CERTIFICATE", str(cert))
    credentials = Mock()
    monkeypatch.setattr("grpc.ssl_channel_credentials", credentials)
    create_exporter(backend, signal, Mock(), endpoint=backend.endpoint)
    credentials.assert_called_once_with(
        root_certificates=None, private_key=b"key", certificate_chain=b"cert"
    )


# ── Follow-ups from merging main (#469) ─────────────────────────────────────


def test_grpc_with_a_headers_helper_is_refused():
    # The 401/403 re-mint rides the HTTP session; a gRPC token would expire.
    with pytest.raises(ValueError, match="headers_helper needs protocol: http/protobuf"):
        resolve(
            BackendConfig(
                type="otlp", endpoint="localhost:4317", protocol="grpc", headers_helper="mint"
            )
        )
    http = resolve(BackendConfig(type="otlp", endpoint="http://x/v1/traces", headers_helper="m"))
    assert http.headers_helper is not None


def test_http_helper_session_reaches_the_http_exporter():
    session = object()
    http = Mock()
    backend = resolve(BackendConfig(type="otlp", endpoint="http://x:4318/v1/traces"))
    create_exporter(
        backend, "traces", http, endpoint=backend.endpoint, headers=None, session=session
    )
    http.assert_called_once_with(endpoint=backend.endpoint, headers=None, session=session)


def test_protocol_and_tls_fields_change_the_fingerprint():
    from hermes_otel.plugin_config import HermesOtelConfig

    def fp(**fields):
        entry = BackendConfig(type="otlp", endpoint="localhost:4317", **fields)
        return HermesOtelConfig(backends=(entry,)).fingerprint()

    assert fp() != fp(protocol="grpc")
    assert fp(protocol="grpc") != fp(protocol="grpc", certificate_file="/ca.pem")
    assert fp(protocol="grpc", insecure=True) != fp(protocol="grpc", insecure=False)


def test_settings_card_lists_protocol_only_when_it_is_not_the_default():
    from hermes_otel.settings_report import _backend_summary

    http = BackendConfig(type="otlp", endpoint="http://x:4318/v1/traces")
    grpc_entry = BackendConfig(type="otlp", endpoint="x:4317", protocol="grpc", insecure=True)
    assert "protocol" not in _backend_summary(http, {}, False)["fields"]
    fields = _backend_summary(grpc_entry, {}, False)["fields"]
    assert fields["protocol"] == "grpc" and fields["insecure"] is True
