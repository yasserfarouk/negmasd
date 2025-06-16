from pydantic import BaseModel, Field


class PartnerModel(BaseModel):
    name: str = Field(description="Name of the partner")
    host: str = Field(default="", description="Host of the partner")
    port: int = Field(default=0, description="Port of the partner")


class NegotiatorModel(BaseModel):
    """
    Model to hold negotiator information.
    """

    type: str = Field(
        default="negmas.sao.negotiators.BoulwareNegotiator",
        description="Negotiation strategy to use",
    )

    class Config:
        extra = "allow"


class StrModel(BaseModel):
    value: str = Field(description="String value")


class MechanismModel(BaseModel):
    """
    Base class for mechanism models.
    """

    type: str = Field(
        default="negmas.sao.mechanism.SAOMechanism", description="Mechanism type"
    )

    class Config:
        extra = "allow"


class UtilityFunctionModel(BaseModel):
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
    mechanism: MechanismModel = Field(
        default_factory=MechanismModel,
        description="Mechanism model for the negotiation",
    )
    random_order: bool = Field(
        default=False,
        description="Randomize the order of partners when starting the negotiation",
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
        default=9000, description="Role of the agent in the negotiation process"
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


class NegResponseModel(BaseModel):
    """
    Response model for negotiation acceptance.
    """

    accepted: bool = Field(
        default=True, description="Whether the negotiation was accepted"
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
