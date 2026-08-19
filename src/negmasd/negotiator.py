"""Runner-side proxy negotiator for the truly-distributed execution mode.

In distributed mode the runner builds the *real* ``SAOMechanism`` but adds one
``SAORemoteNegotiator`` per partner instead of the partner's real strategy. The
proxy holds **no ufun and no strategy** — every ``__call__(state)`` is forwarded
to the partner's own process, which runs the real negotiator and returns its
``SAOResponse``. This is what keeps each negotiator (and optionally its ufun) in
its own process/machine, possibly under a different Python version.

The forwarding goes through a :class:`~negmasd.transport.base.Channel`, so the
proxy is protocol-agnostic: point it at a ``grpc`` address and nothing else
changes. ``transport=`` (a plain ``Callable[[path, payload], dict]``) is still
accepted for in-process testing.

Because a time-based negotiator's response is a pure function of the *passed*
state plus its own ufun (and not the mechanism's clock), feeding it the exact
per-round states the mechanism produces reproduces negmas-proper behaviour
exactly. See ``tests`` for the parity proof.
"""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlsplit

from negmas import SAONegotiator, SAOResponse, SAOState

from .constants import DEFAULT_PROTOCOL
from .transport import Address, CallableChannel, Channel, channel_for
from .wire import decode_response, encode_state

# A transport posts a JSON payload to ``path`` and returns the JSON response.
# Kept as a public alias: the parity tests inject one of these directly.
Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


def address_from_url(url: str, default_protocol: str = DEFAULT_PROTOCOL) -> Address:
    """Parse a ``scheme://host:port`` base URL into an :class:`Address`."""
    parts = urlsplit(url)
    return Address(
        host=parts.hostname or "127.0.0.1",
        port=parts.port or 0,
        protocol=parts.scheme or default_protocol,
    )


class SAORemoteNegotiator(SAONegotiator):
    """A ``SAONegotiator`` that delegates every decision to a remote process."""

    def __init__(
        self,
        *args,
        address: Address | None = None,
        base_url: str = "",
        negid: str = "",
        partner_name: str = "",
        transport: Transport | Channel | None = None,
        timeout: float = 60.0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._negid = negid
        self._partner_name = partner_name
        self._timeout = timeout
        self._address = address
        if self._address is None and base_url:
            self._address = address_from_url(base_url)
        # An explicit transport (Channel or bare callable) wins; otherwise the
        # channel is resolved — and cached — from the address on first use.
        if transport is None:
            self._channel: Channel | None = None
        elif isinstance(transport, Channel):
            self._channel = transport
        else:
            self._channel = CallableChannel(transport)

    @property
    def channel(self) -> Channel:
        if self._channel is None:
            if self._address is None:
                raise ValueError(
                    "SAORemoteNegotiator needs an address, a base_url or a transport"
                )
            self._channel = channel_for(self._address)
        return self._channel

    def __call__(self, state: SAOState, dest: str | None = None) -> SAOResponse:
        # Synchronous on purpose: the mechanism's run loop is synchronous.
        encoded = self.channel.call(
            "respond", dict(negid=self._negid, state=encode_state(state))
        )
        return decode_response(encoded)
