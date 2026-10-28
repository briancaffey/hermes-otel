"""Fixtures for the credential-free e2e tier (#482): the real ``hermes`` binary,
driven through this checkout's plugin, against two loopback servers.

* :class:`~_servers.ScriptedProvider` plays the model (``provider: custom``).
* :class:`~_servers.OtlpReceiver` plays the backend (a ``type: otlp`` entry).
* ``hermes_home`` is a ``HERMES_HOME`` built with the scenario harness's
  ``scripts/scenarios/setup_profile.py`` helpers (same Hermes config shape,
  same plugin copy), pointed at the two servers. ``HERMES_E2E_HOME`` names a
  directory to keep between runs (``make ci-e2e`` sets one), so Hermes's
  runtime venv staging, about a minute, happens once; without it the home
  is a temp directory removed at the end of the session.
* ``hermes`` runs ``hermes chat -Q --yolo -q <prompt>`` (``-r <session>`` to
  resume) with a clean environment: no API key, no ``OTEL_*`` or
  ``HERMES_*`` variable from the developer's shell reaches the agent.

Every test skips when no ``hermes`` is on ``PATH`` (``HERMES_BIN`` overrides).
``make ci-e2e`` runs the tier; the default ``pytest`` deselects it.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

import pytest

from e2e_ci._servers import MODEL_ID, OtlpReceiver, ScriptedProvider

REPO = Path(__file__).resolve().parents[2]
SETUP_PROFILE = REPO / "scripts" / "scenarios" / "setup_profile.py"
SESSION_RE = re.compile(r"^session_id:\s*(\S+)\s*$", re.M)
# A turn is seconds once the home is warm; the first turn in a fresh home
# also stages Hermes's runtime venv. With the provider unreachable Hermes
# retries for minutes, so the cap is what turns a broken fixture into a
# failure instead of a hang.
TURN_TIMEOUT_S = float(os.environ.get("HERMES_E2E_TURN_TIMEOUT", "300"))
# Variables the agent's environment keeps from the test runner's. Everything
# else (every *_API_KEY, OTEL_*, HERMES_*) is dropped on purpose.
KEEP_ENV = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TMPDIR", "SHELL", "SSL_CERT_FILE")


def pytest_collection_modifyitems(items: List[pytest.Item]) -> None:
    """Every test in this package carries the ``e2e_ci`` marker."""
    here = Path(__file__).resolve().parent
    for item in items:
        if here in Path(str(item.fspath)).resolve().parents:
            item.add_marker(pytest.mark.e2e_ci)


def _load_setup_profile() -> Any:
    """``scripts/scenarios/setup_profile.py`` as a module (it puts its own
    helper directories on ``sys.path`` when imported)."""
    spec = importlib.util.spec_from_file_location("e2e_setup_profile", SETUP_PROFILE)
    assert spec is not None and spec.loader is not None, SETUP_PROFILE
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def hermes_bin() -> str:
    path = shutil.which(os.environ.get("HERMES_BIN") or "hermes")
    if not path:
        pytest.skip("no `hermes` on PATH (set HERMES_BIN); the e2e_ci tier needs Hermes Agent")
    return path


@pytest.fixture(scope="session")
def provider() -> Iterator[ScriptedProvider]:
    server = ScriptedProvider()
    yield server
    server.close()


@pytest.fixture(scope="session")
def receiver() -> Iterator[OtlpReceiver]:
    server = OtlpReceiver()
    yield server
    server.close()


@pytest.fixture(scope="session")
def hermes_home(
    tmp_path_factory: pytest.TempPathFactory, provider: ScriptedProvider, receiver: OtlpReceiver
) -> Iterator[Path]:
    """A ``HERMES_HOME``: model → the scripted provider, plugin → this
    checkout's ``hermes_otel/`` (copied afresh), backend → the receiver.
    ``HERMES_E2E_HOME`` keeps it between runs; otherwise a temp directory,
    removed afterwards (Hermes stages a runtime venv inside it)."""
    setup_profile = _load_setup_profile()
    persistent = os.environ.get("HERMES_E2E_HOME")
    if persistent:
        home = Path(persistent).expanduser().resolve()
        home.mkdir(parents=True, exist_ok=True)
    else:
        home = tmp_path_factory.mktemp("hermes-home")
    model = {
        "default": MODEL_ID,
        "provider": "custom",
        "base_url": provider.base_url,
        "api_key": "no-key-required",
    }
    setup_profile.write_yaml(
        home / "config.yaml", setup_profile.hermes_config(home, model, approvals_timeout=5)
    )
    setup_profile.install_plugin(home)
    otel, _needed = setup_profile.otel_config("hermes-e2e-ci", None)
    otel.update(
        {
            "backends": [
                {
                    "type": "otlp",
                    "name": "receiver",
                    "endpoint": f"{receiver.endpoint}/v1/traces",
                    "metrics": True,
                    "logs": True,
                }
            ],
            # Events on, so a turn produces hermes.turn.* / hermes.tool.call records.
            "logs": {"capture": True, "level": "INFO", "events": {"enabled": True}},
            # The receiver is the oracle; no live-store database in the home.
            "dashboard_live": False,
        }
    )
    setup_profile.write_yaml(home / "hermes_otel.yaml", otel)
    yield home
    if not persistent:
        shutil.rmtree(home, ignore_errors=True)


@dataclass
class Turn:
    """One ``hermes chat`` process and what it left behind."""

    prompt: str
    command: List[str]
    exit_code: Optional[int]
    duration_s: float
    stdout: str
    stderr: str
    session_id: Optional[str]
    debug_log: str = ""
    resumed: Optional[str] = None

    @property
    def answer(self) -> str:
        return "\n".join(
            ln for ln in self.stdout.strip().splitlines() if not ln.startswith("session_id:")
        )

    def describe(self) -> str:
        """Everything a failing assertion should show."""
        return (
            f"command: {' '.join(self.command)}\n"
            f"exit: {self.exit_code} in {self.duration_s:.1f}s, session: {self.session_id}\n"
            f"--- stdout (tail) ---\n{self.stdout[-2000:]}\n"
            f"--- stderr (tail) ---\n{self.stderr[-4000:]}\n"
            f"--- plugin debug.log (tail) ---\n{self.debug_log[-4000:]}"
        )


def _text(value: Union[bytes, str, None]) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


class HermesRunner:
    def __init__(
        self, binary: str, home: Path, provider: ScriptedProvider, receiver: OtlpReceiver, cwd: Path
    ) -> None:
        self.binary = binary
        self.home = home
        self.provider = provider
        self.receiver = receiver
        self.cwd = cwd
        self.turns: List[Turn] = []

    def env(self) -> Dict[str, str]:
        env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
        env["HERMES_HOME"] = str(self.home)
        env["HERMES_OTEL_DEBUG"] = "1"  # plugins/hermes_otel/debug.log, shown on failure
        env["NO_COLOR"] = "1"
        return env

    def turn(self, prompt: str, *script: Dict[str, Any], resume: Optional[str] = None) -> Turn:
        """Run one ``hermes chat`` turn with ``script`` loaded into the provider.
        The receiver and the provider's request log are cleared first, so what
        a test reads afterwards came from this process; the plugin flushes
        every signal before the process exits."""
        self.receiver.clear()
        self.provider.clear()
        self.provider.script(*script)
        cmd = [self.binary, "chat", "-Q", "--yolo"]
        if resume:
            cmd += ["-r", resume]
        cmd += ["-q", prompt]
        started = time.monotonic()
        exit_code: Optional[int]
        try:
            proc = subprocess.run(
                cmd,
                env=self.env(),
                cwd=str(self.cwd),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=TURN_TIMEOUT_S,
                check=False,
            )
            stdout, stderr, exit_code = _text(proc.stdout), _text(proc.stderr), proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, exit_code = _text(exc.stdout), _text(exc.stderr), None
        duration = time.monotonic() - started
        found = SESSION_RE.findall(stdout + "\n" + stderr)
        debug_log = self.home / "plugins" / "hermes_otel" / "debug.log"
        turn = Turn(
            prompt=prompt,
            command=cmd,
            exit_code=exit_code,
            duration_s=duration,
            stdout=stdout,
            stderr=stderr,
            session_id=found[-1] if found else resume,
            debug_log=(
                debug_log.read_text(encoding="utf-8", errors="replace")
                if debug_log.exists()
                else ""
            ),
            resumed=resume,
        )
        self.turns.append(turn)
        return turn

    def stop_gateway(self) -> None:
        """Stop the gateway Hermes main starts to run a ``hermes chat`` turn in
        (#637). It outlives the chat process, and a later run against a kept
        ``HERMES_E2E_HOME`` would reuse it, plugin and dead receiver port
        included. A Hermes that started none says so and exits."""
        try:
            subprocess.run(
                [self.binary, "gateway", "stop"],
                env=self.env(),
                cwd=str(self.cwd),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=TURN_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pass

    def completed_session(self) -> Optional[str]:
        """The session id of an earlier successful fresh turn, if any."""
        for turn in self.turns:
            if turn.exit_code == 0 and turn.session_id and not turn.resumed:
                return turn.session_id
        return None


@pytest.fixture(scope="session")
def hermes(
    hermes_bin: str,
    hermes_home: Path,
    provider: ScriptedProvider,
    receiver: OtlpReceiver,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[HermesRunner]:
    workdir = tmp_path_factory.mktemp("hermes-workdir")
    runner = HermesRunner(hermes_bin, hermes_home, provider, receiver, workdir)
    runner.stop_gateway()  # one left from an earlier run against this home
    yield runner
    runner.stop_gateway()
