"""The optional transports must stay genuinely optional.

``grpcio`` is a compiled wheel that fails to install on some Python builds, and
``paho-mqtt`` is only wanted by MQTT deployments, so the HTTP path may not depend
on either — not even transitively. These tests hold that line for *both*, and,
unlike the per-transport parity suites, they run whether or not the extras are
installed: the absent case is simulated by blocking the import.

The specific hazard is eager importing. ``negmasd_pb2_grpc.py`` does ``import
grpc`` at module scope and ``transport/mqtt.py`` does ``import paho.mqtt.client``,
so one stray top-level import anywhere in the package would make an extra
mandatory in practice while still looking optional in ``pyproject.toml``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

# protocol -> (top-level module it needs, the extra that provides it)
OPTIONAL_TRANSPORTS = {
    "grpc": ("grpc", "grpc"),
    "mqtt": ("paho", "mqtt"),
}
# Every top-level module no default-path import is allowed to pull in.
OPTIONAL_MODULES = tuple(module for module, _ in OPTIONAL_TRANSPORTS.values())

_BLOCK_TEMPLATE = """
import sys, importlib.abc

_BLOCKED = {blocked!r}

class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root in _BLOCKED:
            raise ImportError(f"No module named {{root!r}} (blocked for test)")
        return None

sys.meta_path.insert(0, _Blocker())
"""


def _run(body: str, block: tuple[str, ...] = ()) -> str:
    """Run a snippet in a subprocess, optionally with imports blocked."""
    prelude = _BLOCK_TEMPLATE.format(blocked=list(block)) if block else ""
    proc = subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"subprocess failed:\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    return proc.stdout


def test_default_path_never_imports_an_optional_transport():
    """Importing everything on the HTTP path must not pull in grpc or paho."""
    out = _run(
        f"""
        import sys
        import negmasd
        import negmasd.agent, negmasd.runner, negmasd.ufun_service
        import negmasd.cli, negmasd.common, negmasd.models
        import negmasd.negotiator, negmasd.ufuns, negmasd.wire
        import negmasd.transport
        leaked = sorted(
            m for m in sys.modules if m.split(".")[0] in {OPTIONAL_MODULES!r}
        )
        assert not leaked, f"optional dependency imported: {{leaked}}"
        print("OK")
        """
    )
    assert "OK" in out


def test_whole_http_stack_works_without_the_optional_extras():
    """With both optional modules unimportable, all three services still serve."""
    out = _run(
        f"""
        from fastapi.testclient import TestClient
        from negmasd.agent import AgentService
        from negmasd.runner import RunnerService
        from negmasd.ufun_service import UfunService
        from negmasd.transport import Address, build_app, channel_for, known_protocols

        for factory in (AgentService, RunnerService, UfunService):
            service = factory()
            client = TestClient(build_app(service))
            assert client.get("/").status_code == 200, factory
            # A method with a request model bound as query params.
            r = client.get("/load-config", params={{"path": "/definitely/missing.yml"}})
            assert r.status_code == 200 and r.json()["status"] == "failure", r.text

        # The HTTP transport itself is unaffected...
        assert isinstance(channel_for(Address(port=1)).base_url, str)
        # ...and every protocol is still *advertised*, just not loaded.
        assert set(known_protocols()) == {{"http", "grpc", "mqtt"}}
        print("OK")
        """,
        block=OPTIONAL_MODULES,
    )
    assert "OK" in out


def test_hot_path_wire_works_without_the_optional_extras():
    """The per-round respond path (the reason this project exists) is unaffected."""
    out = _run(
        """
        import json
        from negmas import ResponseType, SAOState
        from negmas.sao.common import SAOResponse
        from negmasd.wire import decode_response, decode_state, encode_response, encode_state

        state = SAOState(step=3, current_offer=("a", "b"), n_negotiators=2)
        wire = json.loads(json.dumps(encode_state(state)))
        assert decode_state(wire).current_offer == ("a", "b")

        resp = SAOResponse(ResponseType.ACCEPT_OFFER, ("a", "b"))
        back = decode_response(json.loads(json.dumps(encode_response(resp))))
        assert back.response is ResponseType.ACCEPT_OFFER
        assert back.outcome == ("a", "b")
        print("OK")
        """,
        block=OPTIONAL_MODULES,
    )
    assert "OK" in out


@pytest.mark.parametrize(
    "protocol,module,extra",
    [(p, m, e) for p, (m, e) in OPTIONAL_TRANSPORTS.items()],
)
def test_requesting_an_uninstalled_transport_is_actionable(protocol, module, extra):
    """One clear message naming the fix — not a raw ImportError traceback."""
    out = _run(
        f"""
        from negmasd.agent import AgentService
        from negmasd.transport import (
            Address, TransportUnavailable, channel_for, serve_service,
        )

        for describe, thunk in (
            ("channel", lambda: channel_for(Address(port=1, protocol={protocol!r}))),
            ("server", lambda: serve_service(
                AgentService(), port=1, protocol={protocol!r}
            )),
        ):
            try:
                thunk()
            except TransportUnavailable as e:
                message = str(e)
                assert {protocol!r} in message, message
                assert "uv sync --extra {extra}" in message, message
            else:
                raise AssertionError(f"{{describe}}: expected TransportUnavailable")
        print("OK")
        """,
        block=(module,),
    )
    assert "OK" in out


def test_unknown_protocol_is_rejected_clearly():
    out = _run(
        """
        from negmasd.transport import Address, TransportError, channel_for
        try:
            channel_for(Address(port=1, protocol="carrier-pigeon"))
        except TransportError as e:
            assert "carrier-pigeon" in str(e) and "http" in str(e), str(e)
        else:
            raise AssertionError("expected TransportError")
        print("OK")
        """
    )
    assert "OK" in out
