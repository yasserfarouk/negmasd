from datetime import datetime
from typing import TypedDict
from fastapi import FastAPI
from rich import print
from yaml import safe_load
from negmasd.models import (
    NegResultsModel,
    NegStrategyModel,
    PartnerModel,
    PersonalNegotiationModel,
    ResponseModel,
    AgentConfigModel,
    NegotiationInfoModel,
    NegResponseModel,
)
from uuid import uuid4

from .common import SUCCESS, FAILURE, get_response


class SessionResultsDict(TypedDict):
    info: PersonalNegotiationModel | None
    result: NegResultsModel | None


app = FastAPI()

config: AgentConfigModel | None = None

sessions_started: dict[str, PersonalNegotiationModel] = {}
sessions_accepted: dict[str, PersonalNegotiationModel] = {}
sessions_ended: dict[str, SessionResultsDict] = {}


def is_local(host: str) -> bool:
    return host in ("localhost", "0.0.0.0", "::1") or host.startswith("127.0.0.")


def is_self(partner: PartnerModel) -> bool:
    """Checks if the mentioned partner is the same as self"""
    if not config:
        return False
    if is_local(partner.host) and is_local(config.host) and partner.port == config.port:
        return True
    if (
        not is_local(partner.host)
        and not is_local(config.host)
        and config.host == partner.host
        and partner.port == config.port
    ):
        return True
    return False


@app.get("/")
async def root():
    return {"message": "Agent Service"}


@app.get("/load-config")
async def load_config(path: str) -> ResponseModel:
    global config
    # path = conf.value
    try:
        config = AgentConfigModel(**safe_load(open(path, "r")))
        return ResponseModel(
            status="success", message=f"Configuration loaded from {path}"
        )
    except Exception as e:
        return ResponseModel(status=FAILURE, message=str(e))


@app.get("/start-negotiation")
async def start_negotiation(neg: str) -> ResponseModel:
    if config is None:
        return ResponseModel(
            status=FAILURE,
            message="Configuration not loaded. Please load the configuration first on /load-config.",
        )
    try:
        # Get negotiation information from config
        myinfo = config.negotiations.get(neg, None)
        if myinfo is None:
            return ResponseModel(
                status=FAILURE,
                message=f"Negotiation of type {neg} not found in configuration. Available types are {list(config.negotiations.keys())}.",
            )
        negid = f"{neg}{datetime.now()}-{uuid4()}"
        # Assemble the information of the negotiation for the runner
        data = NegotiationInfoModel(negid=negid, info=myinfo.remove_personal_info())
        # Ask partners to accept to negotiate. Note that this may imply asking the initiator to accept as well if it is mentioned in the partners list.
        for partner in myinfo.partners:
            if is_self(partner):
                print(f"Skipping asking self to accept-negotiation ({partner})")
                sessions_accepted[negid] = myinfo
                continue
            validated_model = await get_response(
                f"http://{partner.host}:{partner.port}/accept-negotiation",
                data,
                response_type=NegResponseModel,
            )
            if not isinstance(validated_model, NegResponseModel):
                return validated_model
            if not validated_model.accepted:
                return ResponseModel(
                    status=FAILURE,
                    message=f"Negotiation did not start because {partner.name} did not accept the negotiation.",
                )
        # ask the negotiation runner to run the negotiation
        response = await get_response(
            f"http://{config.runner_host}:{config.runner_port}/run-negotiation", data
        )
        if isinstance(response, ResponseModel) and response.status != SUCCESS:
            return response
        sessions_started[negid] = myinfo
        return ResponseModel(status=SUCCESS, message=f"Negotiation {negid} started")
    except Exception as e:
        return ResponseModel(status=FAILURE, message=str(e))


@app.post("/accept-negotiation", response_model=NegResponseModel)
async def accept_negotiation(
    req: NegotiationInfoModel,
) -> NegResponseModel:
    if config is None:
        return NegResponseModel(
            accepted=False,
            status=FAILURE,
            message="Configuration not loaded. Please load the configuration first on /load-config.",
        )
    print(f"In accept-negotiation for {config.port}")
    myinfo = config.negotiations.get(req.info.name, None)
    if not myinfo:
        return NegResponseModel(
            accepted=False,
            status=FAILURE,
            message=f"Negotiation of type {req.info.name} not found in configuration. Available types are {list(config.negotiations.keys())}.",
        )
    sessions_accepted[req.negid] = myinfo
    # you can return accepted=False, status=SUCCESS to indicate that the agent decided not to accept the negotiation and there was no processing error
    return NegResponseModel(
        accepted=True, status=SUCCESS, message="Negotiation accepted"
    )


@app.get("/neg-strategy", response_model=NegStrategyModel | ResponseModel)
async def neg_strategy(negid: str) -> NegStrategyModel | ResponseModel:
    if config is None:
        return ResponseModel(
            status=FAILURE,
            message="Configuration not loaded. Please load the configuration first on /load-config.",
        )
    print(f"Getting myinfo for {negid}")
    myinfo = sessions_accepted.get(negid, None)
    print(myinfo)
    if myinfo is None:
        return ResponseModel(
            status=FAILURE,
            message=f"Negotiation {negid} not found in accepted negotiations. Available negotiations are {list(sessions_accepted.keys())}.",
        )

    return NegStrategyModel(negotiator=myinfo.negotiator, ufun=myinfo.ufun)


@app.post("/receive-results")
async def receive_results(results: NegResultsModel) -> ResponseModel:
    info_accepted = sessions_accepted.pop(results.negid, None)
    info_started = sessions_started.pop(results.negid, None)
    info = info_accepted if info_accepted else info_started
    sessions_ended[results.negid] = SessionResultsDict(info=info, result=results)
    # remove from the active sessions
    return ResponseModel(status=SUCCESS, message="Results received")


@app.get("/peak")
async def peak() -> dict:
    """
    Returns local memory
    """
    return dict(
        config=config,
        sessions_accepted=sessions_accepted,
        sessions_started=sessions_started,
        sessions_ended=sessions_ended,
    )
