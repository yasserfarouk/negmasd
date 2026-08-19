"""Shared scaffolding for the per-transport full-stack parity suites.

Each transport (`test_grpc_parity`, `test_mqtt_parity`) drives the *same*
scenario end to end and asserts the same negmas-proper reference, so the scenario
construction, config generation and negotiation driving live here once. That the
only per-transport difference is how services are started is itself the point
being tested: the transport is not supposed to change anything else.
"""

from __future__ import annotations

import json
import random
import socket
import time
from pathlib import Path

import yaml

from negmas.gb.negotiators.timebased import AspirationNegotiator
from negmas.preferences.generators import generate_multi_issue_ufuns
from negmas.sao import SAOMechanism
from negmas.serialization import serialize

from negmasd.transport import Address, channel_for

SEED_RUN = 0xC0FFEE
ASPIRATION = "negmas.gb.negotiators.timebased.AspirationNegotiator"
N_STEPS = 25

__all__ = [
    "ASPIRATION",
    "N_STEPS",
    "SEED_RUN",
    "addr",
    "drive_negotiation",
    "free_port",
    "make_ufuns",
    "plain",
    "run_negmas",
    "write_configs",
]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def plain(obj):
    """Coerce a serialized (possibly numpy-laden) structure to plain JSON types."""
    return json.loads(json.dumps(obj, default=float))


def make_ufuns(scenario: str = "deal"):
    random.seed(7)
    ufuns = generate_multi_issue_ufuns(n_issues=2, n_values=5, n_ufuns=2, numeric=False)
    if scenario == "timeout":
        # No mutually acceptable outcome -> must time out with no agreement.
        for u in ufuns:
            u.reserved_value = 0.999
    return ufuns


def run_negmas(ufuns, n_steps: int = N_STEPS) -> dict:
    """The reference: plain negmas, one process."""
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


def write_configs(
    tmp: Path,
    ufuns,
    mode: str,
    ports: dict,
    protocol: str,
    n_steps: int = N_STEPS,
) -> dict:
    """Generate an app's configs for one protocol.

    Only ``protocol`` differs between transports — the point of the addressing
    design. Under MQTT the host/port pairs are identifiers rather than TCP
    endpoints, but nothing here needs to know that.
    """
    tmp.mkdir(parents=True, exist_ok=True)
    os_ser = plain(serialize(ufuns[0].outcome_space, python_class_identifier="type"))
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
            "ufun": plain(serialize(u, python_class_identifier="type")),
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
        path = tmp / f"agent{i}.yml"
        path.write_text(yaml.safe_dump(agent_cfg))
        agent_paths.append(path)
    return {"runner": runner_path, "agents": agent_paths}


def addr(port: int, protocol: str) -> Address:
    return Address(host="127.0.0.1", port=port, protocol=protocol)


def wait_ready(
    port: int,
    protocol: str,
    timeout: float = 60.0,
    probe_timeout: float = 2.0,
) -> None:
    """Block until the service addressed by ``port`` answers over ``protocol``.

    Each probe gets a *short* timeout and is retried. That matters for MQTT: a
    QoS-1 publish to a topic nobody has subscribed to yet is silently discarded by
    the broker, so a single long call made before the peer is up simply waits out
    its whole timeout. HTTP and gRPC fail fast on connect instead, which retried
    naturally; MQTT has to be retried explicitly.
    """
    channel = channel_for(addr(port, protocol))
    original, channel.timeout = channel.timeout, probe_timeout
    last: Exception | None = None
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if channel.call("root", {}, http_method="get"):
                    return
            except Exception as e:  # not up yet
                last = e
                time.sleep(0.2)
    finally:
        channel.timeout = original
    raise RuntimeError(f"{protocol} service on {port} never became ready: {last!r}")


def drive_negotiation(ports: dict, cfgs: dict, protocol: str) -> dict:
    """Load configs, start the negotiation, poll the runner for its result."""
    runner = channel_for(addr(ports["runner"], protocol))
    agents = [channel_for(addr(p, protocol)) for p in ports["agents"]]

    resp = runner.call("load-config", {"path": str(cfgs["runner"])}, http_method="get")
    assert resp["status"] == "success", resp
    for channel, path in zip(agents, cfgs["agents"]):
        resp = channel.call("load-config", {"path": str(path)}, http_method="get")
        assert resp["status"] == "success", resp

    # Initiator (P0) starts the negotiation.
    resp = agents[0].call("start-negotiation", {"neg": "parity"}, http_method="get")
    assert resp["status"] == "success", resp

    result = None
    deadline = time.time() + 90
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
