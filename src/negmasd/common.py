from typing import Any, overload, TypeVar
import httpx
import requests
from pydantic import BaseModel, ValidationError
from rich import print
from negmasd.constants import (
    SUCCESS,
    FAILURE,
    RUNNER_DEFAULT_PORT,
    AGENT_BASE_PORT,
)
from negmasd.models import ResponseModel

__all__ = [
    "SUCCESS",
    "FAILURE",
    "get_response",
    "RUNNER_DEFAULT_PORT",
    "AGENT_BASE_PORT",
]

ResponseTypeVar = TypeVar("ResponseTypeVar", bound=BaseModel)


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
    payload = data.model_dump() if isinstance(data, BaseModel) else data
    print(
        f"Will {method} {'to' if method.lower() == 'post' else 'from'} {url} with data:"
    )
    print(payload)
    if "negid" in payload.keys():
        print(f"negid: {payload['negid']}")
    try:
        async with httpx.AsyncClient() as client:
            if method.lower() == "post":
                response = await client.post(url, json=payload, **kwargs)
            else:
                response = await client.get(url, params=payload, **kwargs)
        # 4. Check for HTTP errors (e.g., 404 Not Found, 500 Internal Server Error).
        print(f"received {response}")
        response.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        print(f"\n❌ Connection Error: Could not connect to {url}.")
        print("   Please make sure your FastAPI application is running and accessible.")
        return ResponseModel(status=FAILURE, message=str(e))
    except requests.exceptions.HTTPError as e:
        print(f"\n❌ HTTP Error: {e}")
        print("   The endpoint returned an error status code. Check your server logs.")
        return ResponseModel(status=FAILURE, message=str(e))
    except requests.exceptions.RequestException as e:
        print(f"\n❌ An unexpected request error occurred: {e}")
        return ResponseModel(status=FAILURE, message=str(e))
    except Exception as e:
        print(f"\n❌ An unexpected exception occurred: {e}")
        return ResponseModel(status=FAILURE, message=str(e))

    # 5. Get the JSON data from the response.

    print(f"JSON: {response.json()}")
    json_data = response.json()
    if response_type:
        try:
            return response_type(**json_data)
        except ValidationError as e:
            print("\n❌ Validation Failed!")
            print("The received JSON data does not match the NegResponseModel.")
            print("\nError details:")
            print(e)
            return ResponseModel(status=FAILURE, message=str(e))
    else:
        return json_data
    # 6. Validate the received data using your response model.
