"""Transport-agnostic core: addresses, the method table, and channels.

negmasd's service logic must not know how bytes reach a peer. This module holds
the three pieces that make that true:

* :class:`Address` — *where* a peer is, including *which* protocol to speak.
* :class:`MethodSpec` / :class:`Service` — the **method table**: a declarative
  list of a service's callable endpoints. Every server adapter (FastAPI, gRPC,
  ...) is a projection of this one table, so the protocols cannot drift apart.
* :class:`Channel` — a connection to one peer, offering **both** a sync and an
  async call form. Both are required: the four handshake calls happen inside the
  event loop, but the per-round ``respond`` call is made from the synchronous
  ``Mechanism.run`` loop (see ``runner.async_run``'s ``asyncio.to_thread``).

Transports are registered by protocol name and resolved lazily *by module path*
(see :func:`register_transport`), so an optional dependency such as ``grpcio``
is never imported unless a peer actually asks for that protocol.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Callable, Sequence

from pydantic import BaseModel

__all__ = [
    "Address",
    "MethodSpec",
    "Service",
    "Channel",
    "CallableChannel",
    "TransportError",
    "TransportUnavailable",
    "register_transport",
    "register_server",
    "channel_for",
    "close_all_channels",
    "known_protocols",
    "serve_service",
]


class TransportError(Exception):
    """A call could not be completed over the wire."""


class TransportUnavailable(TransportError):
    """The requested protocol's optional dependency is not installed."""


@dataclass(frozen=True)
class Address:
    """Where a peer lives and how to talk to it."""

    host: str = "127.0.0.1"
    port: int = 0
    protocol: str = "http"

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.protocol, self.host, self.port)

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.protocol}://{self.host}:{self.port}"


@dataclass(frozen=True)
class MethodSpec:
    """One callable endpoint of a service, described once for all transports.

    ``name`` is the wire-level method name. It doubles as the HTTP path (so the
    existing URLs are preserved verbatim) and as the gRPC ``method`` selector.

    ``http_method`` is read **only** by the FastAPI adapter — gRPC has no
    GET/POST distinction and ignores it. ``kind`` is always ``"unary"`` today;
    it exists so that adding a streaming variant later does not require changing
    the table's shape.
    """

    name: str
    handler: str
    request_model: type[BaseModel] | None = None
    response_model: Any = None
    http_method: str = "post"
    kind: str = "unary"
    path: str | None = None

    @property
    def route(self) -> str:
        """The HTTP path for this method."""
        return self.path if self.path is not None else "/" + self.name.lstrip("/")


class Service(ABC):
    """A negmasd role (agent, runner, ufun) as plain Python.

    Subclasses hold their own state as instance attributes and expose their
    endpoints through :meth:`methods`. They contain no transport code at all.
    """

    @abstractmethod
    def methods(self) -> Sequence[MethodSpec]:
        """Declare the service's method table (called once; see method_table)."""

    @property
    def method_table(self) -> tuple[MethodSpec, ...]:
        """The method table, built once per instance.

        Cached because :meth:`dispatch` consults it on *every* call, including
        the per-round ``respond`` on the hot path.
        """
        table = self.__dict__.get("_method_table")
        if table is None:
            table = tuple(self.methods())
            self.__dict__["_method_table"] = table
            self.__dict__["_spec_index"] = {
                key: spec
                for spec in table
                for key in (spec.name, spec.route)
            }
        return table

    def spec_for(self, name: str) -> MethodSpec:
        self.method_table  # ensure the index exists
        index = self.__dict__["_spec_index"]
        spec = index.get(name) or index.get(name.strip("/"))
        if spec is None:
            raise KeyError(name)
        return spec

    def handler_for(self, name: str) -> Callable:
        return getattr(self, self.spec_for(name).handler)

    async def dispatch(self, name: str, payload: dict[str, Any] | None = None) -> Any:
        """Invoke one method by name with a raw JSON-ish payload.

        Used by the non-FastAPI adapters. The payload is validated into the
        spec's ``request_model`` when it declares one; methods whose request
        model is ``None`` take no arguments.
        """
        spec = self.spec_for(name)
        handler = getattr(self, spec.handler)
        if spec.request_model is None:
            result = handler()
        elif spec.request_model is dict:
            result = handler(payload or {})
        else:
            result = handler(spec.request_model(**(payload or {})))
        if inspect.isawaitable(result):
            result = await result
        return result


class Channel(ABC):
    """A connection to one peer service, usable from sync and async code."""

    @abstractmethod
    async def acall(
        self, method: str, payload: dict[str, Any], *, http_method: str = "post"
    ) -> Any:
        """Await one call to the peer and return its decoded JSON payload."""

    @abstractmethod
    def call(
        self, method: str, payload: dict[str, Any], *, http_method: str = "post"
    ) -> Any:
        """Blocking variant, for use from the synchronous mechanism loop."""

    def close(self) -> None:  # pragma: no cover - default no-op
        """Release any held connections."""


