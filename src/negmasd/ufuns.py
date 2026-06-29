"""Run a ufun in its own process/machine.

The third level of distribution: not only does each negotiator live in its own
process, its utility function can live in *yet another* process (e.g. a
proprietary or data-heavy ufun the negotiator's owner cannot or will not ship).

* ``RemoteUtilityFunction`` is a real ``BaseUtilityFunction`` whose ``eval`` is
  forwarded over JSON to a ufun service. The negotiator that holds it cannot
  tell it apart from a local ufun, so behaviour — and therefore parity — is
  unchanged. ``reserved_value`` (which negotiators read directly, not via a
  call) is fetched from the service at construction.
* ``ufun_service`` (in ``negmasd.ufun_service``) is the process that holds the
  real ufun and answers ``/eval`` / ``/meta``.

Outcomes cross the wire as JSON arrays and are re-tupled on the service side, so
``ufun(['v1','v2'])`` does not silently return ``None`` (see ``negmasd.wire``).
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
from negmas.outcomes import Outcome
from negmas.preferences import BaseUtilityFunction

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]

__all__ = ["RemoteUtilityFunction"]


class RemoteUtilityFunction(BaseUtilityFunction):
    """A ufun that evaluates outcomes in a remote process over HTTP."""

    def __init__(
        self,
        *args,
        base_url: str = "",
        ufun_id: str = "default",
        transport: Transport | None = None,
        timeout: float = 60.0,
        reserved_value: float | None = None,
        **kwargs,
    ):
        self._base_url = base_url.rstrip("/")
        self._ufun_id = ufun_id
        self._transport = transport
        self._timeout = timeout
        # Pull metadata the negotiator reads directly (reserved_value) unless the
        # caller already supplied it.
        if reserved_value is None:
            meta = self._post("/meta", dict(ufun_id=ufun_id))
            reserved_value = meta.get("reserved_value", float("nan"))
        super().__init__(*args, reserved_value=reserved_value, **kwargs)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._transport is not None:
            return self._transport(path, payload)
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(f"{self._base_url}{path}", json=payload)
            resp.raise_for_status()
            return resp.json()

    def eval(self, offer: Outcome | None) -> float:
        result = self._post(
            "/eval",
            dict(ufun_id=self._ufun_id, outcome=None if offer is None else list(offer)),
        )
        value = result.get("value")
        return float("nan") if value is None else float(value)

    def to_dict(self, *args, **kwargs) -> dict:
        return dict(
            base_url=self._base_url,
            ufun_id=self._ufun_id,
            reserved_value=self.reserved_value,
        )

    @classmethod
    def from_dict(cls, d: dict) -> "RemoteUtilityFunction":
        return cls(**d)
