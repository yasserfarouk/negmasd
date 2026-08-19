"""The gRPC transport: an optional second protocol for every negmasd service.

Importing this module requires ``grpcio`` (``uv sync --extra grpc``). Nothing on
the HTTP path imports it — the registry in :mod:`negmasd.transport.base` holds
only a module-path *string* for this protocol, resolved the first time a gRPC
peer is actually addressed.

Shape of the two halves:

* :class:`GrpcChannel` (client) keeps one long-lived HTTP/2 channel. Its sync
  ``call`` serves the hot per-round ``respond`` path directly from the
  mechanism's synchronous run loop; ``acall`` offloads the same blocking stub to
  a worker thread, which keeps us free of event-loop-bound ``grpc.aio`` channels
  for the handful of handshake calls that need awaiting.
* :func:`serve_grpc` / :class:`GrpcServerHandle` (server) run a ``grpc.aio``
  server. Async is required, not merely convenient: ``RunnerService`` answers
  ``run-negotiation`` with ``asyncio.create_task``, which needs a live event loop
  in the serving thread.

Dispatch reads the very same :class:`~negmasd.transport.base.MethodSpec` table
the FastAPI adapter reads, so the two protocols cannot drift apart.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import grpc

from . import negmasd_pb2, negmasd_pb2_grpc
from .base import Address, Channel, Service, TransportError, to_jsonable

__all__ = ["GrpcChannel", "GrpcServerHandle", "serve_grpc", "target_of"]

# Negotiation configs carry whole serialized outcome spaces and ufuns, which can
# comfortably exceed gRPC's 4 MiB default.
_MAX_MESSAGE = 64 * 1024 * 1024
_CHANNEL_OPTIONS = [
    ("grpc.max_send_message_length", _MAX_MESSAGE),
    ("grpc.max_receive_message_length", _MAX_MESSAGE),
]


def target_of(address: Address) -> str:
    return f"{address.host}:{address.port}"


def _dumps(payload: dict[str, Any] | None) -> str:
    """JSON-encode a request payload.

    Fast path first: most payloads are already plain. Anything exotic (numpy
    scalars from a generated outcome space, pydantic models) goes through the
    same encoder the FastAPI adapter uses, so both protocols put identical bytes
    on the wire.
    """
    if not payload:
        return "{}"
    try:
        return json.dumps(payload)
    except (TypeError, ValueError):
        return json.dumps(to_jsonable(payload))


class GrpcChannel(Channel):
    """A channel to one peer over gRPC."""

    def __init__(self, address: Address, timeout: float = 60.0):
        self.address = address
        self.target = target_of(address)
        self.timeout = timeout
        self._channel: grpc.Channel | None = None
        self._stub: Any = None
        self._lock = threading.Lock()

    def _get_stub(self) -> Any:
        # One HTTP/2 channel for the channel object's lifetime; the per-round
        # respond call rides on it instead of reconnecting each time.
        if self._stub is None:
            with self._lock:
                if self._stub is None:
                    self._channel = grpc.insecure_channel(
                        self.target, options=_CHANNEL_OPTIONS
                    )
                    self._stub = negmasd_pb2_grpc.NegmasdStub(self._channel)
        return self._stub

    def close(self) -> None:
        with self._lock:
            if self._channel is not None:
                self._channel.close()
            self._channel = None
            self._stub = None

    def call(
        self, method: str, payload: dict[str, Any], *, http_method: str = "post"
    ) -> Any:
        # http_method is meaningless here: gRPC has no GET/POST distinction. It
        # is accepted so that callers stay protocol-agnostic.
        # Passed through verbatim rather than stripped: the service's spec index
        # holds both spellings (name and route), so "respond", "/respond" and the
        # root's "/" all resolve. Stripping would turn "/" into "", which matches
        # nothing -- the same name/path asymmetry the HTTP alias guards against.
        request = negmasd_pb2.CallRequest(method=method, payload=_dumps(payload))
        try:
            reply = self._get_stub().Call(request, timeout=self.timeout)
        except grpc.RpcError as e:
            raise TransportError(
                f"gRPC call {method!r} to {self.target} failed: {e}"
            ) from e
        if reply.error:
            raise TransportError(
                f"gRPC call {method!r} to {self.target} failed remotely: {reply.error}"
            )
        return json.loads(reply.payload) if reply.payload else None

    async def acall(
        self, method: str, payload: dict[str, Any], *, http_method: str = "post"
    ) -> Any:
        # Deliberately the blocking stub on a worker thread rather than a second
        # grpc.aio channel: these calls happen a few times per negotiation, and
        # an aio channel is bound to the loop that created it.
        return await asyncio.to_thread(
            self.call, method, payload, http_method=http_method
        )


class _Servicer(negmasd_pb2_grpc.NegmasdServicer):
    """Projects a service's method table onto the single generic ``Call`` RPC."""

    def __init__(self, service: Service):
        self._service = service

    async def Call(self, request, context):  # noqa: N802 - gRPC naming
        try:
            payload = json.loads(request.payload) if request.payload else {}
        except ValueError as e:
            return negmasd_pb2.CallReply(error=f"malformed JSON payload: {e}")
        try:
            result = await self._service.dispatch(request.method, payload)
        except KeyError:
            return negmasd_pb2.CallReply(
                error=f"unknown method {request.method!r}"
            )
        except Exception as e:  # surface as a reply, not a torn stream
            return negmasd_pb2.CallReply(error=f"{type(e).__name__}: {e}")
        return negmasd_pb2.CallReply(payload=json.dumps(to_jsonable(result)))


