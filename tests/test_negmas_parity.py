"""Parity between negmas-proper and negmasd.

The whole point of negmasd is to run a negmas negotiation with its pieces split
across processes/machines. These tests run the *same* scenario under negmas
directly (the reference) and under negmasd, and assert the two reach the
**exact same** agreement, step and timeout flag.

Unlike the (deliberately relaxed) koshodojo parity suite — which reimplements
the SAO protocol and so can only match negmas approximately — negmasd reuses
negmas's own ``SAOMechanism`` and forwards only the per-round negotiator calls.
That makes exact parity achievable, and it is asserted here for both modes:

* ``centralized`` — the runner pulls each partner's strategy + ufun and runs
  the whole negotiation locally (trivially exact: it *is* negmas in-process,
  this checks config serialization fidelity).
* ``distributed`` — the runner runs the real mechanism but every per-round
  ``negotiator(state)`` is forwarded over the JSON wire to the partner's own
  process, which holds the real strategy + ufun.

Determinism note: only the *runner's* mechanism draws from the global RNG (e.g.
random first-proposer). Agents running ``stochastic=False`` time-based
negotiators need no RNG sync — proven by parity holding even when the agent-side
RNG is deliberately poisoned. We pin the runner's RNG with ``seed`` so the
reference and negmasd start their run from the same state.
"""

from __future__ import annotations

import json
import random

import pytest

from negmas.gb.negotiators.timebased import AspirationNegotiator
from negmas.preferences.generators import generate_multi_issue_ufuns
from negmas.sao import SAOMechanism
from negmas.serialization import serialize

from negmasd.agent import build_negotiator
from negmasd.models import (
    MechanismModel,
    NegotiationInfoModel,
    NegotiationModel,
    NegotiatorModel,
    OutcomeSpaceModel,
    PartnerModel,
    PersonalNegotiationModel,
    UtilityFunctionModel,
)
from negmasd.negotiator import SAORemoteNegotiator
from negmasd.ufuns import RemoteUtilityFunction
from negmasd import runner as runner_mod
from negmasd.wire import decode_state, encode_response

ASPIRATION = "negmas.gb.negotiators.timebased.AspirationNegotiator"
SEED_RUN = 0xC0FFEE  # arbitrary fixed seed applied right before every run


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def gen_ufuns(seed: int, n_issues: int, n_values: int, n_ufuns: int, numeric: bool):
    random.seed(seed)
    return generate_multi_issue_ufuns(
        n_issues=n_issues, n_values=n_values, n_ufuns=n_ufuns, numeric=numeric
    )


def gen_ufuns_no_deal(seed, n_issues, n_values, n_ufuns=2):
    """Like ``gen_ufuns`` but with reservation values so high no deal exists,
    forcing a timeout (``agreement=None``, ``timedout=True``)."""
    ufuns = gen_ufuns(seed, n_issues, n_values, n_ufuns, numeric=False)
    for u in ufuns:
        u.reserved_value = 0.999
    return ufuns


def norm(agreement) -> tuple | None:
    return None if agreement is None else tuple(agreement)


def assert_consistent(res: dict) -> None:
    """No finished negotiation may be both agreed and timed out (the invariant
    the referenced koshodojo suite was written to protect)."""
    assert not (res["agreement"] is not None and res["timedout"]), res


def outcome_of(m: SAOMechanism) -> dict:
    return dict(
        agreement=norm(m.agreement),
        step=int(m.state.step),
        timedout=bool(m.state.timedout),
    )


def run_negmas(ufuns, n_steps: int) -> dict:
    """Reference: plain negmas, in one process."""
    os = ufuns[0].outcome_space
    m = SAOMechanism(outcome_space=os, n_steps=n_steps)
    for u in ufuns:
        m.add(AspirationNegotiator(), preferences=u)
    random.seed(SEED_RUN)
    m.run()
    return outcome_of(m)


