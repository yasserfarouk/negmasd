from typing import TypedDict

from yaml import safe_load
from fastapi import FastAPI
import asyncio
import random
from negmas import TraceElement
from rich import print
from random import shuffle

from negmas.mechanisms import Mechanism
from negmas.serialization import serialize
from negmasd.models import (
    NegResultsModel,
    NegStrategyModel,
    NegotiationInfoModel,
    NegotiationModel,
    ResponseModel,
)
from negmasd.negotiator import SAORemoteNegotiator
from negmasd.common import FAILURE, SUCCESS, get_response, RUNNER_DEFAULT_PORT

app = FastAPI()


class SessionResults(TypedDict):
    negid: str
    info: NegotiationModel
    final_state: dict
    agreement: tuple | None
    trace: list[TraceElement]


config = dict(keep_trace=True, port=RUNNER_DEFAULT_PORT, name="runner")

SESSIONS = dict()


class DParamsType(TypedDict):
    python_class_identifier: str
    remove_type_field: bool


def build_mechanism(neg: NegotiationInfoModel) -> Mechanism:
    os = neg.info.outcome_space.make_object()
    m = neg.info.mechanism.make_object(outcome_space=os)
    assert isinstance(m, Mechanism), (
        f"Mechanism type is not valid: {type(m)}\n{neg.info.mechanism}"
    )
    return m


async def add_centralized_negotiators(m: Mechanism, neg: NegotiationInfoModel) -> None:
    """Pull each partner's strategy + ufun and build them in the runner process."""
    os = m.outcome_space
    strategies: list[NegStrategyModel] = []
    for partner in neg.info.partners:
        strategy = await get_response(
            f"http://{partner.host}:{partner.port}/neg-strategy",
            dict(negid=neg.negid),
            response_type=NegStrategyModel,
            method="get",
        )
        if not isinstance(strategy, NegStrategyModel):
            raise ValueError(
                f"Partner {partner} did not return valid strategy info {strategy}"
            )
        strategies.append(strategy)
    if neg.info.random_order:
        shuffle(strategies)
    for s in strategies:
        ufun = s.ufun.make_object(outcome_space=os) if s.ufun is not None else None
        negotiator = (
            s.negotiator.make_object(ufun=ufun)
            if ufun is not None
            else s.negotiator.make_object()
        )
        m.add(negotiator, preferences=ufun)


async def add_distributed_negotiators(m: Mechanism, neg: NegotiationInfoModel) -> None:
    """Add one remote proxy per partner; each forwards rounds to its process.

    The partner's real strategy + ufun never leave its process. The proxy holds
    no ufun: we only need the per-round response over the wire.
    """
    partners = list(neg.info.partners)
    if neg.info.random_order:
        shuffle(partners)
    for partner in partners:
        # Ask the partner to build and hold its live negotiator.
        resp = await get_response(
            f"http://{partner.host}:{partner.port}/init-negotiation",
            neg,
            response_type=ResponseModel,
        )
        if isinstance(resp, ResponseModel) and resp.status != SUCCESS:
            raise ValueError(f"Partner {partner.name} failed to init: {resp.message}")
        m.add(
            SAORemoteNegotiator(
                base_url=f"http://{partner.host}:{partner.port}",
                negid=neg.negid,
                partner_name=partner.name,
                name=partner.name,
            )
        )


async def async_run(neg: NegotiationInfoModel) -> None:
    print(f"Running negotiation ({neg.info.mode} mode):")
    print(neg)
    m = build_mechanism(neg)
    if neg.info.mode == "distributed":
        await add_distributed_negotiators(m, neg)
    else:
        await add_centralized_negotiators(m, neg)

    if neg.info.seed is not None:
        # Bit-exact reproducibility (e.g. random first-proposer selection).
        random.seed(neg.info.seed)
    # The mechanism run loop is synchronous and, in distributed mode, makes
    # blocking HTTP calls per round; keep it off the event loop.
    await asyncio.to_thread(m.run)
    print(m.state)
    log_entry = dict(
        negid=neg.negid,
        info=neg.info,
        final_state=serialize(m.state, python_class_identifier="type"),
        agreement=m.agreement,
        trace=[],
    )
    if config["keep_trace"] and hasattr(m, "full_trace"):
        log_entry["trace"] = m.full_trace  # type: ignore
    SESSIONS[neg.negid] = log_entry
    for partner in neg.info.partners:
        print(
            f" Sending results to http://{partner.host}:{partner.port}/receive-results"
        )
        await get_response(
            f"http://{partner.host}:{partner.port}/receive-results",
            NegResultsModel(
                negid=neg.negid, agreement=m.agreement, state=serialize(m.state)
            ),
        )


@app.get("/")
async def root():
    return {"message": "Negotiation Runner"}


@app.get("/load-config")
async def load_config(path: str) -> ResponseModel:
    try:
        loaded = safe_load(open(path, "r"))
        for k, v in loaded.items():
            config[k] = v
        return ResponseModel(
            status="success", message=f"Configuration loaded from {path}"
        )
    except Exception as e:
        return ResponseModel(status=FAILURE, message=str(e))


@app.post("/run-negotiation")
async def run_negotiation(neg: NegotiationInfoModel) -> ResponseModel:
    asyncio.create_task(async_run(neg))
    return ResponseModel(
        status=SUCCESS,
        message=f"Running negotiation {neg.negid} ({neg.info.mode} mode)",
    )


@app.get("/peak")
async def peak() -> dict:
    """
    Returns local memory
    """
    return dict(
        config=config,
        sessions=SESSIONS,
    )
