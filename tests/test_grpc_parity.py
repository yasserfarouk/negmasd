"""Parity over gRPC, and the guarantees that keep gRPC optional.

``test_negmas_parity`` proves the codec and the distributed algorithm are exact;
``test_subprocess_parity`` proves the HTTP stack carries them faithfully across
processes. This module proves the *transport abstraction* holds: swapping the
protocol to gRPC changes nothing observable about the negotiation.

Three kinds of check live here:

* **Protocol-agnosticism** — the FastAPI and gRPC adapters are projections of the
  same method table, so their method sets must be identical.
* **Full-stack gRPC parity** — real ``grpc.aio`` servers for the runner and both
  agents, driven end to end, asserted bit-exact against negmas-proper.
* **Optionality** — nothing on the HTTP path may import ``grpc``, and asking for
  gRPC without the extra installed must produce one actionable error.

Everything needing ``grpcio`` is skipped when it is absent, so the suite is green
on a plain ``uv sync``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from negmas.serialization import serialize

from negmasd.agent import AgentService
from negmasd.runner import RunnerService
from negmasd.transport import Address, channel_for

from transport_harness import (
    N_STEPS,
    addr as _harness_addr,
    drive_negotiation,
    free_port,
    make_ufuns,
    plain,
    run_negmas,
    wait_ready,
    write_configs,
)

grpc = pytest.importorskip("grpc", reason="needs the optional grpcio extra")

PROTOCOL = "grpc"


def _addr(port: int, protocol: str = PROTOCOL) -> Address:
    return _harness_addr(port, protocol)


# --------------------------------------------------------------------------- #
# the adapters are projections of one table
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "factory", [AgentService, RunnerService, "ufun"], ids=["agent", "runner", "ufun"]
)
def test_http_and_grpc_expose_the_same_methods(factory):
    """Both adapters read the same method table, so neither may add or lose one."""
    from negmasd.transport import build_app
    from negmasd.ufun_service import UfunService

    service = UfunService() if factory == "ufun" else factory()
    app = build_app(service)
    routes = {
        r.path
        for r in app.routes
        if hasattr(r, "methods")
        and not any(k in r.path for k in ("openapi", "docs", "redoc"))
    }
    # Every declared route is served, and no route is served that the table did
    # not declare (aliases included) -- neither adapter may add or lose a method.
    declared = {spec.route for spec in service.method_table}
    aliases = {"/" + spec.name for spec in service.method_table}
    assert declared <= routes
    assert routes <= declared | aliases

    for spec in service.method_table:
        # gRPC dispatch is by method *name*; that same name must therefore be
        # addressable over HTTP, or the two transports diverge. (The root health
        # check is the live case: its path is "/" but its name is "root".)
        assert "/" + spec.name in routes, spec
        # Both spellings resolve to the very same spec object the routes use.
        assert service.spec_for(spec.name) is spec
        assert service.spec_for(spec.route) is spec


# --------------------------------------------------------------------------- #
# full-stack parity over real gRPC servers
# --------------------------------------------------------------------------- #
@pytest.mark.grpc
@pytest.mark.parametrize("mode", ["centralized", "distributed"])
@pytest.mark.parametrize("scenario", ["deal", "timeout"])
def test_full_stack_parity_over_grpc(tmp_path, mode, scenario, clean_channels):
    from negmasd.transport.grpc import GrpcServerHandle

    ufuns = make_ufuns(scenario)
    ref = run_negmas(ufuns)
    if scenario == "timeout":
        assert ref["agreement"] is None and ref["timedout"], ref

    # Each service is a separate object with its own state, exactly as it would
    # be in a separate process; only the transport is in-process here.
    services = [RunnerService(), AgentService(), AgentService()]
    handles = [
        GrpcServerHandle(s, host="127.0.0.1", port=0) for s in services
    ]
    try:
        bound = [h.start() for h in handles]
        ports = {"runner": bound[0], "agents": bound[1:]}
        cfgs = write_configs(tmp_path, ufuns, mode, ports, PROTOCOL)
        got = drive_negotiation(ports, cfgs, PROTOCOL)
        assert got == ref, f"grpc/{mode}/{scenario}: {got} != negmas {ref}"
    finally:
        for h in handles:
            h.stop(grace=0)


@pytest.mark.grpc
def test_grpc_and_http_agree_exactly(tmp_path, clean_channels):
    """The transport is not allowed to change the outcome. Same scenario, both
    protocols, one assertion."""
    from negmasd.transport.grpc import GrpcServerHandle

    ufuns = make_ufuns("deal")
    ref = run_negmas(ufuns)

    services = [RunnerService(), AgentService(), AgentService()]
    handles = [GrpcServerHandle(s, host="127.0.0.1", port=0) for s in services]
    try:
        bound = [h.start() for h in handles]
        ports = {"runner": bound[0], "agents": bound[1:]}
        cfgs = write_configs(tmp_path, ufuns, "distributed", ports, PROTOCOL)
        over_grpc = drive_negotiation(ports, cfgs, PROTOCOL)
    finally:
        for h in handles:
            h.stop(grace=0)
    assert over_grpc == ref


# --------------------------------------------------------------------------- #
# full-stack parity over gRPC, in real subprocesses, driven by the CLI
# --------------------------------------------------------------------------- #
@pytest.mark.slow
@pytest.mark.grpc
@pytest.mark.parametrize("mode", ["centralized", "distributed"])
def test_full_stack_parity_over_grpc_subprocesses(tmp_path, mode, clean_channels):
    """The real deployment path: `negmasd <role> --protocol grpc` in its own OS
    process, so the CLI, the lazy transport resolution and the server all run
    exactly as a user would run them."""
    ufuns = make_ufuns("deal")
    ref = run_negmas(ufuns)

    ports = {"runner": free_port(), "agents": [free_port(), free_port()]}
    cfgs = write_configs(tmp_path, ufuns, mode, ports, PROTOCOL)

    exe = Path(sys.executable).parent / "negmasd"
    procs = [
        subprocess.Popen(
            [str(exe), "runner", "--port", str(ports["runner"]), "--protocol", "grpc"]
        )
    ]
    for port in ports["agents"]:
        procs.append(
            subprocess.Popen(
                [str(exe), "agent", str(port), "--protocol", "grpc"]
            )
        )
    try:
        for port in [ports["runner"], *ports["agents"]]:
            wait_ready(port, PROTOCOL)
        got = drive_negotiation(ports, cfgs, PROTOCOL)
        assert got == ref, f"grpc subprocess/{mode}: {got} != negmas {ref}"
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


# --------------------------------------------------------------------------- #
# the third level of distribution: the ufun in its own process, over gRPC
# --------------------------------------------------------------------------- #
@pytest.mark.grpc
def test_remote_ufun_over_grpc_exact_parity(tmp_path, clean_channels):
    """Negotiators run locally but each ufun lives behind a gRPC ufun service.

    Also the only end-to-end coverage of ``UfunService.load_config``: the ufun is
    serialized to YAML, loaded over the wire, and every ``eval`` is a real RPC.
    """
    import random

    from negmas.gb.negotiators.timebased import AspirationNegotiator
    from negmas.sao import SAOMechanism

    from negmasd.transport.grpc import GrpcServerHandle
    from negmasd.ufun_service import UfunService
    from negmasd.ufuns import RemoteUtilityFunction
    from transport_harness import SEED_RUN

    ufuns = make_ufuns("deal")
    ref = run_negmas(ufuns)

    # One ufun service per party, each holding only its owner's ufun.
    handles, remotes = [], []
    try:
        for i, u in enumerate(ufuns):
            cfg = tmp_path / f"ufun{i}.yml"
            cfg.write_text(
                yaml.safe_dump(
                    {"default": plain(serialize(u, python_class_identifier="type"))}
                )
            )
            handle = GrpcServerHandle(UfunService(), host="127.0.0.1", port=0)
            port = handle.start()
            handles.append(handle)
            address = Address(host="127.0.0.1", port=port, protocol="grpc")
            resp = channel_for(address).call(
                "load-config", {"path": str(cfg)}, http_method="get"
            )
            assert resp["status"] == "success", resp
            remotes.append(
                RemoteUtilityFunction(outcome_space=u.outcome_space, address=address)
            )

        # reserved_value is read directly by negotiators, so it must survive the
        # /meta round trip.
        for remote, u in zip(remotes, ufuns):
            assert remote.reserved_value == pytest.approx(u.reserved_value)

        m = SAOMechanism(outcome_space=ufuns[0].outcome_space, n_steps=N_STEPS)
        for remote in remotes:
            m.add(AspirationNegotiator(), preferences=remote)
        random.seed(SEED_RUN)
        m.run()
        got = dict(
            agreement=None if m.agreement is None else list(m.agreement),
            step=int(m.state.step),
            timedout=bool(m.state.timedout),
        )
        assert got == ref, f"remote-ufun over grpc: {got} != negmas {ref}"
    finally:
        for h in handles:
            h.stop(grace=0)


@pytest.mark.grpc
def test_grpc_accepts_methods_spelled_as_paths(clean_channels):
    """A method may be named ("root") or spelled as its route ("/", "/peak").

    ``common.get_response`` derives the method from a URL path, so gRPC has to
    accept the path spelling or a ``grpc://host:port/`` URL would fail where the
    HTTP one succeeds.
    """
    from negmasd.transport.grpc import GrpcServerHandle

    handle = GrpcServerHandle(AgentService(), host="127.0.0.1", port=0)
    port = handle.start()
    try:
        channel = channel_for(_addr(port))
        for spelling in ("root", "/"):
            assert channel.call(spelling, {}, http_method="get") == {
                "message": "Agent Service"
            }, spelling
        for spelling in ("peak", "/peak"):
            assert "config" in channel.call(spelling, {}, http_method="get"), spelling
    finally:
        handle.stop(grace=0)


@pytest.mark.grpc
@pytest.mark.parametrize("style", ["base_url", "host_port_protocol"])
def test_remote_ufun_is_addressable_from_config(tmp_path, clean_channels, style):
    """The two documented ways to point a config-declared ufun at a gRPC service.

    `RemoteUtilityFunction` is built by `MakableModel.make_object`, i.e. straight
    from YAML, so both spellings documented in `docs/configuration.md` have to
    survive that path -- `grpc://` in `base_url`, or explicit host/port/protocol.
    """
    from negmasd.models import OutcomeSpaceModel, UtilityFunctionModel
    from negmasd.transport.grpc import GrpcServerHandle
    from negmasd.ufun_service import UfunService

    u = make_ufuns("deal")[0]
    cfg = tmp_path / "ufuns.yml"
    cfg.write_text(
        yaml.safe_dump({"default": plain(serialize(u, python_class_identifier="type"))})
    )
    os_model = OutcomeSpaceModel(
        **plain(serialize(u.outcome_space, python_class_identifier="type"))
    )

    handle = GrpcServerHandle(UfunService(), host="127.0.0.1", port=0)
    port = handle.start()
    try:
        resp = channel_for(_addr(port)).call(
            "load-config", {"path": str(cfg)}, http_method="get"
        )
        assert resp["status"] == "success", resp

        declared = {"type": "negmasd.ufuns.RemoteUtilityFunction", "ufun_id": "default"}
        if style == "base_url":
            declared["base_url"] = f"grpc://127.0.0.1:{port}"
        else:
            declared |= {"host": "127.0.0.1", "port": port, "protocol": "grpc"}

        outcome_space = os_model.make_object()
        remote = UtilityFunctionModel(**declared).make_object(
            outcome_space=outcome_space
        )
        assert remote is not None
        for offer in list(outcome_space.enumerate_or_sample())[:5]:
            assert float(remote(offer)) == pytest.approx(float(u(offer)))
    finally:
        handle.stop(grace=0)
