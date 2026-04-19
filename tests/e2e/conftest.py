"""E2E test fixtures for Docker-based observability backends."""

import os
import subprocess
import time
from pathlib import Path

import pytest

COMPOSE_DIR = Path(__file__).parent.parent.parent / "docker-compose"


def _is_port_open(host, port, timeout=2):
    """Check if a TCP port is accepting connections."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _wait_for_service(host, port, timeout=60, interval=2):
    """Poll until a service is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_port_open(host, port):
            return True
        time.sleep(interval)
    return False


@pytest.fixture(scope="session")
def phoenix_service():
    """Start a Phoenix container for E2E tests.

    If Phoenix is already running on port 6006, reuse it.
    Otherwise, start a Docker container and tear it down after the session.
    """
    host = "localhost"
    port = 6006
    container_name = "hermes-otel-test-phoenix"

    # Check if already running
    if _is_port_open(host, port):
        yield f"http://{host}:{port}"
        return

    # Start Phoenix container
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container_name,
                "-p",
                f"{port}:{port}",
                "arizephoenix/phoenix:latest",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        pytest.skip("Docker not available")
    except subprocess.CalledProcessError as e:
        # Container name might already exist (leftover from previous run)
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
        )
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container_name,
                "-p",
                f"{port}:{port}",
                "arizephoenix/phoenix:latest",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    if not _wait_for_service(host, port, timeout=60):
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        pytest.fail("Phoenix container failed to start within 60 seconds")

    # Give Phoenix a moment to fully initialize
    time.sleep(2)

    yield f"http://{host}:{port}"

    # Cleanup
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True,
    )


def _wait_for_url(url, timeout=120, interval=3):
    """Poll until an HTTP URL returns 200."""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _load_dotenv(path):
    """Read key=value pairs from a .env file into a dict (ignores comments)."""
    result = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    result[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return result


@pytest.fixture(scope="session")
def langfuse_service():
    """Start the Langfuse stack via docker compose for E2E tests.

    If Langfuse is already running on port 3000, reuse it.
    Otherwise, bring up the stack from docker-compose/langfuse.yaml
    and tear it down after the session.

    Keys are resolved from (in order): environment variables, ~/.hermes/.env,
    then the pre-seeded keys from docker-compose/langfuse.yaml.

    Yields a dict with: base_url, public_key, secret_key, otel_endpoint.
    """
    host = "localhost"
    port = 3000
    compose_file = str(COMPOSE_DIR / "langfuse.yaml")

    # Load keys from ~/.hermes/.env as a fallback for env vars
    hermes_env = _load_dotenv(os.path.expanduser("~/.hermes/.env"))

    # Use env vars if set (for existing Langfuse instances), otherwise
    # fall back to ~/.hermes/.env, then the pre-seeded test keys.
    public_key = (
        os.environ.get("OTEL_LANGFUSE_PUBLIC_API_KEY")
        or os.environ.get("LANGFUSE_PUBLIC_KEY")
        or hermes_env.get("OTEL_LANGFUSE_PUBLIC_API_KEY")
        or hermes_env.get("LANGFUSE_PUBLIC_KEY")
        or "lf_pk_test_hermes_otel"
    )
    secret_key = (
        os.environ.get("OTEL_LANGFUSE_SECRET_API_KEY")
        or os.environ.get("LANGFUSE_SECRET_KEY")
        or hermes_env.get("OTEL_LANGFUSE_SECRET_API_KEY")
        or hermes_env.get("LANGFUSE_SECRET_KEY")
        or "lf_sk_test_hermes_otel"
    )

    # The OTEL ingestion endpoint may differ from the REST API base URL.
    otel_endpoint = (
        os.environ.get("OTEL_LANGFUSE_ENDPOINT")
        or hermes_env.get("OTEL_LANGFUSE_ENDPOINT")
        or f"http://{host}:{port}/api/public/otel/v1/traces"
    )

    service_info = {
        "base_url": f"http://{host}:{port}",
        "public_key": public_key,
        "secret_key": secret_key,
        "otel_endpoint": otel_endpoint,
    }

    already_running = _is_port_open(host, port)
    started_by_us = False

    if not already_running:
        if not os.path.isfile(compose_file):
            pytest.skip(f"Langfuse compose file not found: {compose_file}")

        try:
            subprocess.run(
                ["docker", "compose", "-f", compose_file, "up", "-d"],
                check=True,
                capture_output=True,
                text=True,
            )
            started_by_us = True
        except FileNotFoundError:
            pytest.skip("Docker not available")
        except subprocess.CalledProcessError as e:
            pytest.fail(f"Failed to start Langfuse stack: {e.stderr}")

    # Wait for the health endpoint
    health_url = f"http://{host}:{port}/api/public/health"
    if not _wait_for_url(health_url, timeout=120):
        if started_by_us:
            subprocess.run(
                ["docker", "compose", "-f", compose_file, "down", "-v"],
                capture_output=True,
            )
        pytest.fail("Langfuse failed to become healthy within 120 seconds")

    yield service_info

    # Cleanup — only tear down if we started it
    if started_by_us:
        subprocess.run(
            ["docker", "compose", "-f", compose_file, "down", "-v"],
            capture_output=True,
        )
