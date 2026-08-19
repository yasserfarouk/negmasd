"""Pluggable transports for negmasd.

Service logic lives in :class:`~negmasd.transport.base.Service` subclasses that
declare a *method table*; each transport is an adapter that projects that table
onto a wire protocol. Adding a protocol therefore never touches negotiation
logic, and the protocols cannot drift apart because they read the same table.

``http`` (FastAPI + httpx) is always available. ``grpc`` is **optional**: it is
registered here only as a module-path string, so ``grpcio`` is imported the
first moment a gRPC peer is actually addressed and never otherwise. Everything
on the HTTP path works with ``grpcio`` absent.
"""

from .base import (
    Address,
    CallableChannel,
    Channel,
    MethodSpec,
    Service,
    TransportError,
    TransportUnavailable,
    channel_for,
    close_all_channels,
    known_protocols,
    register_server,
    register_transport,
    serve_service,
    to_jsonable,
)
from .http import HttpChannel, build_app, serve_http

# Registered by string, never imported here: see module docstring.
_GRPC_HINT = "Install the optional extra with `uv sync --extra grpc`."
register_transport(
    "grpc", "negmasd.transport.grpc:GrpcChannel", install_hint=_GRPC_HINT
)
register_server("grpc", "negmasd.transport.grpc:serve_grpc", install_hint=_GRPC_HINT)

__all__ = [
    "Address",
    "CallableChannel",
    "Channel",
    "HttpChannel",
    "MethodSpec",
    "Service",
    "TransportError",
    "TransportUnavailable",
    "build_app",
    "channel_for",
    "register_server",
    "serve_http",
    "serve_service",
    "close_all_channels",
    "known_protocols",
    "register_transport",
    "to_jsonable",
]
