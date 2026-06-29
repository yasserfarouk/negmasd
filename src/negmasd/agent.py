from datetime import datetime
from typing import TypedDict
from fastapi import FastAPI
from negmas import Negotiator
from rich import print
from yaml import safe_load
from negmasd.models import (
    NegResultsModel,
    NegStrategyModel,
    PartnerModel,
    PersonalNegotiationModel,
    RespondRequest,
    ResponseModel,
    AgentConfigModel,
    NegotiationInfoModel,
    NegResponseModel,
)
from uuid import uuid4

from .common import SUCCESS, FAILURE, get_response
from .wire import decode_state, encode_response


class SessionResultsDict(TypedDict):
    info: PersonalNegotiationModel | None
    result: NegResultsModel | None


app = FastAPI()

config: AgentConfigModel | None = None

sessions_started: dict[str, PersonalNegotiationModel] = {}
sessions_accepted: dict[str, PersonalNegotiationModel] = {}
sessions_ended: dict[str, SessionResultsDict] = {}
# Distributed mode: the real, live negotiator object for each negotiation,
# kept in *this* process across all rounds.
live_negotiators: dict[str, Negotiator] = {}


def build_negotiator(info: PersonalNegotiationModel) -> Negotiator:
    """Instantiate this agent's real negotiator + ufun, locally.

    The ufun carries the outcome space, so the negotiator is fully functional
    standalone (no mechanism join needed for time-based strategies). This is the
    object that lives in the agent's own process in distributed mode.
    """
    os = info.outcome_space.make_object()
    ufun = info.ufun.make_object(outcome_space=os) if info.ufun is not None else None
    if ufun is not None:
        negotiator = info.negotiator.make_object(preferences=ufun)
    else:
        negotiator = info.negotiator.make_object()
    if negotiator is None:
        raise ValueError(f"Could not build negotiator from {info.negotiator}")
    if ufun is not None:
        negotiator.set_preferences(ufun)
    return negotiator


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


@app.post("/init-negotiation")
async def init_negotiation(req: NegotiationInfoModel) -> ResponseModel:
    """Distributed mode: build and store the live negotiator for this process.

    Called by the runner before the mechanism starts. The agent looks up its
    *private* strategy + ufun for this negotiation and instantiates them here,
    so they never leave this process.
    """
    if config is None:
        return ResponseModel(
            status=FAILURE,
            message="Configuration not loaded. Please load the configuration first on /load-config.",
        )
    myinfo = sessions_accepted.get(req.negid, None) or config.negotiations.get(
        req.info.name, None
    )
    if myinfo is None:
        return ResponseModel(
            status=FAILURE,
            message=f"No accepted/known negotiation for {req.negid} ({req.info.name}).",
        )
    try:
        live_negotiators[req.negid] = build_negotiator(myinfo)
    except Exception as e:
        return ResponseModel(status=FAILURE, message=str(e))
    return ResponseModel(status=SUCCESS, message=f"Negotiator ready for {req.negid}")


@app.post("/respond")
async def respond(req: RespondRequest) -> dict:
    """Distributed mode: run one round of the real negotiator on this process.

    Receives a wire-encoded ``SAOState``, calls the live negotiator, and returns
    a wire-encoded ``SAOResponse``.
    """
    negotiator = live_negotiators.get(req.negid, None)
    if negotiator is None:
        # Lazily build if /init-negotiation was skipped.
        myinfo = sessions_accepted.get(req.negid, None)
        if myinfo is None:
            return ResponseModel(
                status=FAILURE,
                message=f"No live negotiator for {req.negid}. Call /init-negotiation first.",
            ).model_dump()
        negotiator = live_negotiators.setdefault(req.negid, build_negotiator(myinfo))
    state = decode_state(req.state)
    response = negotiator(state)
    return encode_response(response)


@app.post("/receive-results")
async def receive_results(results: NegResultsModel) -> ResponseModel:
    info_accepted = sessions_accepted.pop(results.negid, None)
    info_started = sessions_started.pop(results.negid, None)
    live_negotiators.pop(results.negid, None)
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
