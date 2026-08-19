"""The runner role: the single service that builds and runs the negmas mechanism.

As with the agent, the logic lives in a transport-free
:class:`~negmasd.transport.base.Service`; ``app`` is its FastAPI projection and
stays at module scope so ``uvicorn negmasd.runner:app`` keeps working.
"""

from typing import Sequence, TypedDict

from yaml import safe_load
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
    PathRequest,
    ResponseModel,
)
from negmasd.negotiator import SAORemoteNegotiator
from negmasd.common import FAILURE, SUCCESS, call_service
from negmasd.constants import RUNNER_DEFAULT_PORT
from negmasd.transport import MethodSpec, Service, build_app


class SessionResults(TypedDict):
    negid: str
    info: NegotiationModel
    final_state: dict
    agreement: tuple | None
    trace: list[TraceElement]


class DParamsType(TypedDict):
    python_class_identifier: str
    remove_type_field: bool


def default_config() -> dict:
    return dict(keep_trace=True, port=RUNNER_DEFAULT_PORT, name="runner")


def build_mechanism(neg: NegotiationInfoModel) -> Mechanism:
    os = neg.info.outcome_space.make_object()
    m = neg.info.mechanism.make_object(outcome_space=os)
    assert isinstance(m, Mechanism), (
        f"Mechanism type is not valid: {type(m)}\n{neg.info.mechanism}"
    )
    return m


class RunnerService(Service):
    """Builds and runs mechanisms on behalf of the agents."""

    def __init__(self) -> None:
        self.config: dict = default_config()
        self.sessions: dict = {}

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
                "run-negotiation",
                "run_negotiation",
                NegotiationInfoModel,
                ResponseModel,
            ),
            MethodSpec("peak", "peak", http_method="get"),
        )

    # -- mechanism assembly -------------------------------------------------- #
    async def add_centralized_negotiators(
        self, m: Mechanism, neg: NegotiationInfoModel
    ) -> None:
        """Pull each partner's strategy + ufun and build them in the runner process."""
        os = m.outcome_space
        strategies: list[NegStrategyModel] = []
        for partner in neg.info.partners:
            strategy = await call_service(
                partner.address,
                "neg-strategy",
                dict(negid=neg.negid),
                response_type=NegStrategyModel,
                http_method="get",
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

    async def add_distributed_negotiators(
        self, m: Mechanism, neg: NegotiationInfoModel
    ) -> None:
        """Add one remote proxy per partner; each forwards rounds to its process.

        The partner's real strategy + ufun never leave its process. The proxy
        holds no ufun: we only need the per-round response over the wire.
        """
        partners = list(neg.info.partners)
        if neg.info.random_order:
            shuffle(partners)
        for partner in partners:
            # Ask the partner to build and hold its live negotiator.
            resp = await call_service(
                partner.address,
                "init-negotiation",
                neg,
                response_type=ResponseModel,
            )
            if isinstance(resp, ResponseModel) and resp.status != SUCCESS:
                raise ValueError(
                    f"Partner {partner.name} failed to init: {resp.message}"
                )
            m.add(
                SAORemoteNegotiator(
                    address=partner.address,
                    negid=neg.negid,
                    partner_name=partner.name,
                    name=partner.name,
                )
            )

    async def async_run(self, neg: NegotiationInfoModel) -> None:
        print(f"Running negotiation ({neg.info.mode} mode):")
        print(neg)
        m = build_mechanism(neg)
        if neg.info.mode == "distributed":
            await self.add_distributed_negotiators(m, neg)
        else:
            await self.add_centralized_negotiators(m, neg)

        if neg.info.seed is not None:
            # Bit-exact reproducibility (e.g. random first-proposer selection).
            random.seed(neg.info.seed)
        # The mechanism run loop is synchronous and, in distributed mode, makes
        # blocking calls per round; keep it off the event loop.
        await asyncio.to_thread(m.run)
        print(m.state)
        log_entry = dict(
            negid=neg.negid,
            info=neg.info,
            final_state=serialize(m.state, python_class_identifier="type"),
            agreement=m.agreement,
            trace=[],
        )
        if self.config["keep_trace"] and hasattr(m, "full_trace"):
            log_entry["trace"] = m.full_trace  # type: ignore
        self.sessions[neg.negid] = log_entry
        for partner in neg.info.partners:
            print(f" Sending results to {partner.address}/receive-results")
            await call_service(
                partner.address,
                "receive-results",
                NegResultsModel(
                    negid=neg.negid, agreement=m.agreement, state=serialize(m.state)
                ),
            )

    # -- endpoints ----------------------------------------------------------- #
    async def root(self) -> dict:
        return {"message": "Negotiation Runner"}

    async def load_config(self, req: PathRequest) -> ResponseModel:
        try:
            loaded = safe_load(open(req.path, "r"))
            for k, v in loaded.items():
                self.config[k] = v
            return ResponseModel(
                status="success", message=f"Configuration loaded from {req.path}"
            )
        except Exception as e:
            return ResponseModel(status=FAILURE, message=str(e))

    async def run_negotiation(self, neg: NegotiationInfoModel) -> ResponseModel:
        asyncio.create_task(self.async_run(neg))
        return ResponseModel(
            status=SUCCESS,
            message=f"Running negotiation {neg.negid} ({neg.info.mode} mode)",
        )

    async def peak(self) -> dict:
        """
        Returns local memory
        """
        return dict(
            config=self.config,
            sessions=self.sessions,
        )


service = RunnerService()
app = build_app(service)

# Backwards-compatible module-level aliases (the runner's state used to live
# here). They are views onto the process-wide service, not copies.
config = service.config
SESSIONS = service.sessions
