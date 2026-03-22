"""Tests for the HTTP server.

The server is the post office. These tests verify request handling
without needing real network connections.
"""

from pyweb.response import StatusCode, text_response
from pyweb.router import Router
from pyweb.server import Server

STATUS_200 = 200
STATUS_500 = 500


def _make_server() -> Server:
    """Create a test server with a simple route."""
    router = Router()
    router.add_route("GET", "/", lambda r: text_response("home"))
    router.add_route("GET", "/error", lambda r: (_ for _ in ()).throw(ValueError("boom")))
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

    def test_malformed_request(self) -> None:
        """A malformed request should return 500."""
        server = _make_server()
        raw = server.handle_request("GARBAGE")
        assert b"500" in raw

    def test_server_properties(self) -> None:
        """Server should expose host and port."""
        server = _make_server()
        assert server.host == "127.0.0.1"
        assert server.port == 8000  # noqa: PLR2004
