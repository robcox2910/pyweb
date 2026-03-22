"""HTTP request parsing -- reading the incoming letter.

An HTTP request is like a letter arriving at the post office. It has
an address (the path), a purpose (the method), and extra notes
(headers). This module parses raw HTTP text into a structured object.

How HTTP requests look:

    GET /about?page=2 HTTP/1.1
    Host: example.com
    Accept: text/html

    (body goes here for POST requests)

The parser splits this into method, path, headers, query parameters,
and body -- everything a handler needs to build a response.
"""

import json
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import unquote_plus

from pyweb.errors import ParseError

# HTTP methods we support.
VALID_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH"})


@dataclass(frozen=True)
class Request:
    """A parsed HTTP request.

    Args:
        method: The HTTP method (GET, POST, etc.).
        path: The requested URL path (e.g., "/about").
        headers: Header name-value pairs.
        body: The request body (empty for GET requests).
        query_params: Parsed query string parameters.
        params: Path parameters from dynamic routes (e.g., {"id": "42"}).

    """

    method: str
    path: str
    headers: dict[str, str] = field(default_factory=lambda: {})
    body: str = ""
    query_params: dict[str, str] = field(default_factory=lambda: {})
    params: dict[str, str] = field(default_factory=lambda: {})

    def json(self) -> dict[str, object]:
        """Parse the request body as JSON.

        Returns:
            The parsed JSON as a dictionary.

        Raises:
            ValueError: If the body is not valid JSON.

        """
        raw = json.loads(self.body)
        if not isinstance(raw, dict):
            msg = "JSON body must be an object"
            raise TypeError(msg)
        return cast(dict[str, object], raw)


def parse_request(raw: str) -> Request:
    """Parse a raw HTTP request string into a Request object.

    Args:
        raw: The raw HTTP request text.

    Returns:
        A parsed Request.

    Raises:
        ParseError: If the request is malformed.

    """
    if not raw.strip():
        msg = "Empty request"
        raise ParseError(msg)

    # Normalize line endings to \n for consistent parsing.
    normalized = raw.replace("\r\n", "\n")

    # Split headers from body (separated by blank line).
    parts = normalized.split("\n\n", maxsplit=1)
    header_section = parts[0]
    body = parts[1] if len(parts) > 1 else ""

    lines = header_section.split("\n")
    if not lines:
        msg = "Missing request line"
        raise ParseError(msg)

    # Parse the request line: "GET /path HTTP/1.1"
    request_line = lines[0]
    request_parts = request_line.split()
    expected_parts = 3
    if len(request_parts) < expected_parts:
        msg = f"Malformed request line: {request_line!r}"
        raise ParseError(msg)

    method = request_parts[0].upper()
    full_path = request_parts[1]

    if method not in VALID_METHODS:
        msg = f"Unsupported HTTP method: {method!r}"
        raise ParseError(msg)

    # Parse query string from path.
    query_params: dict[str, str] = {}
    path = full_path
    if "?" in full_path:
        path, query_string = full_path.split("?", maxsplit=1)
        for pair in query_string.split("&"):
            if "=" in pair:
                key, value = pair.split("=", maxsplit=1)
                query_params[unquote_plus(key)] = unquote_plus(value)
            elif pair:
                # Bare parameter like ?debug
                query_params[unquote_plus(pair)] = ""

    # Parse headers.
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        if ":" in line:
            key, value = line.split(":", maxsplit=1)
            headers[key.strip()] = value.strip()

    return Request(
        method=method,
        path=path,
        headers=headers,
        body=body,
        query_params=query_params,
    )
