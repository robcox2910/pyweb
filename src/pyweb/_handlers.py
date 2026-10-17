"""Helpers the App uses to turn handler results and failures into responses.

Kept apart from ``app.py`` so that file stays about routes and middleware.
Private to PyWeb: import from ``pyweb.app`` or the package instead.
"""

import logging
from typing import cast

from pyweb.messages import (
    Headers,
    HTTPError,
    HttpRequest,
    HttpResponse,
    JsonValue,
    Outcome,
    StreamingResponse,
    html,
    json,
)

logger = logging.getLogger("pyweb.app")

_METHOD_NOT_ALLOWED = 405
_SERVER_ERROR = 500
_UPGRADE_REQUIRED = 426
_ERROR_PAGE = (
    "<!doctype html><title>500 Internal Server Error</title><h1>500 Internal Server Error</h1>"
)


def server_error(request: HttpRequest) -> HttpResponse:
    """Build the generic 500 answer, with no details for the client."""
    if request.wants_html:
        return html(_ERROR_PAGE, _SERVER_ERROR)
    return json({"error": "Internal Server Error"}, _SERVER_ERROR)


def coerce(result: object, request: HttpRequest, *, source: str = "Handler") -> Outcome:
    """Turn a handler's return value into a response.

    Args:
        result: What the handler (or middleware) gave back.
        request: The request being answered.
        source: Who produced ``result``, for the log message.

    Returns:
        The response itself, JSON for a ``dict``/``list``, HTML for a
        ``str``, or a logged generic 500 for anything else.

    """
    if isinstance(result, HttpResponse | StreamingResponse):
        return result
    if isinstance(result, dict | list):
        return json(cast("JsonValue", result))
    if isinstance(result, str):
        return html(result)
    logger.error(
        "%s for %s %s returned unsupported type %s",
        source,
        request.method,
        request.path,
        type(result).__name__,
    )
    return server_error(request)


def without_body(response: HttpResponse) -> HttpResponse:
    """Copy a response for HEAD: same headers plus the GET length, no body.

    The handler's own object is left alone, in case it is shared.
    """
    headers = Headers(response.headers.items())
    headers.set("Content-Length", str(len(response.body)))
    return HttpResponse(response.status, headers)


class MethodNotAllowedError(HTTPError):
    """A 405 that remembers which methods would have worked."""

    def __init__(self, allowed: list[str]) -> None:
        """Store the allowed methods."""
        super().__init__(_METHOD_NOT_ALLOWED)
        self.allowed = allowed

    def to_response(self, request: HttpRequest) -> HttpResponse:
        """Render the 405 with an ``Allow`` header."""
        response = super().to_response(request)
        response.headers.set("Allow", ", ".join(self.allowed))
        return response


class UpgradeRequiredError(HTTPError):
    """A 426: this path only speaks WebSocket."""

    def __init__(self) -> None:
        """Set the status."""
        super().__init__(_UPGRADE_REQUIRED)

    def to_response(self, request: HttpRequest) -> HttpResponse:
        """Render the 426 with the ``Upgrade`` header saying what to ask for."""
        response = super().to_response(request)
        response.headers.set("Upgrade", "websocket")
        return response
