"""Full-stack parity over real HTTP across separate processes.

The in-process suite (``test_negmas_parity``) proves the codec + proxy + agent
logic are exact, but elides the network. This test launches the runner and the
agents as *real* ``uvicorn`` subprocesses and drives a negotiation end-to-end
over HTTP — the same path a cross-machine / cross-Python-version deployment
takes — then asserts exact parity with negmas-proper.

Marked ``slow``: it starts processes and polls. Run a single mode with::

    pytest tests/test_subprocess_parity.py -k distributed -s
"""

from __future__ import annotations

import json
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml

from negmas.gb.negotiators.timebased import AspirationNegotiator
from negmas.preferences.generators import generate_multi_issue_ufuns
from negmas.sao import SAOMechanism
from negmas.serialization import serialize

SEED_RUN = 0xC0FFEE
ASPIRATION = "negmas.gb.negotiators.timebased.AspirationNegotiator"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _plain(obj):
    """Coerce a serialized (possibly numpy-laden) structure to plain JSON types."""
    return json.loads(json.dumps(obj, default=float))


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


def _wait_ready(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/", timeout=1.0).status_code == 200:
                return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"service {base_url} did not become ready")


def _launch(module_app: str, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            module_app,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
    )


def _write_configs(tmp: Path, ufuns, n_steps: int, mode: str, ports: dict) -> dict:
    os_ser = _plain(serialize(ufuns[0].outcome_space, python_class_identifier="type"))
    mechanism = {"type": "negmas.sao.mechanism.SAOMechanism", "n_steps": n_steps}
    partners = [
        {"name": f"P{i}", "host": "127.0.0.1", "port": ports["agents"][i]}
        for i in range(len(ufuns))
    ]
    runner_cfg = {"name": "runner", "port": ports["runner"], "keep_trace": True}
    runner_path = tmp / "runner.yml"
    runner_path.write_text(yaml.safe_dump(runner_cfg))

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
            "runner_host": "127.0.0.1",
            "runner_port": ports["runner"],
            "negotiations": {"parity": negotiation},
        }
        p = tmp / f"agent{i}.yml"
        p.write_text(yaml.safe_dump(agent_cfg))
        agent_paths.append(p)
    return {"runner": runner_path, "agents": agent_paths}


@pytest.mark.slow
@pytest.mark.parametrize("mode", ["centralized", "distributed"])
@pytest.mark.parametrize("scenario", ["deal", "timeout"])
def test_full_stack_parity_over_http(tmp_path, mode, scenario):
    random.seed(7)
    ufuns = generate_multi_issue_ufuns(n_issues=2, n_values=5, n_ufuns=2, numeric=False)
    if scenario == "timeout":
        # No mutually acceptable outcome -> must time out with no agreement.
        for u in ufuns:
            u.reserved_value = 0.999
    ref = _run_negmas(ufuns, n_steps=25)
    if scenario == "timeout":
        assert ref["agreement"] is None and ref["timedout"], ref

    ports = {"runner": _free_port(), "agents": [_free_port(), _free_port()]}
    cfgs = _write_configs(tmp_path, ufuns, 25, mode, ports)

    procs = [_launch("negmasd.runner:app", ports["runner"])]
    for ap in ports["agents"]:
        procs.append(_launch("negmasd.agent:app", ap))
    try:
        runner_url = f"http://127.0.0.1:{ports['runner']}"
        agent_urls = [f"http://127.0.0.1:{p}" for p in ports["agents"]]
        _wait_ready(runner_url)
        for u in agent_urls:
            _wait_ready(u)

        # Load configs.
        r = httpx.get(
            f"{runner_url}/load-config", params={"path": str(cfgs["runner"])}, timeout=10
        )
        assert r.json()["status"] == "success", r.json()
        for url, path in zip(agent_urls, cfgs["agents"]):
            r = httpx.get(f"{url}/load-config", params={"path": str(path)}, timeout=10)
            assert r.json()["status"] == "success", r.json()

        # Initiator (P0) starts the negotiation.
        r = httpx.get(
            f"{agent_urls[0]}/start-negotiation", params={"neg": "parity"}, timeout=30
        )
        assert r.json()["status"] == "success", r.json()

        # Poll the runner for the stored result.
        result = None
        deadline = time.time() + 60
        while time.time() < deadline:
            sessions = httpx.get(f"{runner_url}/peak", timeout=10).json()["sessions"]
            if sessions:
                result = next(iter(sessions.values()))
                break
            time.sleep(0.2)
        assert result is not None, "runner produced no result"

        got = dict(
            agreement=None if result["agreement"] is None else list(result["agreement"]),
            step=int(result["final_state"]["step"]),
            timedout=bool(result["final_state"]["timedout"]),
        )
        assert got == ref, f"{mode}: full-stack {got} != negmas {ref}"
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
