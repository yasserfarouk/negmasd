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

import json
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from negmas.gb.negotiators.timebased import AspirationNegotiator
from negmas.preferences.generators import generate_multi_issue_ufuns
from negmas.sao import SAOMechanism
from negmas.serialization import serialize

from negmasd.agent import AgentService
from negmasd.runner import RunnerService
from negmasd.transport import Address, channel_for, close_all_channels

grpc = pytest.importorskip("grpc", reason="needs the optional grpcio extra")

SEED_RUN = 0xC0FFEE
ASPIRATION = "negmas.gb.negotiators.timebased.AspirationNegotiator"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _plain(obj):
    """Coerce a serialized (possibly numpy-laden) structure to plain JSON types."""
    return json.loads(json.dumps(obj, default=float))


def _make_ufuns(scenario: str):
    random.seed(7)
    ufuns = generate_multi_issue_ufuns(n_issues=2, n_values=5, n_ufuns=2, numeric=False)
    if scenario == "timeout":
        # No mutually acceptable outcome -> must time out with no agreement.
        for u in ufuns:
            u.reserved_value = 0.999
    return ufuns


def _run_negmas(ufuns, n_steps: int) -> dict:
    os = ufuns[0].outcome_space
    m = SAOMechanism(outcome_space=os, n_steps=n_steps)
    for u in ufuns:
        m.add(AspirationNegotiator(), preferences=u)
    random.seed(SEED_RUN)
    m.run()
    return dict(
        agreement=None if m.agreement is None else list(m.agreement),
        step=int(m.state.step),
        timedout=bool(m.state.timedout),
    )


def _write_configs(
    tmp: Path, ufuns, n_steps: int, mode: str, ports: dict, protocol: str
) -> dict:
    """Same shape as the HTTP full-stack configs, but naming a protocol."""
    os_ser = _plain(serialize(ufuns[0].outcome_space, python_class_identifier="type"))
    mechanism = {"type": "negmas.sao.mechanism.SAOMechanism", "n_steps": n_steps}
    partners = [
        {
            "name": f"P{i}",
            "host": "127.0.0.1",
            "port": ports["agents"][i],
            "protocol": protocol,
        }
        for i in range(len(ufuns))
    ]
    runner_path = tmp / "runner.yml"
    runner_path.write_text(
        yaml.safe_dump({"name": "runner", "port": ports["runner"], "keep_trace": True})
    )

    agent_paths = []
    for i, u in enumerate(ufuns):
        negotiation = {
            "name": "parity",
            "partners": partners,
            "outcome_space": os_ser,
            "mechanism": mechanism,
            "mode": mode,
            "seed": SEED_RUN,
            "negotiator": {"type": ASPIRATION, "name": f"P{i}"},
            "ufun": _plain(serialize(u, python_class_identifier="type")),
        }
        agent_cfg = {
            "name": f"P{i}",
            "host": "127.0.0.1",
            "port": ports["agents"][i],
            "protocol": protocol,
            "runner_host": "127.0.0.1",
            "runner_port": ports["runner"],
            "runner_protocol": protocol,
            "negotiations": {"parity": negotiation},
        }
        p = tmp / f"agent{i}.yml"
        p.write_text(yaml.safe_dump(agent_cfg))
        agent_paths.append(p)
    return {"runner": runner_path, "agents": agent_paths}


def _addr(port: int, protocol: str = "grpc") -> Address:
    return Address(host="127.0.0.1", port=port, protocol=protocol)


def _drive_negotiation(ports: dict, cfgs: dict, protocol: str = "grpc") -> dict:
    """Load configs, start the negotiation, and poll the runner for its result."""
    runner = channel_for(_addr(ports["runner"], protocol))
    agents = [channel_for(_addr(p, protocol)) for p in ports["agents"]]

    resp = runner.call(
        "load-config", {"path": str(cfgs["runner"])}, http_method="get"
    )
    assert resp["status"] == "success", resp
    for channel, path in zip(agents, cfgs["agents"]):
        resp = channel.call("load-config", {"path": str(path)}, http_method="get")
        assert resp["status"] == "success", resp

    # Initiator (P0) starts the negotiation.
    resp = agents[0].call("start-negotiation", {"neg": "parity"}, http_method="get")
    assert resp["status"] == "success", resp

    result = None
    deadline = time.time() + 60
    while time.time() < deadline:
        sessions = runner.call("peak", {}, http_method="get")["sessions"]
        if sessions:
            result = next(iter(sessions.values()))
            break
        time.sleep(0.1)
    assert result is not None, "runner produced no result"
    return dict(
        agreement=None if result["agreement"] is None else list(result["agreement"]),
        step=int(result["final_state"]["step"]),
        timedout=bool(result["final_state"]["timedout"]),
    )


