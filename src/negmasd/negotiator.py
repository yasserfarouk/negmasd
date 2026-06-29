"""Runner-side proxy negotiator for the truly-distributed execution mode.

In distributed mode the runner builds the *real* ``SAOMechanism`` but adds one
``SAORemoteNegotiator`` per partner instead of the partner's real strategy. The
proxy holds **no ufun and no strategy** — every ``__call__(state)`` is forwarded
over HTTP to the partner's own process, which runs the real negotiator and
returns its ``SAOResponse``. This is what keeps each negotiator (and optionally
its ufun) in its own process/machine, possibly under a different Python version.

Because a time-based negotiator's response is a pure function of the *passed*
state plus its own ufun (and not the mechanism's clock), feeding it the exact
per-round states the mechanism produces reproduces negmas-proper behaviour
exactly. See ``tests`` for the parity proof.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
from negmas import SAONegotiator, SAOResponse, SAOState

from .wire import decode_response, encode_state

# A transport posts a JSON payload to ``path`` and returns the JSON response.
# Defaults to real HTTP; tests inject one that targets a FastAPI TestClient.
Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class SAORemoteNegotiator(SAONegotiator):
    """A ``SAONegotiator`` that delegates every decision to a remote process."""

    def __init__(
        self,
        *args,
        base_url: str = "",
        negid: str = "",
        partner_name: str = "",
        transport: Transport | None = None,
        timeout: float = 60.0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._base_url = base_url.rstrip("/")
        self._negid = negid
        self._partner_name = partner_name
        self._transport = transport
        self._timeout = timeout

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._transport is not None:
            return self._transport(path, payload)
        # Synchronous on purpose: the mechanism's run loop is synchronous.
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(f"{self._base_url}{path}", json=payload)
            resp.raise_for_status()
            return resp.json()

    def __call__(self, state: SAOState, dest: str | None = None) -> SAOResponse:
        encoded = self._post(
            "/respond",
            dict(negid=self._negid, state=encode_state(state)),
        )
        return decode_response(encoded)
