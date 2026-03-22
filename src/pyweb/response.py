"""HTTP response building -- writing the reply letter.

A response is the server's reply to a request. It includes a status
code (did it work?), headers (info about the reply), and a body
(the actual content).
"""

from dataclasses import dataclass, field
from enum import IntEnum


class StatusCode(IntEnum):
    """Common HTTP status codes."""

    OK = 200
    CREATED = 201
    NO_CONTENT = 204
    MOVED = 301
    FOUND = 302
    BAD_REQUEST = 400
    NOT_FOUND = 404
    METHOD_NOT_ALLOWED = 405
    INTERNAL_ERROR = 500


# Human-readable names for status codes.
STATUS_PHRASES: dict[int, str] = {
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
}


@dataclass
class Response:
    """An HTTP response to send back to the client.

    Args:
        status: The HTTP status code.
        headers: Response headers.
        body: The response body.

    """

    status: int = StatusCode.OK
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""

    def set_content_type(self, content_type: str) -> None:
        """Set the Content-Type header.

        Args:
            content_type: The MIME type (e.g., "text/html").

        """
        self.headers["Content-Type"] = content_type

    def to_bytes(self) -> bytes:
        """Serialize the response to raw HTTP bytes.

        Returns:
            The complete HTTP response as bytes.

        """
        phrase = STATUS_PHRASES.get(self.status, "Unknown")
        status_line = f"HTTP/1.1 {self.status} {phrase}"

        # Auto-set Content-Length.
        body_bytes = self.body.encode("utf-8")
        self.headers["Content-Length"] = str(len(body_bytes))

        header_lines = [f"{k}: {v}" for k, v in self.headers.items()]
        head = "\r\n".join([status_line, *header_lines])
        return head.encode("utf-8") + b"\r\n\r\n" + body_bytes


def html_response(body: str, status: int = StatusCode.OK) -> Response:
    """Create an HTML response.

    Args:
        body: The HTML content.
        status: The HTTP status code.

    Returns:
        A Response with Content-Type set to text/html.

    """
    resp = Response(status=status, body=body)
    resp.set_content_type("text/html; charset=utf-8")
    return resp


def json_response(body: str, status: int = StatusCode.OK) -> Response:
    """Create a JSON response.

    Args:
        body: The JSON string.
        status: The HTTP status code.

    Returns:
        A Response with Content-Type set to application/json.

    """
    resp = Response(status=status, body=body)
    resp.set_content_type("application/json")
    return resp


def text_response(body: str, status: int = StatusCode.OK) -> Response:
    """Create a plain text response.

    Args:
        body: The text content.
        status: The HTTP status code.

    Returns:
        A Response with Content-Type set to text/plain.

    """
    resp = Response(status=status, body=body)
    resp.set_content_type("text/plain")
    return resp


def not_found(message: str = "Not Found") -> Response:
    """Create a 404 Not Found response."""
    return html_response(f"<h1>404</h1><p>{message}</p>", status=StatusCode.NOT_FOUND)
