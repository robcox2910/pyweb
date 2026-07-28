"""Tests for the HTTP server.

The server is the post office. These tests verify request handling
without needing real network connections.
"""

import socket

from pyweb.request import Request
from pyweb.response import Response, text_response
from pyweb.router import Router
from pyweb.server import Server, _read_request

STATUS_200 = 200
STATUS_404 = 404
STATUS_500 = 500
DEFAULT_PORT = 8000
LARGE_BODY_SIZE = 5000


def _make_server() -> Server:
    """Create a test server with simple routes."""
    router = Router()
    router.add_route("GET", "/", lambda _r: text_response("home"))

    def _raise_error(_r: Request) -> Response:
        """Raise an error to test 500 handling."""
        msg = "boom"
        raise ValueError(msg)

    router.add_route("GET", "/error", _raise_error)
    router.add_route("POST", "/echo", lambda r: text_response(r.body))
    return Server(router)


class TestHandleRequest:
    """Verify request handling (no network needed)."""

    def test_valid_request(self) -> None:
        """A valid GET request should return the handler's response."""
        server = _make_server()
        raw = server.handle_request("GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert b"200 OK" in raw
        assert b"home" in raw

    def test_not_found(self) -> None:
        """A request to an unknown path should return 404."""
        server = _make_server()
        raw = server.handle_request("GET /missing HTTP/1.1\r\n\r\n")
        assert b"404" in raw

    def test_malformed_request_returns_400(self) -> None:
        """A malformed request is the client's fault -> 400 Bad Request."""
        server = _make_server()
        raw = server.handle_request("GARBAGE")
        assert b"400" in raw
        assert b"Bad Request" in raw

    def test_empty_request_returns_400(self) -> None:
        """An empty/whitespace request should return 400, not 500."""
        server = _make_server()
        raw = server.handle_request("   ")
        assert b"400" in raw

    def test_handler_exception_returns_500(self) -> None:
        """A handler that raises should return 500 without leaking details."""
        server = _make_server()
        raw = server.handle_request("GET /error HTTP/1.1\r\n\r\n")
        assert b"500" in raw
        assert b"boom" not in raw  # Exception details should NOT leak.

    def test_server_properties(self) -> None:
        """Server should expose host and port."""
        server = _make_server()
        assert server.host == "127.0.0.1"
        assert server.port == DEFAULT_PORT


class TestReadRequest:
    """Verify full-request reading over a socket (honors Content-Length)."""

    def test_reads_body_larger_than_buffer(self) -> None:
        """A body bigger than one recv buffer must not be truncated."""
        client, server_side = socket.socketpair()
        try:
            body = "x" * LARGE_BODY_SIZE
            request = (
                f"POST /echo HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"\r\n"
                f"{body}"
            )
            client.sendall(request.encode("utf-8"))
            client.close()

            raw = _read_request(server_side)
        finally:
            client.close()
            server_side.close()

        assert raw.endswith(body)

        # And end-to-end: the echo handler should see the whole body.
        response = _make_server().handle_request(raw)
        assert body.encode("utf-8") in response

    def test_handles_closed_connection(self) -> None:
        """A client that closes before sending headers yields empty text."""
        client, server_side = socket.socketpair()
        client.close()
        try:
            raw = _read_request(server_side)
        finally:
            server_side.close()
        assert raw == ""