class GrpcServerHandle:
    """A gRPC server for one service, running its own loop in its own thread.

    Owning a thread (rather than assuming the caller has a loop) is what lets
    the same object back both the blocking CLI command and in-process tests.
    """

    def __init__(
        self,
        service: Service,
        *,
        host: str = "0.0.0.0",
        port: int = 0,
        grace: float = 5.0,
    ):
        self._service = service
        self._host = host
        self._requested_port = port
        self._grace = grace
        self.port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    async def _boot(self) -> None:
        self._server = grpc.aio.server(options=_CHANNEL_OPTIONS)
        negmasd_pb2_grpc.add_NegmasdServicer_to_server(
            _Servicer(self._service), self._server
        )
        # Returns the actually-bound port, so port=0 works for tests.
        self.port = self._server.add_insecure_port(f"{self._host}:{self._requested_port}")
        await self._server.start()

    def start(self, timeout: float = 30.0) -> int:
        """Start serving in the background and return the bound port."""

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(self._boot())
            except BaseException as e:  # pragma: no cover - startup failure
                self._error = e
                self._ready.set()
                return
            self._ready.set()
            loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True, name="negmasd-grpc")
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise TransportError("gRPC server did not start in time")
        if self._error is not None:
            raise TransportError(f"gRPC server failed to start: {self._error}")
        assert self.port is not None
        return self.port

    def wait(self) -> None:
        """Block until the serving thread exits."""
        if self._thread is not None:
            self._thread.join()

    def stop(self, grace: float | None = None) -> None:
        loop, server = self._loop, self._server
        if loop is None or server is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                server.stop(self._grace if grace is None else grace), loop
            ).result(timeout=30)
        except Exception:  # pragma: no cover - best effort shutdown
            pass
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._loop = self._server = self._thread = None


def serve_grpc(
    service: Service, *, host: str = "0.0.0.0", port: int = 0, grace: float = 5.0
) -> None:
    """Block serving ``service`` over gRPC (the CLI's ``--protocol grpc``)."""
    handle = GrpcServerHandle(service, host=host, port=port, grace=grace)
    bound = handle.start()
    print(f"negmasd gRPC server for {type(service).__name__} listening on {host}:{bound}")
    try:
        handle.wait()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        handle.stop()
