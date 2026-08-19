"""The agent role: one process per negotiator, holding that party's private info.

The negotiation logic lives in :class:`AgentService`, a plain
:class:`~negmasd.transport.base.Service` with no transport code. Its method table
is projected onto whatever protocol is asked for; ``app`` below is the FastAPI
projection, kept at module scope so ``uvicorn negmasd.agent:app`` still works.
"""

from datetime import datetime
from typing import Sequence, TypedDict

from negmas import Negotiator
from rich import print
from yaml import safe_load

from negmasd.models import (
    AgentConfigModel,
    NegIdRequest,
    NegResponseModel,
    NegResultsModel,
    NegStrategyModel,
    NegotiationInfoModel,
    PartnerModel,
    PathRequest,
    PersonalNegotiationModel,
    RespondRequest,
    ResponseModel,
    StartNegotiationRequest,
)
from uuid import uuid4

from .common import SUCCESS, FAILURE, call_service
from .transport import MethodSpec, Service, build_app
from .wire import decode_state, encode_response


class SessionResultsDict(TypedDict):
    info: PersonalNegotiationModel | None
    result: NegResultsModel | None


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


class AgentService(Service):
    """One negotiating party. Holds its private strategy + ufun and its sessions."""

    def __init__(self) -> None:
        self.config: AgentConfigModel | None = None
        self.sessions_started: dict[str, PersonalNegotiationModel] = {}
        self.sessions_accepted: dict[str, PersonalNegotiationModel] = {}
        self.sessions_ended: dict[str, SessionResultsDict] = {}
        # Distributed mode: the real, live negotiator object for each
        # negotiation, kept in *this* process across all rounds.
        self.live_negotiators: dict[str, Negotiator] = {}

    def methods(self) -> Sequence[MethodSpec]:
        return (
            MethodSpec("root", "root", http_method="get", path="/"),
            MethodSpec(
                "load-config",
                "load_config",
                PathRequest,
                ResponseModel,
                http_method="get",
            ),
            MethodSpec(
                "start-negotiation",
                "start_negotiation",
                StartNegotiationRequest,
                ResponseModel,
                http_method="get",
            ),
            MethodSpec(
                "accept-negotiation",
                "accept_negotiation",
                NegotiationInfoModel,
                NegResponseModel,
            ),
            MethodSpec(
                "neg-strategy",
                "neg_strategy",
                NegIdRequest,
                NegStrategyModel | ResponseModel,
                http_method="get",
            ),
            MethodSpec(
                "init-negotiation", "init_negotiation", NegotiationInfoModel, ResponseModel
            ),
            # response_model stays None: the wire-encoded SAOState/SAOResponse
            # must pass through as a raw dict. Round-tripping it through a
            # pydantic model is exactly where ResponseType collapses to {} and
            # outcome tuples degrade to lists (see negmasd.wire).
            MethodSpec("respond", "respond", RespondRequest),
            MethodSpec(
                "receive-results", "receive_results", NegResultsModel, ResponseModel
            ),
            MethodSpec("peak", "peak", http_method="get"),
        )

    # -- helpers ------------------------------------------------------------- #
    def is_self(self, partner: PartnerModel) -> bool:
        """Checks if the mentioned partner is the same as self"""
        config = self.config
        if not config:
            return False
        if (
            is_local(partner.host)
            and is_local(config.host)
            and partner.port == config.port
        ):
            return True
        if (
            not is_local(partner.host)
            and not is_local(config.host)
            and config.host == partner.host
            and partner.port == config.port
        ):
            return True
        return False

    # -- endpoints ----------------------------------------------------------- #
    async def root(self) -> dict:
        return {"message": "Agent Service"}

    async def load_config(self, req: PathRequest) -> ResponseModel:
        try:
            self.config = AgentConfigModel(**safe_load(open(req.path, "r")))
            return ResponseModel(
                status="success", message=f"Configuration loaded from {req.path}"
            )
        except Exception as e:
            return ResponseModel(status=FAILURE, message=str(e))

    async def start_negotiation(self, req: StartNegotiationRequest) -> ResponseModel:
        config = self.config
        if config is None:
            return ResponseModel(
                status=FAILURE,
                message="Configuration not loaded. Please load the configuration first on /load-config.",
            )
        neg = req.neg
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
            data = NegotiationInfoModel(
                negid=negid, info=myinfo.remove_personal_info()
            )
            # Ask partners to accept to negotiate. Note that this may imply asking
            # the initiator to accept as well if it is in the partners list.
            for partner in myinfo.partners:
                if self.is_self(partner):
                    print(f"Skipping asking self to accept-negotiation ({partner})")
                    self.sessions_accepted[negid] = myinfo
                    continue
                validated_model = await call_service(
                    partner.address,
                    "accept-negotiation",
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
            response = await call_service(
                config.runner_address, "run-negotiation", data
            )
            if isinstance(response, ResponseModel) and response.status != SUCCESS:
                return response
            self.sessions_started[negid] = myinfo
            return ResponseModel(status=SUCCESS, message=f"Negotiation {negid} started")
        except Exception as e:
            return ResponseModel(status=FAILURE, message=str(e))

    async def accept_negotiation(self, req: NegotiationInfoModel) -> NegResponseModel:
        config = self.config
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
        self.sessions_accepted[req.negid] = myinfo
        # you can return accepted=False, status=SUCCESS to indicate that the agent
        # decided not to accept the negotiation and there was no processing error
        return NegResponseModel(
            accepted=True, status=SUCCESS, message="Negotiation accepted"
        )

    async def neg_strategy(self, req: NegIdRequest) -> NegStrategyModel | ResponseModel:
        if self.config is None:
            return ResponseModel(
                status=FAILURE,
                message="Configuration not loaded. Please load the configuration first on /load-config.",
            )
        print(f"Getting myinfo for {req.negid}")
        myinfo = self.sessions_accepted.get(req.negid, None)
        print(myinfo)
        if myinfo is None:
            return ResponseModel(
                status=FAILURE,
                message=f"Negotiation {req.negid} not found in accepted negotiations. Available negotiations are {list(self.sessions_accepted.keys())}.",
            )

        return NegStrategyModel(negotiator=myinfo.negotiator, ufun=myinfo.ufun)

    async def init_negotiation(self, req: NegotiationInfoModel) -> ResponseModel:
        """Distributed mode: build and store the live negotiator for this process.

        Called by the runner before the mechanism starts. The agent looks up its
        *private* strategy + ufun for this negotiation and instantiates them
        here, so they never leave this process.
        """
        config = self.config
        if config is None:
            return ResponseModel(
                status=FAILURE,
                message="Configuration not loaded. Please load the configuration first on /load-config.",
            )
        myinfo = self.sessions_accepted.get(req.negid, None) or config.negotiations.get(
            req.info.name, None
        )
        if myinfo is None:
            return ResponseModel(
                status=FAILURE,
                message=f"No accepted/known negotiation for {req.negid} ({req.info.name}).",
            )
        try:
            self.live_negotiators[req.negid] = build_negotiator(myinfo)
        except Exception as e:
            return ResponseModel(status=FAILURE, message=str(e))
        return ResponseModel(
            status=SUCCESS, message=f"Negotiator ready for {req.negid}"
        )

    async def respond(self, req: RespondRequest) -> dict:
        """Distributed mode: run one round of the real negotiator on this process.

        Receives a wire-encoded ``SAOState``, calls the live negotiator, and
        returns a wire-encoded ``SAOResponse``.
        """
        negotiator = self.live_negotiators.get(req.negid, None)
        if negotiator is None:
            # Lazily build if /init-negotiation was skipped.
            myinfo = self.sessions_accepted.get(req.negid, None)
            if myinfo is None:
                return ResponseModel(
                    status=FAILURE,
                    message=f"No live negotiator for {req.negid}. Call /init-negotiation first.",
                ).model_dump()
            negotiator = self.live_negotiators.setdefault(
                req.negid, build_negotiator(myinfo)
            )
        state = decode_state(req.state)
        response = negotiator(state)
        return encode_response(response)

    async def receive_results(self, results: NegResultsModel) -> ResponseModel:
        info_accepted = self.sessions_accepted.pop(results.negid, None)
        info_started = self.sessions_started.pop(results.negid, None)
        self.live_negotiators.pop(results.negid, None)
        info = info_accepted if info_accepted else info_started
        self.sessions_ended[results.negid] = SessionResultsDict(
            info=info, result=results
        )
        # remove from the active sessions
        return ResponseModel(status=SUCCESS, message="Results received")

    async def peak(self) -> dict:
        """
        Returns local memory
        """
        return dict(
            config=self.config,
            sessions_accepted=self.sessions_accepted,
            sessions_started=self.sessions_started,
            sessions_ended=self.sessions_ended,
        )


# The process-wide agent and its FastAPI projection. `uvicorn negmasd.agent:app`
# and `negmasd agent <port>` both rely on these names.
service = AgentService()
app = build_app(service)
