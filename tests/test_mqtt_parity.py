"""Parity over MQTT.

MQTT is the third transport and the first broker-mediated one, so this suite
answers the question the transport layer exists to make answerable: does swapping
the protocol change anything observable about the negotiation? It must not.

Scenario construction, config generation and negotiation driving are shared with
the gRPC suite (``tests/transport_harness.py``) — the *only* per-transport
difference here is how the three services are started, which is the point.

Parity is claimed for ``n_steps``-bounded negotiations exactly as for gRPC: the
envelope is step-bounded, so the extra broker hop cannot change the outcome.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from negmasd.agent import AgentService
from negmasd.runner import RunnerService
from negmasd.transport import channel_for

from transport_harness import (
    N_STEPS,
    addr,
    drive_negotiation,
    free_port,
    make_ufuns,
    plain,
    run_negmas,
    wait_ready,
    write_configs,
)

pytest.importorskip("paho.mqtt.client", reason="needs the optional mqtt extra")

pytestmark = pytest.mark.mqtt

PROTOCOL = "mqtt"


def _serve_roles(ports: dict) -> tuple[list, list]:
    """Start a runner and one agent per port, each an independent service."""
    from negmasd.transport.mqtt import MqttServerHandle

    services = [RunnerService(), *(AgentService() for _ in ports["agents"])]
    handles = []
    for service, port in zip(services, [ports["runner"], *ports["agents"]]):
        handle = MqttServerHandle(service, host="127.0.0.1", port=port)
        handle.start()
        handles.append(handle)
    return services, handles


def _ports() -> dict:
    # Under MQTT these are topic identifiers rather than TCP endpoints, but they
    # still have to be unique, so they are allocated the same way.
    return {"runner": free_port(), "agents": [free_port(), free_port()]}


# --------------------------------------------------------------------------- #
# full-stack parity
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["centralized", "distributed"])
@pytest.mark.parametrize("scenario", ["deal", "timeout"])
def test_full_stack_parity_over_mqtt(tmp_path, mqtt_broker, mode, scenario):
    ufuns = make_ufuns(scenario)
    ref = run_negmas(ufuns)
    if scenario == "timeout":
        assert ref["agreement"] is None and ref["timedout"], ref

    ports = _ports()
    _, handles = _serve_roles(ports)
    try:
        cfgs = write_configs(tmp_path, ufuns, mode, ports, PROTOCOL)
        got = drive_negotiation(ports, cfgs, PROTOCOL)
        assert got == ref, f"mqtt/{mode}/{scenario}: {got} != negmas {ref}"
    finally:
        for handle in handles:
            handle.stop()


def test_an_http_config_becomes_an_mqtt_config_by_one_field(tmp_path, mqtt_broker):
    """The addressing design's central claim: only `protocol:` differs.

    The configs here are produced by the very same helper the HTTP and gRPC suites
    use, with nothing but the protocol changed.
    """
    ufuns = make_ufuns("deal")
    ports = _ports()
    http_cfgs = write_configs(tmp_path / "http", ufuns, "distributed", ports, "http")
    mqtt_cfgs = write_configs(tmp_path / "mqtt", ufuns, "distributed", ports, PROTOCOL)

    import yaml

    for http_path, mqtt_path in zip(http_cfgs["agents"], mqtt_cfgs["agents"]):
        http_cfg = yaml.safe_load(http_path.read_text())
        mqtt_cfg = yaml.safe_load(mqtt_path.read_text())
        differing = {
            k for k in http_cfg if http_cfg[k] != mqtt_cfg.get(k)
        } | {k for k in mqtt_cfg if k not in http_cfg}
        assert differing == {"protocol", "runner_protocol", "negotiations"}, differing
        # ...and inside `negotiations` only the partners' protocol differs.
        http_neg = http_cfg["negotiations"]["parity"]
        mqtt_neg = mqtt_cfg["negotiations"]["parity"]
        assert {k for k in http_neg if http_neg[k] != mqtt_neg.get(k)} == {"partners"}
        for a, b in zip(http_neg["partners"], mqtt_neg["partners"]):
            assert {k for k in a if a[k] != b.get(k)} == {"protocol"}


# --------------------------------------------------------------------------- #
# routing isolation: the failure mode the derived node id exists to avoid
# --------------------------------------------------------------------------- #
def test_identically_named_services_do_not_steal_each_other_s_calls(
    tmp_path, mqtt_broker
):
    """Node ids come from host:port, not from the service name.

    Partner *names* are unique only within a negotiation, so deriving the topic
    from a name would let two same-named services on one broker consume each
    other's requests — silently, since MQTT has no "address already in use".
    """
    from negmasd.transport.mqtt import MqttServerHandle

    ufuns = make_ufuns("deal")
    ports = _ports()
    cfgs = write_configs(tmp_path, ufuns, "distributed", ports, PROTOCOL)
    # Both agent configs are named "P0" on purpose.
    import yaml

    same_name = []
    for i, path in enumerate(cfgs["agents"]):
        cfg = yaml.safe_load(path.read_text())
        cfg["name"] = "P0"
        out = tmp_path / f"same{i}.yml"
        out.write_text(yaml.safe_dump(cfg))
        same_name.append(out)

    handles = []
    try:
        for port in ports["agents"]:
            handle = MqttServerHandle(AgentService(), host="127.0.0.1", port=port)
            handle.start()
            handles.append(handle)
        assert handles[0].node != handles[1].node, "distinct services shared a topic"

        first, second = (channel_for(addr(p, PROTOCOL)) for p in ports["agents"])
        # Configure only the first one.
        resp = first.call(
            "load-config", {"path": str(same_name[0])}, http_method="get"
        )
        assert resp["status"] == "success", resp

        assert first.call("peak", {}, http_method="get")["config"] is not None
        assert second.call("peak", {}, http_method="get")["config"] is None
    finally:
        for handle in handles:
            handle.stop()


def test_mqtt_accepts_methods_spelled_as_paths(mqtt_broker):
    """A method may be named ("root") or spelled as its route ("/", "/peak")."""
    from negmasd.transport.mqtt import MqttServerHandle

    port = free_port()
    handle = MqttServerHandle(AgentService(), host="127.0.0.1", port=port)
    handle.start()
    try:
        channel = channel_for(addr(port, PROTOCOL))
        for spelling in ("root", "/"):
            assert channel.call(spelling, {}, http_method="get") == {
                "message": "Agent Service"
            }, spelling
        for spelling in ("peak", "/peak"):
            assert "config" in channel.call(spelling, {}, http_method="get"), spelling
    finally:
        handle.stop()


# --------------------------------------------------------------------------- #
# the ufun in its own process, over MQTT
# --------------------------------------------------------------------------- #
def test_remote_ufun_over_mqtt_exact_parity(tmp_path, mqtt_broker):
    """Negotiators run locally but each ufun lives behind an MQTT ufun service."""
    import random

    import yaml
    from negmas.gb.negotiators.timebased import AspirationNegotiator
    from negmas.sao import SAOMechanism
    from negmas.serialization import serialize

    from negmasd.transport import Address
    from negmasd.transport.mqtt import MqttServerHandle
    from negmasd.ufun_service import UfunService
    from negmasd.ufuns import RemoteUtilityFunction
    from transport_harness import SEED_RUN

    ufuns = make_ufuns("deal")
    ref = run_negmas(ufuns)

    handles, remotes = [], []
    try:
        for i, u in enumerate(ufuns):
            cfg = tmp_path / f"ufun{i}.yml"
            cfg.write_text(
                yaml.safe_dump(
                    {"default": plain(serialize(u, python_class_identifier="type"))}
                )
            )
            port = free_port()
            handle = MqttServerHandle(UfunService(), host="127.0.0.1", port=port)
            handle.start()
            handles.append(handle)
            address = Address(host="127.0.0.1", port=port, protocol=PROTOCOL)
            resp = channel_for(address).call(
                "load-config", {"path": str(cfg)}, http_method="get"
            )
            assert resp["status"] == "success", resp
            remotes.append(
                RemoteUtilityFunction(outcome_space=u.outcome_space, address=address)
            )

        # reserved_value is read directly by negotiators, so it must survive /meta.
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
        assert got == ref, f"remote-ufun over mqtt: {got} != negmas {ref}"
    finally:
        for handle in handles:
            handle.stop()


# --------------------------------------------------------------------------- #
# the real deployment path: the CLI, in real OS processes
# --------------------------------------------------------------------------- #
@pytest.mark.slow
@pytest.mark.parametrize("mode", ["centralized", "distributed"])
def test_full_stack_parity_over_mqtt_subprocesses(tmp_path, mqtt_broker, mode):
    """`negmasd <role> --protocol mqtt --broker …` in its own OS process, so the
    CLI, the lazy transport resolution and the server all run as a user runs them."""
    ufuns = make_ufuns("deal")
    ref = run_negmas(ufuns)

    ports = _ports()
    cfgs = write_configs(tmp_path, ufuns, mode, ports, PROTOCOL)
    broker = f"{mqtt_broker.host}:{mqtt_broker.port}"
    exe = Path(sys.executable).parent / "negmasd"

    common = ["--protocol", PROTOCOL, "--broker", broker, "--host", "127.0.0.1"]
    procs = [
        subprocess.Popen(
            [str(exe), "runner", "--port", str(ports["runner"]), *common]
        )
    ]
    for port in ports["agents"]:
        procs.append(subprocess.Popen([str(exe), "agent", str(port), *common]))
    try:
        for port in [ports["runner"], *ports["agents"]]:
            wait_ready(port, PROTOCOL)
        got = drive_negotiation(ports, cfgs, PROTOCOL)
        assert got == ref, f"mqtt subprocess/{mode}: {got} != negmas {ref}"
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()


@pytest.mark.parametrize("style", ["base_url", "host_port_protocol"])
def test_remote_ufun_is_addressable_from_config_over_mqtt(tmp_path, mqtt_broker, style):
    """The two documented ways to point a config-declared ufun at an MQTT service.

    `RemoteUtilityFunction` is built by `MakableModel.make_object`, i.e. straight
    from YAML, so both spellings documented in `docs/configuration.md` have to
    survive that path — an `mqtt://` scheme in `base_url`, or explicit
    host/port/protocol.
    """
    import yaml
    from negmas.serialization import serialize

    from negmasd.models import OutcomeSpaceModel, UtilityFunctionModel
    from negmasd.transport.mqtt import MqttServerHandle
    from negmasd.ufun_service import UfunService

    u = make_ufuns("deal")[0]
    cfg = tmp_path / "ufuns.yml"
    cfg.write_text(
        yaml.safe_dump({"default": plain(serialize(u, python_class_identifier="type"))})
    )
    os_model = OutcomeSpaceModel(
        **plain(serialize(u.outcome_space, python_class_identifier="type"))
    )

    port = free_port()
    handle = MqttServerHandle(UfunService(), host="127.0.0.1", port=port)
    handle.start()
    try:
        resp = channel_for(addr(port, PROTOCOL)).call(
            "load-config", {"path": str(cfg)}, http_method="get"
        )
        assert resp["status"] == "success", resp

        declared = {"type": "negmasd.ufuns.RemoteUtilityFunction", "ufun_id": "default"}
        if style == "base_url":
            declared["base_url"] = f"mqtt://127.0.0.1:{port}"
        else:
            declared |= {"host": "127.0.0.1", "port": port, "protocol": PROTOCOL}

        outcome_space = os_model.make_object()
        remote = UtilityFunctionModel(**declared).make_object(
            outcome_space=outcome_space
        )
        assert remote is not None
        for offer in list(outcome_space.enumerate_or_sample())[:5]:
            assert float(remote(offer)) == pytest.approx(float(u(offer)))
    finally:
        handle.stop()
