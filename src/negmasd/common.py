from typing import Any, overload, TypeVar
import requests
from pydantic import BaseModel, ValidationError
from negmasd.models import ResponseModel

__all__ = ["SUCCESS", "FAILURE", "get_response"]
SUCCESS = "success"
FAILURE = "failure"

ResponseTypeVar = TypeVar("ResponseTypeVar", bound=BaseModel)


@overload
def get_response(
    url: str, data: BaseModel, response_type: None = None
) -> dict[str, Any] | ResponseModel: ...


@overload
def get_response(
    url: str, data: BaseModel, response_type: type[ResponseTypeVar]
) -> ResponseTypeVar | ResponseModel: ...


def get_response(
    url: str, data: BaseModel, response_type: type[ResponseTypeVar] | None = None
) -> dict[str, Any] | ResponseTypeVar | ResponseModel:
    payload = data.model_dump_json()
    try:
        print(f"Will post to {url} with payload:\n{payload}\n")
        response = requests.post(url, json=payload, timeout=5)

        # 4. Check for HTTP errors (e.g., 404 Not Found, 500 Internal Server Error).
        response.raise_for_status()
        # 5. Get the JSON data from the response.
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
