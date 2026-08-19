"""The HTTP transport: an httpx-backed channel and a FastAPI server adapter.

This is the reference implementation of the two halves every transport must
provide:

* :class:`HttpChannel` — the client half, with both a sync and an async call
  form. The sync ``httpx.Client`` is held for the channel's lifetime, which is
  what makes the per-round ``respond`` call reuse one TCP connection.
* :func:`build_app` — the server half. It walks a service's method table and
  registers one FastAPI route per :class:`~negmasd.transport.base.MethodSpec`,
  reproducing the paths, verbs and response models the endpoints had when they
  were hand-decorated.
"""

from __future__ import annotations

import inspect
from typing import Annotated, Any, Callable

import httpx
from fastapi import FastAPI, Query

from .base import Address, Channel, MethodSpec, Service, TransportError

__all__ = ["HttpChannel", "build_app", "serve_http", "base_url_of"]


def base_url_of(address: Address) -> str:
    return f"http://{address.host}:{address.port}"


def _other_verb(verb: str) -> str:
    return "post" if verb == "get" else "get"


class HttpChannel(Channel):
    """A channel to one peer over HTTP/1.1."""

    def __init__(self, address: Address, timeout: float = 60.0):
        self.address = address
        self.base_url = base_url_of(address)
        self.timeout = timeout
        self._client: httpx.Client | None = None

    # -- client management --------------------------------------------------- #
    def _sync_client(self) -> httpx.Client:
        # Held for the channel's lifetime: this is the hot path (one call per
        # negotiation round) and it used to open a fresh connection every time.
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _url(self, method: str) -> str:
        return f"{self.base_url}/{method.lstrip('/')}"

    # -- calls --------------------------------------------------------------- #
    def call(
        self, method: str, payload: dict[str, Any], *, http_method: str = "post"
    ) -> Any:
        url = self._url(method)
        client = self._sync_client()
        verb = http_method.lower()
        try:
            response = self._send(client, verb, url, payload)
            if response.status_code == 405:
                # `http_method` is a hint, not a requirement: a caller working
                # against the transport-neutral method table should not have to
                # know which endpoints happen to be GETs over HTTP. gRPC has no
                # such distinction, so requiring it here would leak HTTP into
                # protocol-agnostic call sites.
                verb = _other_verb(verb)
                response = self._send(client, verb, url, payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise TransportError(f"{verb.upper()} {url} failed: {e}") from e

    @staticmethod
    def _send(
        client: httpx.Client, verb: str, url: str, payload: dict[str, Any]
    ) -> httpx.Response:
        if verb == "get":
            return client.get(url, params=payload)
        return client.post(url, json=payload)

    async def acall(
        self, method: str, payload: dict[str, Any], *, http_method: str = "post"
    ) -> Any:
        # An AsyncClient is bound to the event loop that created it, and these
        # calls happen a handful of times per negotiation (not per round), so
        # there is nothing to gain from caching one and a correctness trap in
        # doing so. The sync client above is where connection reuse matters.
        url = self._url(method)
        verb = http_method.lower()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await self._asend(client, verb, url, payload)
                if response.status_code == 405:  # see the note in `call`
                    verb = _other_verb(verb)
                    response = await self._asend(client, verb, url, payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as e:
            raise TransportError(f"{verb.upper()} {url} failed: {e}") from e

    @staticmethod
    async def _asend(
        client: httpx.AsyncClient, verb: str, url: str, payload: dict[str, Any]
    ) -> httpx.Response:
        if verb == "get":
            return await client.get(url, params=payload)
        return await client.post(url, json=payload)


# --------------------------------------------------------------------------- #
# server adapter
# --------------------------------------------------------------------------- #
def _endpoint_for(handler: Callable, spec: MethodSpec) -> Callable:
    """Build a FastAPI endpoint with the signature FastAPI needs to see.

    FastAPI derives a route's parameters from ``inspect.signature``, so the
    wrapper's ``__signature__`` is set explicitly rather than relying on a
    literal annotation (which cannot vary per spec).
    """
    is_async = inspect.iscoroutinefunction(handler)

    if spec.request_model is None:

        async def endpoint():  # type: ignore[misc]
            return await handler() if is_async else handler()

        return endpoint

    async def endpoint(req):  # type: ignore[misc]
        return await handler(req) if is_async else handler(req)

    if spec.http_method.lower() == "get" and spec.request_model is not dict:
        # Bind the model's fields as query parameters, preserving the existing
        # `?path=`, `?neg=`, `?negid=` URLs.
        annotation: Any = Annotated[spec.request_model, Query()]
    else:
        annotation = spec.request_model

    endpoint.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter(
                "req", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation
            )
        ]
    )
    endpoint.__annotations__ = {"req": annotation}
    return endpoint


def build_app(service: Service, **fastapi_kwargs: Any) -> FastAPI:
    """Project a service's method table onto a FastAPI application."""
    app = FastAPI(**fastapi_kwargs)
    for spec in service.method_table:
        handler = getattr(service, spec.handler)
        endpoint = _endpoint_for(handler, spec)
        endpoint.__name__ = spec.handler
        routes = [spec.route]
        # A method whose HTTP path was customised (only the root health check,
        # which must stay at "/") is *also* published under "/<name>". Otherwise
        # a caller working from the transport-neutral method table could reach it
        # over gRPC but 404 over HTTP -- precisely the adapter drift this table
        # exists to prevent. The alias is hidden from /docs.
        alias = "/" + spec.name.lstrip("/")
        if alias != spec.route:
            routes.append(alias)
        for i, route in enumerate(routes):
            app.add_api_route(
                route,
                endpoint,
                methods=[spec.http_method.upper()],
                response_model=spec.response_model,
                name=spec.handler if i == 0 else f"{spec.handler}_alias",
                include_in_schema=(i == 0),
            )
    app.state.service = service
    return app


def serve_http(
    service: Service,
    *,
    host: str = "0.0.0.0",
    port: int = 0,
    app_path: str | None = None,
    **uvicorn_kwargs: Any,
) -> None:
    """Block serving ``service`` over HTTP with uvicorn.

    ``app_path`` lets a caller pass uvicorn an import string (e.g.
    ``"negmasd.agent:app"``) instead of an app object, which is how the CLI has
    always started these services.
    """
    import uvicorn

    uvicorn.run(app_path or build_app(service), host=host, port=port, **uvicorn_kwargs)
