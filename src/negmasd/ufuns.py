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

from negmas.outcomes import Outcome
from negmas.preferences import BaseUtilityFunction

from .constants import DEFAULT_PROTOCOL
from .negotiator import address_from_url
from .transport import Address, CallableChannel, Channel, channel_for

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]

__all__ = ["RemoteUtilityFunction"]


class RemoteUtilityFunction(BaseUtilityFunction):
    """A ufun that evaluates outcomes in a remote process over HTTP."""

    def __init__(
        self,
        *args,
        address: Address | None = None,
        base_url: str = "",
        ufun_id: str = "default",
        transport: Transport | Channel | None = None,
        timeout: float = 60.0,
        reserved_value: float | None = None,
        **kwargs,
    ):
        self._base_url = base_url.rstrip("/")
        self._ufun_id = ufun_id
        self._timeout = timeout
        self._address = address
        if self._address is None and self._base_url:
            self._address = address_from_url(self._base_url)
        if transport is None:
            self._channel: Channel | None = None
        elif isinstance(transport, Channel):
            self._channel = transport
        else:
            self._channel = CallableChannel(transport)
        # Pull metadata the negotiator reads directly (reserved_value) unless the
        # caller already supplied it.
        if reserved_value is None:
            meta = self._post("/meta", dict(ufun_id=ufun_id))
            reserved_value = meta.get("reserved_value", float("nan"))
        super().__init__(*args, reserved_value=reserved_value, **kwargs)

    @property
    def channel(self) -> Channel:
        if self._channel is None:
            if self._address is None:
                raise ValueError(
                    "RemoteUtilityFunction needs an address, a base_url or a transport"
                )
            self._channel = channel_for(self._address)
        return self._channel

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.channel.call(path, payload)

    def eval(self, offer: Outcome | None) -> float:
        result = self._post(
            "/eval",
            dict(ufun_id=self._ufun_id, outcome=None if offer is None else list(offer)),
        )
        value = result.get("value")
        return float("nan") if value is None else float(value)

    def to_dict(self, *args, **kwargs) -> dict:
        address = self._address
        return dict(
            base_url=self._base_url,
            host=None if address is None else address.host,
            port=None if address is None else address.port,
            protocol=None if address is None else address.protocol,
            ufun_id=self._ufun_id,
            reserved_value=self.reserved_value,
        )

    @classmethod
    def from_dict(cls, d: dict) -> "RemoteUtilityFunction":
        d = dict(d)
        host, port = d.pop("host", None), d.pop("port", None)
        protocol = d.pop("protocol", None) or DEFAULT_PROTOCOL
        if host and port:
            d.setdefault("address", Address(host=host, port=port, protocol=protocol))
        return cls(**d)
