from negmas import BaseUtilityFunction, Mechanism, Negotiator, OutcomeSpace
from pydantic import BaseModel, Field
from .constants import RUNNER_DEFAULT_PORT


class PartnerModel(BaseModel):
    name: str = Field(description="Name of the partner")
    host: str = Field(default="127.0.0.1", description="Host of the partner")
    port: int = Field(default=0, description="Port of the partner")


class StrModel(BaseModel):
    value: str = Field(description="String value")


class MakableModel[T](BaseModel):
    type: str = Field(
        description="NegMAS type to make",
    )

    def make_object(self, **kwargs) -> T | None:
        """
        Create a Mechanism instance based on the type.
        """
        from negmas.serialization import deserialize

        if not self.type:
            return None

        dparams = dict(
            python_class_identifier="type",
            remove_type_field=True,
        )
        return deserialize(self.model_dump() | kwargs, **dparams)  # type: ignore


class OutcomeSpaceModel(MakableModel[OutcomeSpace]):
    """
    Model to hold negotiator information.
    """

    type: str = Field(
        default="negmas.outcomes.outcome_space.DiscreteCartesianOutcomeSpace",
        description="Outcome-space to use for a negotiation",
    )

    class Config:
        extra = "allow"


class NegotiatorModel(MakableModel[Negotiator]):
    """
    Model to hold negotiator information.
    """

    type: str = Field(
        default="negmas.sao.negotiators.BoulwareNegotiator",
        description="Negotiation strategy to use",
    )

    class Config:
        extra = "allow"


class MechanismModel(MakableModel[Mechanism]):
    """
    Base class for mechanism models.
    """

    type: str = Field(
        default="negmas.sao.mechanism.SAOMechanism", description="Mechanism type"
    )

    class Config:
        extra = "allow"


class UtilityFunctionModel(MakableModel[BaseUtilityFunction]):
    """
    Base class for utility function models.
    """

    type: str = Field(default="__unknown__", description="Utility function type")

    class Config:
        extra = "allow"


class NegotiationModel(BaseModel):
    """
    Configuration for a negotiation.
    """

    name: str = Field(description="Name of the negotiation")
    partners: list[PartnerModel] = Field(
        description="List of partner names involved in the negotiation"
    )
    outcome_space: OutcomeSpaceModel = Field(
        default_factory=OutcomeSpaceModel, description="Outcome space information"
    )
    mechanism: MechanismModel = Field(
        default_factory=MechanismModel,
        description="Mechanism model for the negotiation",
    )
    random_order: bool = Field(
        default=False,
        description="Randomize the order of partners when starting the negotiation",
    )
    mode: str = Field(
        default="distributed",
        description=(
            "Execution mode: 'distributed' (default; runner runs the real"
            " mechanism but forwards every per-round negotiator call to the"
            " partner's own process, so strategy + ufun never leave it) or"
            " 'centralized' (runner pulls each partner's strategy + ufun and"
            " runs the whole negotiation locally)."
        ),
    )
    seed: int | None = Field(
        default=None,
        description=(
            "If set, the runner seeds the global RNG with this value immediately"
            " before running the mechanism. Required for bit-exact reproducibility"
            " (e.g. random first-proposer selection) across runs/machines."
        ),
    )


class PersonalNegotiationModel(NegotiationModel):
    ufun: UtilityFunctionModel = Field(
        default_factory=UtilityFunctionModel,
        description="Utility function model for the negotiation",
    )
    negotiator: NegotiatorModel = Field(
        default_factory=NegotiatorModel, description="Negotiator model"
    )

    def remove_personal_info(self) -> NegotiationModel:
        """
        Returns a NegotiationModel without personal information.
        """
        return NegotiationModel(
            name=self.name,
            partners=self.partners,
            mechanism=self.mechanism,
            random_order=self.random_order,
            outcome_space=self.outcome_space,
        )


class AgentConfigModel(BaseModel):
    """
    Configuration for an agent.
    """

    name: str = Field(description="Name of the agent")
    port: int = Field(description="Role of the agent in the negotiation process")
    host: str = Field(default="127.0.0.1", description="Host to run the agent on")
    runner_host: str = Field(
        default="127.0.0.1", description="Host to run the agent on"
    )
    runner_port: int = Field(
        default=RUNNER_DEFAULT_PORT,
        description="Role of the agent in the negotiation process",
    )
    negotiations: dict[str, PersonalNegotiationModel] = Field(
        default_factory=dict,
        description="Map of names to  negotiation models that the agent is involved in",
    )


class ResponseModel(BaseModel):
    """
    Base class for response models.
    """

    status: str = Field(description="Status of the response")
    message: str = Field(default="", description="Message associated with the response")

    class Config:
        extra = "allow"


class NegotiationInfoModel(BaseModel):
    """
    Model to hold negotiation information in requests.
    """

    negid: str = Field(description="Unique identifier for the negotiation")
    info: NegotiationModel = Field(
        description="Negotiation information including partners, mechanism, and utility function"
    )


class NegResponseModel(ResponseModel):
    """
    Response model for negotiation acceptance.
    """

    accepted: bool = Field(
        default=False, description="Whether the negotiation was accepted"
    )


class NegStrategyModel(BaseModel):
    """
    Response model for negotiation acceptance.
    """

    negotiator: NegotiatorModel = Field(
        default_factory=NegotiatorModel, description="Negotiator information"
    )
    ufun: UtilityFunctionModel | None = Field(
        default=None, description="utility information information"
    )


class NegResultsModel(BaseModel):
    negid: str = Field(description="Unique identifier for the negotiation")
    agreement: tuple | None = Field(description="Agreement")
    state: dict = Field(description="Final state")


class RespondRequest(BaseModel):
    """Distributed mode: ask an agent for one negotiator response.

    ``state`` is a JSON-encoded ``SAOState`` (see ``negmasd.wire``). The agent
    returns a JSON-encoded ``SAOResponse``.
    """

    negid: str = Field(description="Unique identifier for the negotiation")
    state: dict = Field(description="Wire-encoded SAOState for the current round")