def make_models(ufuns, n_steps: int, mode: str):
    """Build the public negotiation info + one personal model per partner.

    Every ufun and outcome space goes through negmas (de)serialization, so this
    also validates that the config wire faithfully reproduces them.
    """
    os = ufuns[0].outcome_space
    os_model = OutcomeSpaceModel(**serialize(os, python_class_identifier="type"))
    partners = [PartnerModel(name=f"P{i}", port=i + 1) for i in range(len(ufuns))]
    public = NegotiationModel(
        name="parity",
        partners=partners,
        outcome_space=os_model,
        mechanism=MechanismModel(type="negmas.sao.mechanism.SAOMechanism", n_steps=n_steps),
        mode=mode,
        seed=SEED_RUN,
    )
    personal = [
        PersonalNegotiationModel(
            name="parity",
            partners=partners,
            outcome_space=os_model,
            mechanism=public.mechanism,
            mode=mode,
            seed=SEED_RUN,
            negotiator=NegotiatorModel(type=ASPIRATION),
            ufun=UtilityFunctionModel(**serialize(u, python_class_identifier="type")),
        )
        for u in ufuns
    ]
    return public, personal


def run_negmasd_centralized(ufuns, n_steps: int) -> dict:
    """Exercise the runner's centralized assembly from serialized configs."""
    public, personal = make_models(ufuns, n_steps, "centralized")
    neg = NegotiationInfoModel(negid="parity", info=public)
    m = runner_mod.build_mechanism(neg)
    os = m.outcome_space
    for p in personal:
        ufun = p.ufun.make_object(outcome_space=os)
        negotiator = p.negotiator.make_object(ufun=ufun)
        m.add(negotiator, preferences=ufun)
    random.seed(SEED_RUN)
    m.run()
    return outcome_of(m)


def _agent_transport(live_negotiator):
    """A transport standing in for one agent's ``/respond`` endpoint.

    Goes through a full JSON round-trip in both directions, so the wire codec is
    exercised exactly as it would be over HTTP — only the network is elided.
    """

    def transport(path: str, payload: dict) -> dict:
        assert path == "/respond"
        wire = json.loads(json.dumps(payload))  # runner -> network -> agent
        state = decode_state(wire["state"])
        response = live_negotiator(state)
        return json.loads(json.dumps(encode_response(response)))  # agent -> runner

    return transport


def run_negmasd_distributed(ufuns, n_steps: int) -> dict:
    """Exercise the full distributed path (real components, JSON wire)."""
    public, personal = make_models(ufuns, n_steps, "distributed")
    neg = NegotiationInfoModel(negid="parity", info=public)
    m = runner_mod.build_mechanism(neg)
    # Each "agent" builds its real negotiator + ufun in (conceptually) its own
    # process; the runner only ever sees a no-ufun proxy.
    for i, p in enumerate(personal):
        agent_negotiator = build_negotiator(p)
        m.add(
            SAORemoteNegotiator(
                negid="parity",
                partner_name=f"P{i}",
                name=f"P{i}",
                transport=_agent_transport(agent_negotiator),
            )
        )
    random.seed(SEED_RUN)
    m.run()
    return outcome_of(m)


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
BILATERAL_SEEDS = list(range(30))


@pytest.mark.parametrize("seed", BILATERAL_SEEDS)
def test_bilateral_distributed_exact_parity(seed):
    ufuns = gen_ufuns(seed, n_issues=2, n_values=5, n_ufuns=2, numeric=False)
    ref = run_negmas(ufuns, n_steps=25)
    dist = run_negmasd_distributed(ufuns, n_steps=25)
    assert dist == ref, f"seed {seed}: distributed {dist} != negmas {ref}"


