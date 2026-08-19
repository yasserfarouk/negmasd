"""A service that holds real utility functions and evaluates them.

This is the process a ``RemoteUtilityFunction`` talks to. It keeps the ufun
(and its outcome space) entirely on its own side; clients only ever send an
outcome and receive a number.

A single service can host several ufuns keyed by ``ufun_id``. Load them from a
config file whose top level maps ``ufun_id`` -> a ufun model (a ``type`` field
plus its constructor kwargs, as elsewhere in negmasd)::

    seller:
      type: negmas.preferences.crisp.linear.LinearAdditiveUtilityFunction
      ...

Like the agent and runner roles this is a transport-free
:class:`~negmasd.transport.base.Service`; ``app`` is its FastAPI projection.
"""

from __future__ import annotations

from typing import Any, Sequence

from negmas.preferences import BaseUtilityFunction
from yaml import safe_load

from .common import FAILURE, SUCCESS
from .models import PathRequest, ResponseModel, UtilityFunctionModel
from .transport import MethodSpec, Service, build_app


class UfunService(Service):
    """Holds real ufuns and answers evaluation requests about them."""

    def __init__(self) -> None:
        # ufun_id -> live ufun
        self.ufuns: dict[str, BaseUtilityFunction] = {}

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
            MethodSpec("meta", "meta", dict),
            MethodSpec("eval", "eval_outcome", dict),
        )

    async def root(self) -> dict:
        return {"message": "Utility Function Service"}

    async def load_config(self, req: PathRequest) -> ResponseModel:
        """Load one or more ufuns from a YAML mapping ``ufun_id -> ufun model``."""
        try:
            loaded = safe_load(open(req.path, "r"))
            for ufun_id, model in loaded.items():
                os = None
                if isinstance(model, dict) and "outcome_space" in model:
                    from .models import OutcomeSpaceModel

                    os = OutcomeSpaceModel(**model["outcome_space"]).make_object()
                ufun = UtilityFunctionModel(**model).make_object(outcome_space=os)
                if ufun is None:
                    return ResponseModel(
                        status=FAILURE, message=f"Could not build ufun '{ufun_id}'"
                    )
                self.ufuns[ufun_id] = ufun
            return ResponseModel(
                status=SUCCESS, message=f"Loaded ufuns: {list(self.ufuns.keys())}"
            )
        except Exception as e:
            return ResponseModel(status=FAILURE, message=str(e))

    async def meta(self, req: dict) -> dict:
        ufun = self.ufuns.get(req.get("ufun_id", "default"))
        if ufun is None:
            return {"reserved_value": None}
        return {"reserved_value": ufun.reserved_value}

    async def eval_outcome(self, req: dict) -> dict[str, Any]:
        ufun = self.ufuns.get(req.get("ufun_id", "default"))
        if ufun is None:
            return {"value": None, "error": f"unknown ufun {req.get('ufun_id')}"}
        outcome = req.get("outcome")
        outcome = None if outcome is None else tuple(outcome)  # JSON array -> tuple
        value = ufun(outcome)
        return {"value": None if value is None else float(value)}


service = UfunService()
app = build_app(service)

# Backwards-compatible module-level alias for the live ufun registry.
UFUNS = service.ufuns
