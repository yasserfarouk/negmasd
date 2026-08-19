"""Client-side helpers for calling another negmasd service.

Every outbound call goes through a :class:`~negmasd.transport.base.Channel`, so
the caller never names a protocol. :func:`call_service` is the address-based
form used internally; :func:`get_response` keeps the original URL-based
signature for the CLI and for backwards compatibility.

Transport failures are mapped to ``ResponseModel(status=FAILURE, ...)`` here, in
one place. (The previous implementation caught ``requests.exceptions.*`` while
calling ``httpx``, so only its generic fallback ever fired — a rough edge this
consolidation removes.)
"""

from typing import Any, TypeVar, overload
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError
from rich import print

from negmasd.constants import (
    SUCCESS,
    FAILURE,
    DEFAULT_PROTOCOL,
    RUNNER_DEFAULT_PORT,
    AGENT_BASE_PORT,
)
from negmasd.models import ResponseModel
from negmasd.transport import Address, TransportError, channel_for

__all__ = [
    "SUCCESS",
    "FAILURE",
    "call_service",
    "get_response",
    "RUNNER_DEFAULT_PORT",
    "AGENT_BASE_PORT",
]

ResponseTypeVar = TypeVar("ResponseTypeVar", bound=BaseModel)


def _as_payload(data: BaseModel | dict[str, Any]) -> dict[str, Any]:
    return data.model_dump() if isinstance(data, BaseModel) else data


def _validate(
    json_data: Any, response_type: type[ResponseTypeVar] | None
) -> Any:
    if not response_type:
        return json_data
    try:
        return response_type(**json_data)
    except ValidationError as e:
        print("\n❌ Validation Failed!")
        print(f"The received JSON data does not match {response_type.__name__}.")
        print("\nError details:")
        print(e)
        return ResponseModel(status=FAILURE, message=str(e))


async def call_service(
    address: Address,
    method: str,
    data: BaseModel | dict[str, Any],
    *,
    http_method: str = "post",
    response_type: type[ResponseTypeVar] | None = None,
) -> Any:
    """Call ``method`` on the service at ``address`` over its own protocol."""
    payload = _as_payload(data)
    print(f"Will call {method} on {address} with data:")
    print(payload)
    if "negid" in payload.keys():
        print(f"negid: {payload['negid']}")
    try:
        json_data = await channel_for(address).acall(
            method, payload, http_method=http_method
        )
    except TransportError as e:
        print(f"\n❌ Transport error calling {method} on {address}: {e}")
        return ResponseModel(status=FAILURE, message=str(e))
    except Exception as e:
        print(f"\n❌ An unexpected exception occurred: {e}")
        return ResponseModel(status=FAILURE, message=str(e))
    print(f"JSON: {json_data}")
    return _validate(json_data, response_type)


@overload
async def get_response(
    url: str,
    data: BaseModel | dict[str, Any],
    *,
    method="post",
    response_type: None = None,
) -> dict[str, Any] | ResponseModel: ...


@overload
async def get_response(
    url: str,
    data: BaseModel | dict[str, Any],
    *,
    method="post",
    response_type: type[ResponseTypeVar],
) -> ResponseTypeVar | ResponseModel: ...


async def get_response(
    url: str,
    data: BaseModel | dict[str, Any],
    *,
    method="post",
    response_type: type[ResponseTypeVar] | None = None,
    **kwargs,
) -> dict[str, Any] | ResponseTypeVar | ResponseModel:
    """URL-based wrapper over :func:`call_service`.

    The URL's scheme selects the protocol, so ``grpc://host:port/method`` works
    exactly as ``http://host:port/method`` does.
    """
    parts = urlsplit(url)
    address = Address(
        host=parts.hostname or "127.0.0.1",
        port=parts.port or 0,
        protocol=parts.scheme or DEFAULT_PROTOCOL,
    )
    return await call_service(
        address,
        parts.path,
        data,
        http_method=method,
        response_type=response_type,
    )