@pytest.mark.parametrize("seed", BILATERAL_SEEDS)
def test_bilateral_centralized_exact_parity(seed):
    ufuns = gen_ufuns(seed, n_issues=2, n_values=5, n_ufuns=2, numeric=False)
    ref = run_negmas(ufuns, n_steps=25)
    cen = run_negmasd_centralized(ufuns, n_steps=25)
    assert cen == ref, f"seed {seed}: centralized {cen} != negmas {ref}"


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("n_issues,n_values", [(1, 4), (3, 3)])
def test_bilateral_varied_spaces(seed, n_issues, n_values):
    ufuns = gen_ufuns(seed, n_issues=n_issues, n_values=n_values, n_ufuns=2, numeric=False)
    ref = run_negmas(ufuns, n_steps=20)
    dist = run_negmasd_distributed(ufuns, n_steps=20)
    assert dist == ref


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_multilateral_distributed_exact_parity(seed):
    ufuns = gen_ufuns(seed, n_issues=2, n_values=4, n_ufuns=3, numeric=False)
    ref = run_negmas(ufuns, n_steps=20)
    dist = run_negmasd_distributed(ufuns, n_steps=20)
    assert dist == ref


# --------------------------------------------------------------------------- #
# ufun in its own process: negotiators evaluate utilities via a remote ufun.
# --------------------------------------------------------------------------- #
def _ufun_service_transport(real_ufun):
    """Stand-in for a ufun service's /eval and /meta, with JSON round-trips."""

    def transport(path: str, payload: dict) -> dict:
        wire = json.loads(json.dumps(payload))
        if path == "/meta":
            return {"reserved_value": real_ufun.reserved_value}
        if path == "/eval":
            o = wire.get("outcome")
            value = real_ufun(None if o is None else tuple(o))
            return json.loads(
                json.dumps({"value": None if value is None else float(value)})
            )
        raise AssertionError(path)

    return transport


def run_negmasd_remote_ufun(ufuns, n_steps: int) -> dict:
    """Negotiators run locally but each ufun lives in its own (remote) process."""
    os = ufuns[0].outcome_space
    m = SAOMechanism(outcome_space=os, n_steps=n_steps)
    for u in ufuns:
        remote = RemoteUtilityFunction(
            outcome_space=os, transport=_ufun_service_transport(u)
        )
        m.add(AspirationNegotiator(), preferences=remote)
    random.seed(SEED_RUN)
    m.run()
    return outcome_of(m)


@pytest.mark.parametrize("seed", BILATERAL_SEEDS)
def test_bilateral_remote_ufun_exact_parity(seed):
    ufuns = gen_ufuns(seed, n_issues=2, n_values=5, n_ufuns=2, numeric=False)
    ref = run_negmas(ufuns, n_steps=25)
    got = run_negmasd_remote_ufun(ufuns, n_steps=25)
    assert got == ref, f"seed {seed}: remote-ufun {got} != negmas {ref}"


# --------------------------------------------------------------------------- #
# Timeout / no-agreement branch — the heart of the referenced koshodojo suite.
# A negotiation that cannot agree must reach exactly the same timed-out end
# state in negmasd as in negmas, and never be both agreed and timed out.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4, 5, 6, 7])
def test_bilateral_timeout_exact_parity(seed):
    ufuns = gen_ufuns_no_deal(seed, n_issues=2, n_values=5)
    ref = run_negmas(ufuns, n_steps=25)
    # Precondition: we are actually exercising the timeout branch.
    assert ref["agreement"] is None and ref["timedout"], ref
    cen = run_negmasd_centralized(gen_ufuns_no_deal(seed, 2, 5), n_steps=25)
    dist = run_negmasd_distributed(gen_ufuns_no_deal(seed, 2, 5), n_steps=25)
    assert cen == ref, f"seed {seed}: centralized {cen} != negmas {ref}"
    assert dist == ref, f"seed {seed}: distributed {dist} != negmas {ref}"
    for res in (ref, cen, dist):
        assert_consistent(res)


def test_agreement_is_consistent_across_all_seeds():
    """Across every agreeing scenario, the distributed end state is consistent
    (never agreement AND timedout) and matches negmas exactly."""
    for seed in BILATERAL_SEEDS:
        ufuns = gen_ufuns(seed, n_issues=2, n_values=5, n_ufuns=2, numeric=False)
        ref = run_negmas(ufuns, n_steps=25)
        dist = run_negmasd_distributed(ufuns, n_steps=25)
        assert dist == ref
        assert_consistent(dist)
