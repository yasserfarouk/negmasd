"""`negmasd builtin` end to end: both protocols, and the port handling.

`builtin` is the documented way to try the project out, so it gets its own
coverage. Two behaviours matter beyond "a negotiation happens":

* it serves over either protocol (``--protocol``), and
* it no longer dies when a preferred port is taken. The bundled YAML hardcodes
  9010/8001/8002; `builtin` now picks free ports when those are busy and rewrites
  the configs so the agents still find each other and the runner.

These launch real processes, so they are all ``slow``.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from negmasd.transport import Address, channel_for, close_all_channels

EXE = Path(sys.executable).parent / "negmasd"
READY = re.compile(r"builtin ready: (\{.*\})")

pytestmark = pytest.mark.slow


@pytest.fixture
def clean_channels():
    close_all_channels()
    yield
    close_all_channels()


class Builtin:
    """Runs `negmasd builtin procurement` and exposes the ports it chose."""

    def __init__(self, tmp_path: Path, *args: str):
        self.log_path = tmp_path / "builtin.log"
        self._log = self.log_path.open("w")
        self.proc = subprocess.Popen(
            [str(EXE), "builtin", "procurement", *args],
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        self.summary: dict = {}

    def wait_ready(self, timeout: float = 120.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self.log_path.read_text()
            match = READY.search(text)
            if match:
                self.summary = json.loads(match.group(1))
                return self.summary
            if self.proc.poll() is not None:
                raise RuntimeError(f"builtin exited early:\n{text[-3000:]}")
            time.sleep(0.3)
        raise RuntimeError(f"no ready line within {timeout}s:\n{self.log_path.read_text()[-3000:]}")

    def channel(self, which: str):
        s = self.summary
        port = s["runner"] if which == "runner" else s["agents"][which]
        return channel_for(Address(host=s["host"], port=port, protocol=s["protocol"]))

    def negotiate(self, timeout: float = 90.0) -> dict:
        resp = self.channel("buyer").call(
            "start-negotiation", {"neg": "trade"}, http_method="get"
        )
        assert resp["status"] == "success", resp
        runner = self.channel("runner")
        deadline = time.time() + timeout
        while time.time() < deadline:
            sessions = runner.call("peak", {}, http_method="get")["sessions"]
            if sessions:
                result = next(iter(sessions.values()))
                return dict(
                    agreement=result["agreement"],
                    step=result["final_state"]["step"],
                    timedout=result["final_state"]["timedout"],
                )
            time.sleep(0.2)
        raise RuntimeError("runner produced no result")

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._log.close()


@pytest.fixture
def builtin(tmp_path, clean_channels):
    started: list[Builtin] = []

    def start(*args: str) -> Builtin:
        app = Builtin(tmp_path, *args)
        started.append(app)
        app.wait_ready()
        return app

    yield start
    for app in started:
        app.close()


@pytest.mark.parametrize("protocol", ["http", pytest.param("grpc", marks=pytest.mark.grpc)])
def test_builtin_reaches_an_agreement_over_each_protocol(builtin, protocol):
    if protocol == "grpc":
        pytest.importorskip("grpc", reason="needs the optional grpcio extra")
    app = builtin("--protocol", protocol)
    assert app.summary["protocol"] == protocol
    assert set(app.summary["agents"]) == {"buyer", "seller"}

    result = app.negotiate()
    # The bundled app sets no seed, so the exact agreement is not pinned here;
    # cross-protocol *equality* is asserted (with a seed) in test_grpc_parity.
    assert result["agreement"] is not None, result
    assert not result["timedout"], result


def test_builtin_uses_a_preferred_port_when_it_is_free(builtin):
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        wanted = s.getsockname()[1]
    app = builtin("--runner-port", str(wanted))
    assert app.summary["runner"] == wanted


def test_builtin_falls_back_when_a_preferred_port_is_busy(builtin):
    """The regression that used to sink the whole app: something else holds the
    port, every server logs 'address already in use', and the config push then
    fails against a runner that never listened."""
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    try:
        app = builtin("--runner-port", str(busy))
        assert app.summary["runner"] != busy, "should not have reused the busy port"
        # ...and the app is genuinely wired up on the substitute port.
        assert app.negotiate()["agreement"] is not None
    finally:
        blocker.close()


def test_builtin_refuses_to_substitute_ports_without_config_rewriting(tmp_path):
    """`--no-load-configs` opts out of the rewriting that makes a substituted port
    safe, so a busy preferred port must fail loudly rather than start an app whose
    configs point at the wrong places."""
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    try:
        proc = subprocess.run(
            [
                str(EXE), "builtin", "procurement",
                "--no-load-configs", "--runner-port", str(busy),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        blocker.close()
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "already in use" in proc.stdout, proc.stdout
    assert "--no-load-configs" in proc.stdout, proc.stdout


def test_builtin_removes_its_generated_config_dir(builtin):
    """The rewritten configs live in a temp dir; it must not leak per run.

    The path is read from the structured ready line, not the human-readable one --
    rich wraps long paths at the console width.
    """
    app = builtin()
    cfg_dir = Path(app.summary["configs"])
    assert cfg_dir.is_dir(), cfg_dir
    app.close()
    deadline = time.time() + 20
    while time.time() < deadline and cfg_dir.exists():
        time.sleep(0.2)
    assert not cfg_dir.exists(), f"{cfg_dir} was left behind"
