"""gRPC must stay genuinely optional.

``grpcio`` is a compiled wheel that fails to install on some Python builds, so
the HTTP path may not depend on it even transitively. These tests hold that line
and — unlike ``test_grpc_parity`` — they run whether or not the extra is
installed: the gRPC-absent case is simulated by blocking the import.

The specific hazard being guarded is eager importing. The generated
``negmasd_pb2_grpc.py`` does ``import grpc`` at module scope, so a single stray
top-level import anywhere in the package would make ``grpcio`` mandatory in
practice while still looking optional in ``pyproject.toml``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

# Inserted at the top of a subprocess to make `import grpc` fail exactly as it
# would on a machine where the extra was never installed.
BLOCK_GRPC = """
import sys, importlib.abc

class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "grpc" or fullname.startswith("grpc."):
            raise ImportError("No module named 'grpc' (blocked for test)")
        return None

sys.meta_path.insert(0, _Blocker())
"""


def _run(body: str, block_grpc: bool = False) -> str:
    script = (BLOCK_GRPC if block_grpc else "") + textwrap.dedent(body)
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, (
        f"subprocess failed:\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    return proc.stdout


def test_http_path_never_imports_grpc():
    """Importing everything on the HTTP path must not pull in grpc."""
    out = _run(
        """
        import sys
        import negmasd
        import negmasd.agent, negmasd.runner, negmasd.ufun_service
        import negmasd.cli, negmasd.common, negmasd.models
        import negmasd.negotiator, negmasd.ufuns, negmasd.wire
        import negmasd.transport
        leaked = [m for m in sys.modules if m == "grpc" or m.startswith("grpc.")]
        assert not leaked, f"grpc was imported: {leaked}"
        print("OK")
        """
    )
    assert "OK" in out


def test_whole_http_stack_works_without_grpcio():
    """With grpc unimportable, all three services still build and serve."""
    out = _run(
        """
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
            r = client.get("/load-config", params={"path": "/definitely/missing.yml"})
            assert r.status_code == 200 and r.json()["status"] == "failure", r.text

        # The HTTP transport itself is unaffected...
        assert isinstance(channel_for(Address(port=1)).base_url, str)
        # ...and grpc is still *advertised*, just not loaded.
        assert set(known_protocols()) == {"http", "grpc"}
        print("OK")
        """,
        block_grpc=True,
    )
    assert "OK" in out


def test_hot_path_wire_works_without_grpcio():
    """The per-round respond path (the reason this project exists) is unaffected."""
    out = _run(
        """
        import json
        from negmas import ResponseType, SAOState
        from negmasd.wire import decode_response, decode_state, encode_response, encode_state
        from negmas.sao.common import SAOResponse

        state = SAOState(step=3, current_offer=("a", "b"), n_negotiators=2)
        wire = json.loads(json.dumps(encode_state(state)))
        assert decode_state(wire).current_offer == ("a", "b")

        resp = SAOResponse(ResponseType.ACCEPT_OFFER, ("a", "b"))
        back = decode_response(json.loads(json.dumps(encode_response(resp))))
        assert back.response is ResponseType.ACCEPT_OFFER
        assert back.outcome == ("a", "b")
        print("OK")
        """,
        block_grpc=True,
    )
    assert "OK" in out


def test_requesting_grpc_without_grpcio_is_actionable():
    """One clear message naming the fix — not a raw ImportError traceback."""
    out = _run(
        """
        from negmasd.agent import AgentService
        from negmasd.transport import Address, TransportUnavailable, channel_for, serve_service

        for describe, thunk in (
            ("channel", lambda: channel_for(Address(port=1, protocol="grpc"))),
            ("server", lambda: serve_service(AgentService(), port=1, protocol="grpc")),
        ):
            try:
                thunk()
            except TransportUnavailable as e:
                message = str(e)
                assert "grpc" in message, message
                assert "uv sync --extra grpc" in message, message
            else:
                raise AssertionError(f"{describe}: expected TransportUnavailable")
        print("OK")
        """,
        block_grpc=True,
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