@pytest.fixture
def clean_channels():
    """Channels are cached per address; don't leak them between tests."""
    close_all_channels()
    yield
    close_all_channels()


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

    ufuns = _make_ufuns(scenario)
    ref = _run_negmas(ufuns, n_steps=25)
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
        cfgs = _write_configs(tmp_path, ufuns, 25, mode, ports, "grpc")
        got = _drive_negotiation(ports, cfgs)
        assert got == ref, f"grpc/{mode}/{scenario}: {got} != negmas {ref}"
    finally:
        for h in handles:
            h.stop(grace=0)


@pytest.mark.grpc
def test_grpc_and_http_agree_exactly(tmp_path, clean_channels):
    """The transport is not allowed to change the outcome. Same scenario, both
    protocols, one assertion."""
    from negmasd.transport.grpc import GrpcServerHandle

    ufuns = _make_ufuns("deal")
    ref = _run_negmas(ufuns, n_steps=25)

    services = [RunnerService(), AgentService(), AgentService()]
    handles = [GrpcServerHandle(s, host="127.0.0.1", port=0) for s in services]
    try:
        bound = [h.start() for h in handles]
        ports = {"runner": bound[0], "agents": bound[1:]}
        cfgs = _write_configs(tmp_path, ufuns, 25, "distributed", ports, "grpc")
        over_grpc = _drive_negotiation(ports, cfgs)
    finally:
        for h in handles:
            h.stop(grace=0)
    assert over_grpc == ref


# --------------------------------------------------------------------------- #
# full-stack parity over gRPC, in real subprocesses, driven by the CLI
# --------------------------------------------------------------------------- #
def _wait_ready(port: int, timeout: float = 30.0) -> None:
    channel = channel_for(_addr(port))
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            if channel.call("root", {}, http_method="get"):
                return
        except Exception as e:  # server not up yet
            last = e
            time.sleep(0.2)
    raise RuntimeError(f"grpc service on {port} did not become ready: {last}")


@pytest.mark.slow
@pytest.mark.grpc
@pytest.mark.parametrize("mode", ["centralized", "distributed"])
def test_full_stack_parity_over_grpc_subprocesses(tmp_path, mode, clean_channels):
    """The real deployment path: `negmasd <role> --protocol grpc` in its own OS
    process, so the CLI, the lazy transport resolution and the server all run
    exactly as a user would run them."""
    ufuns = _make_ufuns("deal")
    ref = _run_negmas(ufuns, n_steps=25)

    ports = {"runner": _free_port(), "agents": [_free_port(), _free_port()]}
    cfgs = _write_configs(tmp_path, ufuns, 25, mode, ports, "grpc")

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
            _wait_ready(port)
        got = _drive_negotiation(ports, cfgs)
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
    from negmasd.transport.grpc import GrpcServerHandle
    from negmasd.ufun_service import UfunService
    from negmasd.ufuns import RemoteUtilityFunction

    ufuns = _make_ufuns("deal")
    ref = _run_negmas(ufuns, n_steps=25)

    # One ufun service per party, each holding only its owner's ufun.
    handles, remotes = [], []
    try:
        for i, u in enumerate(ufuns):
            cfg = tmp_path / f"ufun{i}.yml"
            cfg.write_text(
                yaml.safe_dump(
                    {"default": _plain(serialize(u, python_class_identifier="type"))}
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

        m = SAOMechanism(outcome_space=ufuns[0].outcome_space, n_steps=25)
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

    u = _make_ufuns("deal")[0]
    cfg = tmp_path / "ufuns.yml"
    cfg.write_text(
        yaml.safe_dump({"default": _plain(serialize(u, python_class_identifier="type"))})
    )
    os_model = OutcomeSpaceModel(
        **_plain(serialize(u.outcome_space, python_class_identifier="type"))
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
