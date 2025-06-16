from typing import TypedDict
from fastapi import FastAPI
import asyncio

from negmas.mechanisms import Mechanism
from negmas.serialization import deserialize, serialize
from negmasd.models import (
    NegResultsModel,
    NegStrategyModel,
    NegotiationInfoModel,
    ResponseModel,
    StrModel,
)
from negmasd.common import FAILURE, SUCCESS, get_response

app = FastAPI()


class DParamsType(TypedDict):
    python_class_identifier: str
    remove_type_field: bool


async def async_run(
    neg: NegotiationInfoModel, strategies: list[NegStrategyModel]
) -> None:
    dparams: DParamsType = DParamsType(
        python_class_identifier="type", remove_type_field=True
    )
    m = deserialize(neg.info.mechanism.type, **dparams)
    assert isinstance(m, Mechanism)
    for s in strategies:
        ufun = deserialize(s.ufun, **dparams)
        negotiator = deserialize(s.negotiator, **dparams)
        m.add(negotiator, preferences=ufun)

    m.run()
    for partner in neg.info.partners:
        get_response(
            f"http://{partner.host}:{partner.port}/receive-results",
            NegResultsModel(
                negid=neg.negid, agreement=m.agreement, state=serialize(m.state)
            ),
        )


@app.get("/")
async def root():
    return {"message": "Negotiation Runner"}


@app.get("/run-negotiation")
async def run_negotiation(neg: NegotiationInfoModel) -> ResponseModel:
    strategies: list[NegStrategyModel] = []
    for partner in neg.info.partners:
        strategy = get_response(
            f"http://{partner.host}:{partner.port}/neg-strategy",
            StrModel(value=neg.negid),
            response_type=NegStrategyModel,
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
