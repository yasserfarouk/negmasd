"""A FastAPI service that holds real utility functions and evaluates them.

This is the process a ``RemoteUtilityFunction`` talks to. It keeps the ufun
(and its outcome space) entirely on its own side; clients only ever send an
outcome and receive a number.

A single service can host several ufuns keyed by ``ufun_id``. Load them from a
config file whose top level maps ``ufun_id`` -> a ufun model (a ``type`` field
plus its constructor kwargs, as elsewhere in negmasd)::

    seller:
      type: negmas.preferences.crisp.linear.LinearAdditiveUtilityFunction
      ...
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from negmas.preferences import BaseUtilityFunction
from yaml import safe_load

from .common import FAILURE, SUCCESS
from .models import ResponseModel, UtilityFunctionModel

app = FastAPI()

# ufun_id -> live ufun
UFUNS: dict[str, BaseUtilityFunction] = {}


@app.get("/")
async def root():
    return {"message": "Utility Function Service"}


@app.get("/load-config")
async def load_config(path: str) -> ResponseModel:
    """Load one or more ufuns from a YAML mapping ``ufun_id -> ufun model``."""
    try:
        loaded = safe_load(open(path, "r"))
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
            UFUNS[ufun_id] = ufun
        return ResponseModel(
            status=SUCCESS, message=f"Loaded ufuns: {list(UFUNS.keys())}"
        )
    except Exception as e:
        return ResponseModel(status=FAILURE, message=str(e))


@app.post("/meta")
async def meta(req: dict) -> dict:
    ufun = UFUNS.get(req.get("ufun_id", "default"))
    if ufun is None:
        return {"reserved_value": None}
    return {"reserved_value": ufun.reserved_value}


@app.post("/eval")
async def eval_outcome(req: dict) -> dict[str, Any]:
    ufun = UFUNS.get(req.get("ufun_id", "default"))
    if ufun is None:
        return {"value": None, "error": f"unknown ufun {req.get('ufun_id')}"}
    outcome = req.get("outcome")
    outcome = None if outcome is None else tuple(outcome)  # JSON array -> tuple
    value = ufun(outcome)
    return {"value": None if value is None else float(value)}
