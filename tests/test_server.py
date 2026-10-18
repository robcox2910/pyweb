"""Tests for the HTTP server.

The server is the post office. These tests verify request handling
without needing real network connections.
"""

import socket
import struct
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from pyweb.request import Request
from pyweb.response import Response, text_response
from pyweb.router import Router
from pyweb.server import Server, _read_request

STATUS_200 = 200
STATUS_404 = 404
STATUS_500 = 500
DEFAULT_PORT = 8000
LARGE_BODY_SIZE = 5000
QUICK_CLIENT_TIMEOUT = 0.3  # seconds a silent client may hog the server in tests
WAIT_SECONDS = 5.0


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


@contextmanager
def _running_server() -> Iterator[Server]:
    """Run a real server on a free port in a background thread."""
    router = Router()
    router.add_route("GET", "/", lambda _r: text_response("home"))
    server = Server(router, port=0, client_timeout=QUICK_CLIENT_TIMEOUT)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.ready.wait(WAIT_SECONDS), "server never started listening"
    try:
        yield server
    finally:
        server.stop()
        thread.join(WAIT_SECONDS)
        assert not thread.is_alive(), "server did not stop"


def _send(server: Server, raw: bytes) -> bytes:
    """Send raw bytes as one client and return everything the server replies."""
    with socket.create_connection((server.host, server.port), timeout=WAIT_SECONDS) as client:
        client.sendall(raw)
        chunks: list[bytes] = []
        while chunk := client.recv(4096):
            chunks.append(chunk)
        return b"".join(chunks)


def _get_home(server: Server) -> bytes:
    """Fetch ``/`` as a well-behaved client would."""
    return _send(server, b"GET / HTTP/1.1\r\nHost: test\r\n\r\n")


class TestServerSurvivesBadClients:
    """One misbehaving visitor must never close the post office for everyone."""

    def test_client_that_hangs_up_mid_request_does_not_crash_the_server(self) -> None:
        """Regression: a connection reset used to escape serve_forever and kill it."""
        with _running_server() as server:
            rude = socket.create_connection((server.host, server.port))
            rude.sendall(b"GET / HTTP/1.1\r\n")
            # SO_LINGER 0 makes close() send a reset instead of a polite goodbye.
            rude.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            rude.close()
            assert b"200 OK" in _get_home(server)

    def test_silent_client_does_not_freeze_the_server(self) -> None:
        """Regression: a client that never spoke used to block everyone forever."""
        with _running_server() as server, socket.create_connection((server.host, server.port)):
            assert b"200 OK" in _get_home(server)

    def test_huge_headers_are_refused_with_431(self) -> None:
        """Headers that never end are cut off instead of eating all memory."""
        with _running_server() as server:
            reply = _send(server, b"GET / HTTP/1.1\r\nX-Junk: " + b"a" * 100_000)
            assert reply.startswith(b"HTTP/1.1 431")
            assert b"200 OK" in _get_home(server)

    def test_huge_body_is_refused_with_413(self) -> None:
        """A body bigger than the limit is refused before we try to read it."""
        with _running_server() as server:
            reply = _send(
                server,
                b"POST / HTTP/1.1\r\nContent-Length: 999999999\r\n\r\nhi",
            )
            assert reply.startswith(b"HTTP/1.1 413")

    @pytest.mark.parametrize("length", ["0\x0b", "1_0", "²"])
    def test_odd_content_length_is_refused_with_400(self, length: str) -> None:
        """Only plain ASCII digits, padded with spaces or tabs, make a Content-Length.

        Regression: ``int(value.strip())`` accepted a vertical tab, an
        underscore and a superscript two, the same smuggling bug the async
        server's ``body_length`` had.
        """
        with _running_server() as server:
            head = f"GET / HTTP/1.1\r\nContent-Length: {length}\r\n\r\n"
            reply = _send(server, head.encode())
            assert reply.startswith(b"HTTP/1.1 400")

    def test_content_length_padded_with_a_tab_is_fine(self) -> None:
        """Spaces and tabs around the number are allowed padding."""
        with _running_server() as server:
            reply = _send(server, b"GET / HTTP/1.1\r\nContent-Length:\t0 \r\n\r\n")
            assert reply.startswith(b"HTTP/1.1 200")

    def test_conflicting_duplicate_content_lengths_are_refused_with_400(self) -> None:
        """Two Content-Length headers that disagree are ambiguous framing, so refuse.

        Regression: the legacy server used the first one while ``parse_request``
        kept the last, so the two parsers could disagree about where the body ends.
        """
        with _running_server() as server:
            head = b"GET / HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 5\r\n\r\nhello"
            assert _send(server, head).startswith(b"HTTP/1.1 400")

    def test_identical_duplicate_content_lengths_are_accepted(self) -> None:
        """Repeating the same length is harmless, as in ``http1.body_length``."""
        with _running_server() as server:
            head = b"GET / HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n"
            assert _send(server, head).startswith(b"HTTP/1.1 200")

    def test_comma_list_of_equal_content_lengths_is_accepted(self) -> None:
        """``0, 0`` is one length said twice, which ``body_length`` also accepts."""
        with _running_server() as server:
            reply = _send(server, b"GET / HTTP/1.1\r\nContent-Length: 0, 0\r\n\r\n")
            assert reply.startswith(b"HTTP/1.1 200")

    def test_comma_list_of_different_content_lengths_is_refused(self) -> None:
        """``0, 5`` is ambiguous framing, so refuse it."""
        with _running_server() as server:
            reply = _send(server, b"GET / HTTP/1.1\r\nContent-Length: 0, 5\r\n\r\n")
            assert reply.startswith(b"HTTP/1.1 400")

    @pytest.mark.parametrize("second", ["0\x0b", "²"])
    def test_bad_second_duplicate_content_length_is_refused(self, second: str) -> None:
        """Every repeated Content-Length must be plain digits, not just the first."""
        with _running_server() as server:
            head = f"GET / HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: {second}\r\n\r\n"
            assert _send(server, head.encode()).startswith(b"HTTP/1.1 400")

    def test_port_zero_picks_a_free_port(self) -> None:
        """Asking for port 0 lets the operating system pick, and we report it."""
        with _running_server() as server:
            assert server.port != 0