class CallableChannel(Channel):
    """Adapts a plain ``Callable[[path, payload], dict]`` to :class:`Channel`.

    This is the pre-existing ``transport=`` seam that ``SAORemoteNegotiator``
    and ``RemoteUtilityFunction`` already accept and that the parity tests
    already inject. Wrapping it here means those call sites and tests keep
    working untouched while everything else moves to channels.
    """

    def __init__(self, fn: Callable[[str, dict[str, Any]], Any]):
        self._fn = fn

    def call(
        self, method: str, payload: dict[str, Any], *, http_method: str = "post"
    ) -> Any:
        return self._fn("/" + method.lstrip("/"), payload)

    async def acall(
        self, method: str, payload: dict[str, Any], *, http_method: str = "post"
    ) -> Any:
        result = self._fn("/" + method.lstrip("/"), payload)
        if inspect.isawaitable(result):
            return await result
        return result


# --------------------------------------------------------------------------- #
# transport registry
# --------------------------------------------------------------------------- #
# protocol -> either a Channel factory, or a "module.path:ClassName" string that
# is imported on first use. The string form is what keeps optional dependencies
# optional: nothing imports grpc until a grpc peer is actually addressed.
_FACTORIES: dict[str, Callable[[Address], Channel] | str] = {}
_INSTALL_HINTS: dict[str, str] = {}
_CHANNELS: dict[tuple[str, str, int], Channel] = {}


def register_transport(
    protocol: str,
    factory: Callable[[Address], Channel] | str,
    *,
    install_hint: str = "",
) -> None:
    """Register a channel factory for ``protocol``.

    ``factory`` may be a ``"package.module:ClassName"`` string, which is
    imported lazily the first time that protocol is used. ``install_hint`` is
    shown when that import fails, so a missing optional dependency produces one
    actionable message instead of a raw ``ImportError``.
    """
    _FACTORIES[protocol] = factory
    if install_hint:
        _INSTALL_HINTS[protocol] = install_hint


def known_protocols() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


def _resolve(protocol: str) -> Callable[[Address], Channel]:
    try:
        factory = _FACTORIES[protocol]
    except KeyError:
        raise TransportError(
            f"unknown protocol {protocol!r}; known: {', '.join(known_protocols())}"
        ) from None
    if not isinstance(factory, str):
        return factory
    module_name, _, attr = factory.partition(":")
    try:
        module = import_module(module_name)
    except ImportError as e:
        hint = _INSTALL_HINTS.get(protocol, "")
        raise TransportUnavailable(
            f"protocol {protocol!r} is not available: {e}."
            + (f" {hint}" if hint else "")
        ) from e
    resolved = getattr(module, attr)
    _FACTORIES[protocol] = resolved
    return resolved


def channel_for(address: Address) -> Channel:
    """Return a cached channel to ``address``, creating it on first use.

    Caching is what gives connection reuse: before this existed, the per-round
    ``respond`` call built a fresh ``httpx.Client`` — and therefore a fresh TCP
    connection — on every single round.
    """
    channel = _CHANNELS.get(address.key)
    if channel is None:
        channel = _resolve(address.protocol)(address)
        _CHANNELS[address.key] = channel
    return channel


def close_all_channels() -> None:
    """Close and forget every cached channel (used by tests and shutdown)."""
    for channel in list(_CHANNELS.values()):
        try:
            channel.close()
        except Exception:  # pragma: no cover - best effort
            pass
    _CHANNELS.clear()


# --------------------------------------------------------------------------- #
# server registry (the serving half; same lazy-by-string rule)
# --------------------------------------------------------------------------- #
_SERVERS: dict[str, Callable[..., None] | str] = {}


def register_server(
    protocol: str, factory: Callable[..., None] | str, *, install_hint: str = ""
) -> None:
    """Register the function that serves a :class:`Service` over ``protocol``."""
    _SERVERS[protocol] = factory
    if install_hint:
        _INSTALL_HINTS.setdefault(protocol, install_hint)


def serve_service(
    service: "Service", *, host: str = "0.0.0.0", port: int = 0, protocol: str = "http", **kwargs
) -> None:
    """Block serving ``service`` over ``protocol``."""
    try:
        factory = _SERVERS[protocol]
    except KeyError:
        raise TransportError(
            f"no server registered for protocol {protocol!r};"
            f" known: {', '.join(sorted(_SERVERS))}"
        ) from None
    if isinstance(factory, str):
        module_name, _, attr = factory.partition(":")
        try:
            module = import_module(module_name)
        except ImportError as e:
            hint = _INSTALL_HINTS.get(protocol, "")
            raise TransportUnavailable(
                f"serving protocol {protocol!r} is not available: {e}."
                + (f" {hint}" if hint else "")
            ) from e
        factory = getattr(module, attr)
        _SERVERS[protocol] = factory
    return factory(service, host=host, port=port, **kwargs)


# The HTTP transport is registered eagerly: httpx and fastapi are hard
# dependencies, so there is nothing optional to defer. gRPC is registered by
# string path (see negmasd.transport.__init__) precisely because grpcio is not.
register_transport("http", "negmasd.transport.http:HttpChannel")
register_server("http", "negmasd.transport.http:serve_http")


def to_jsonable(obj: Any) -> Any:
    """Coerce a handler's return value to plain JSON types.

    Handlers legitimately return pydantic models, dicts of models, and negmas
    objects (``/peak`` hands back whole config models and ``TraceElement``s).
    The FastAPI adapter gets this for free; other adapters call this so that
    every transport produces byte-identical payloads.
    """
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(obj)
