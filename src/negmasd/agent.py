from datetime import datetime
from fastapi import FastAPI
from yaml import safe_load
from negmasd.models import (
    NegResultsModel,
    NegStrategyModel,
    PersonalNegotiationModel,
    ResponseModel,
    AgentConfigModel,
    NegotiationInfoModel,
    NegResponseModel,
    StrModel,
)
from uuid import uuid4

from .common import SUCCESS, FAILURE, get_response

app = FastAPI()

config: AgentConfigModel | None = None

sessions_started: dict[str, PersonalNegotiationModel] = {}
sessions_accepted: dict[str, PersonalNegotiationModel] = {}
sessions_ended: dict[str, tuple[PersonalNegotiationModel | None, NegResultsModel]] = {}


@app.get("/")
async def root():
    return {"message": "Agent Service"}


@app.post("/load-config")
async def load_config(conf: StrModel) -> ResponseModel:
    global config
    path = conf.value
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
            validated_model = get_response(
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
        response = get_response(
            f"http://{config.runner_host}:{config.runner_port}/run-negotiation", data
        )
        if isinstance(response, ResponseModel) and response.status != SUCCESS:
            return response
        sessions_started[negid] = myinfo
        return ResponseModel(status=SUCCESS, message=f"Negotiation {negid} started")
    except Exception as e:
        return ResponseModel(status=FAILURE, message=str(e))


@app.post("/accept-negotiation")
async def accept_negotiation(
    req: NegotiationInfoModel,
) -> NegResponseModel | ResponseModel:
    if config is None:
        return ResponseModel(
            status=FAILURE,
            message="Configuration not loaded. Please load the configuration first on /load-config.",
        )
    myinfo = config.negotiations.get(req.info.name, None)
    if not myinfo:
        return ResponseModel(
            status=FAILURE,
            message=f"Negotiation of type {req.info.name} not found in configuration. Available types are {list(config.negotiations.keys())}.",
        )
    sessions_accepted[req.negid] = myinfo
    return NegResponseModel(accepted=True)


@app.post("/neg-strategy")
async def neg_strategy(neg: StrModel) -> NegStrategyModel | ResponseModel:
    negid = neg.value
    if config is None:
        return ResponseModel(
            status=FAILURE,
            message="Configuration not loaded. Please load the configuration first on /load-config.",
        )
    myinfo = sessions_accepted.get(negid, None)
    if not myinfo:
        return ResponseModel(
            status=FAILURE,
            message=f"Negotiation {neg} not found in accepted negotiations. Available negotiations are {list(sessions_accepted.keys())}.",
        )

    if not myinfo:
        return ResponseModel(
            status=FAILURE,
            message=f"Negotiation of type {neg} not found in configuration. Available types are {list(config.negotiations.keys())}.",
        )
    return NegStrategyModel(negotiator=myinfo.negotiator, ufun=myinfo.ufun)


@app.get("/receive-results")
async def receive_results(results: NegResultsModel) -> ResponseModel:
    info_accepted = sessions_accepted.pop(results.negid, None)
    info_started = sessions_started.pop(results.negid, None)
    info = info_accepted if info_accepted else info_started
    sessions_ended[results.negid] = (info, results)
    # remove from the active sessions
    return ResponseModel(status=SUCCESS, message="Results received")
