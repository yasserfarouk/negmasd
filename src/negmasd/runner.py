from typing import TypedDict

from yaml import safe_load
from fastapi import FastAPI
import asyncio
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


async def async_run(
    neg: NegotiationInfoModel, strategies: list[NegStrategyModel]
) -> None:
    print("Running negotiation:")
    print(neg)
    print(strategies)
    os = neg.info.outcome_space.make_object()
    m = neg.info.mechanism.make_object(outcome_space=os)
    assert isinstance(m, Mechanism), (
        f"Mechanism type is not valid: {type(m)}\n{neg.info.mechanism}"
    )
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

    m.run()
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
    strategies: list[NegStrategyModel] = []
    for partner in neg.info.partners:
        strategy = await get_response(
            f"http://{partner.host}:{partner.port}/neg-strategy",
            dict(negid=neg.negid),
            response_type=NegStrategyModel,
            method="get",
        )
        if not isinstance(strategy, NegStrategyModel):
            return ResponseModel(
                status=FAILURE,
                message=f"Partner {partner} did not return valid strategy info {strategy}",
            )
        strategies.append(strategy)
    asyncio.create_task(async_run(neg, strategies))

    return ResponseModel(
        status=SUCCESS,
        message=f"Running negotiation {neg.negid}",
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
