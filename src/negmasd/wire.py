"""Language-agnostic JSON wire codec for SAO runtime objects.

The distributed runner runs the *real* ``SAOMechanism`` and forwards each
per-round ``negotiator(state)`` call to the negotiator's own process. The two
sides may run different Python versions (or live on different machines), so the
wire format is plain JSON with an *explicit* schema — never pickle and never
``negmas.serialization.serialize`` (which silently drops the ``ResponseType``
enum to ``{}`` and turns outcome tuples into lists).

Two invariants make this exact:

* ``ResponseType`` is an ``IntEnum`` — carried as its integer ``.value``.
* negmas outcomes are *tuples*; JSON only has arrays, so every outcome is
  re-tupled on decode. Forgetting this makes ``ufun(offer)`` return ``None`` and
  the negotiator silently rejects every offer.
"""

from __future__ import annotations

from typing import Any

from negmas import ResponseType
from negmas.sao.common import SAOResponse, SAOState

__all__ = [
    "encode_outcome",
    "decode_outcome",
    "encode_state",
    "decode_state",
    "encode_response",
    "decode_response",
]


def encode_outcome(outcome: Any) -> list | None:
    """Outcome tuple -> JSON array (or ``None``)."""
    return None if outcome is None else list(outcome)


def decode_outcome(outcome: Any) -> tuple | None:
    """JSON array -> outcome tuple (or ``None``)."""
    return None if outcome is None else tuple(outcome)


# Fields of SAOState that a negotiator may read, that round-trip as plain
# JSON scalars. Outcome-bearing fields are handled separately below.
_SCALAR_FIELDS = (
    "running",
    "waiting",
    "started",
    "step",
    "time",
    "relative_time",
    "broken",
    "timedout",
    "n_negotiators",
    "has_error",
    "current_proposer",
    "current_proposer_agent",
    "n_acceptances",
    "last_negotiator",
)


def encode_state(state: SAOState) -> dict[str, Any]:
    """Serialize a ``SAOState`` to a JSON-safe dict."""
    data: dict[str, Any] = {f: getattr(state, f) for f in _SCALAR_FIELDS}
    data["current_offer"] = encode_outcome(state.current_offer)
    data["new_offers"] = [
        [nid, encode_outcome(o)] for nid, o in (state.new_offers or [])
    ]
    return data


def decode_state(data: dict[str, Any]) -> SAOState:
    """Reconstruct a ``SAOState`` from its wire form (re-tupling outcomes)."""
    kwargs: dict[str, Any] = {f: data[f] for f in _SCALAR_FIELDS if f in data}
    kwargs["current_offer"] = decode_outcome(data.get("current_offer"))
    kwargs["new_offers"] = [
        (nid, decode_outcome(o)) for nid, o in data.get("new_offers", [])
    ]
    return SAOState(**kwargs)


def encode_response(response: SAOResponse) -> dict[str, Any]:
    """Serialize a ``SAOResponse`` (enum -> int, outcome -> array)."""
    return dict(
        response=int(response.response.value),
        outcome=encode_outcome(response.outcome),
        data=response.data,
    )


def decode_response(data: dict[str, Any]) -> SAOResponse:
    """Reconstruct a ``SAOResponse`` from its wire form."""
    return SAOResponse(
        response=ResponseType(data["response"]),
        outcome=decode_outcome(data.get("outcome")),
        data=data.get("data"),
    )
