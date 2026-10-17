"""Tests for WebSocket routes: the App's side and the server's side."""

import logging

import pytest
from pynet.loop import Event, run, sleep, spawn
from pynet.loop.testing import FakeClock
from pynet.ws import WebSocket, WebSocketClosed, client_handshake_request, connect_websocket

from pyweb.app import App, CallNext, WebSocketUpgrade
from pyweb.aserver import AsyncServer
from pyweb.http1 import STATUS_REASONS
from pyweb.messages import Headers, HttpRequest, HttpResponse, Outcome, QueryParams, text
from tests.aio.test_aserver import (
    BAD_REQUEST,
    GRACE,
    HOST,
    OK,
    WAIT,
    Reply,
    connect,
    exchange,
    read_reply,
    send,
    serve,
)

UNAUTHORIZED = 401
NOT_FOUND = 404
UPGRADE_REQUIRED = 426
SWITCHING = 101
CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_INTERNAL_ERROR = 1011
POLL = 0.01
KEY = "dGhlIHNhbXBsZSBub25jZQ=="
UPGRADE_HEADERS = [("Upgrade", "websocket"), ("Connection", "Upgrade")]


def upgrade_request(path: str, headers: list[tuple[str, str]] | None = None) -> HttpRequest:
    """Build a GET request that asks to upgrade to a WebSocket."""
    return HttpRequest("GET", path, QueryParams(), Headers(headers or UPGRADE_HEADERS))


def echo_app() -> App:
    """Build an app with an echo WebSocket route that has a path parameter."""
    app = App()

    @app.websocket("/echo/<name>")
    async def echo(ws: WebSocket, request: HttpRequest, name: str) -> None:  # pyright: ignore[reportUnusedFunction]
        """Echo text with the caller's name, and bytes as they are, until they hang up."""
        assert request.path == f"/echo/{name}"
        while True:
            message = await ws.recv()
            if isinstance(message, str):
                await ws.send_text(f"{name}: {message}")
            else:
                await ws.send_bytes(message)

    return app


async def close_code(ws: WebSocket) -> int:
    """Read until the conversation ends and return its close code."""
    try:
        while True:
            await ws.recv()
    except WebSocketClosed as closed:
        return closed.code


class TestAppUpgrade:
    """Test how the App answers upgrade requests (no sockets)."""

    def test_upgrade_request_gives_the_marker(self) -> None:
        """Answer an upgrade for a WebSocket route with a WebSocketUpgrade marker."""
        app = echo_app()
        outcome = run(app.handle(upgrade_request("/echo/sam")), clock=FakeClock(autojump=True))
        assert isinstance(outcome, WebSocketUpgrade)
        assert outcome.params == {"name": "sam"}
        assert outcome.status == SWITCHING

    def test_middleware_sees_the_upgrade(self) -> None:
        """Run middleware around an upgrade, with the request's method GET."""
        app = echo_app()
        seen: list[tuple[str, str]] = []

        @app.middleware
        async def record(request: HttpRequest, call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Write down the method, then the kind of answer."""
            outcome = await call_next(request)
            seen.append((request.method, type(outcome).__name__))
            return outcome

        run(app.handle(upgrade_request("/echo/sam")), clock=FakeClock(autojump=True))
        assert seen == [("GET", "WebSocketUpgrade")]

    def test_plain_get_to_websocket_path_is_426(self) -> None:
        """Answer 426 Upgrade Required to a plain GET on a WebSocket-only path."""
        app = echo_app()
        request = HttpRequest("GET", "/echo/sam", QueryParams(), Headers())
        outcome = run(app.handle(request), clock=FakeClock(autojump=True))
        assert isinstance(outcome, HttpResponse)
        assert outcome.status == UPGRADE_REQUIRED
        assert outcome.headers.get("Upgrade") == "websocket"

    def test_upgrade_to_a_plain_route_is_routed_normally(self) -> None:
        """Answer an upgrade request for a path with no WebSocket route as a normal GET."""
        app = App()

        @app.get("/page")
        def page(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Answer."""
            return text("page")

        outcome = run(app.handle(upgrade_request("/page")), clock=FakeClock(autojump=True))
        assert isinstance(outcome, HttpResponse)
        assert outcome.status == OK

    def test_post_to_websocket_path_is_404(self) -> None:
        """Answer 404 to a non-GET request on a WebSocket-only path."""
        app = echo_app()
        request = HttpRequest("POST", "/echo/sam", QueryParams(), Headers(UPGRADE_HEADERS))
        outcome = run(app.handle(request), clock=FakeClock(autojump=True))
        assert outcome.status == NOT_FOUND

    def test_reason_phrase(self) -> None:
        """Know the reason phrase for 426."""
        assert STATUS_REASONS[UPGRADE_REQUIRED] == "Upgrade Required"


class TestWebSocketServer:
    """Test WebSocket conversations over real sockets."""

    def test_echo_text_and_bytes_with_path_param(self) -> None:
        """Echo a text and a bytes message, using the path parameter."""

        async def client(server: AsyncServer) -> tuple[str | bytes, str | bytes]:
            """Say hello in text and in bytes, then hang up."""
            ws = await connect_websocket(HOST, server.port, "/echo/sam")
            await ws.send_text("hi")
            heard_text = await ws.recv()
            await ws.send_bytes(b"\x00\x01")
            heard_bytes = await ws.recv()
            await ws.close()
            return heard_text, heard_bytes

        assert serve(echo_app(), client) == ("sam: hi", b"\x00\x01")

    def test_handler_return_closes_with_1000(self) -> None:
        """Close with 1000 when the handler returns."""
        app = App()

        @app.websocket("/hello")
        async def hello(ws: WebSocket, _request: HttpRequest) -> None:  # pyright: ignore[reportUnusedFunction]
            """Say one thing and hang up."""
            await ws.send_text("hello")

        async def client(server: AsyncServer) -> tuple[str | bytes, int]:
            """Hear the greeting, then the goodbye."""
            ws = await connect_websocket(HOST, server.port, "/hello")
            return await ws.recv(), await close_code(ws)

        assert serve(app, client) == ("hello", CLOSE_NORMAL)

    def test_middleware_rejection_is_sent_before_upgrade(self) -> None:
        """Send a middleware's 401 instead of upgrading, then close."""
        app = echo_app()

        @app.middleware
        async def guard(request: HttpRequest, call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Turn away anyone without a ticket."""
            if request.headers.get("X-Ticket") is None:
                return text("no ticket", UNAUTHORIZED)
            return await call_next(request)

        raw = client_handshake_request(HOST, "/echo/sam", KEY)
        reply, rest = serve(app, lambda s: exchange(s, raw))
        assert reply.status == UNAUTHORIZED
        assert reply.body == b"no ticket"
        assert reply.headers["connection"] == "close"
        assert rest == b""

    def test_middleware_runs_before_an_accepted_upgrade(self) -> None:
        """Run middleware once before the handshake, handing its request to the handler."""
        app = App()
        seen: list[str] = []

        @app.middleware
        async def record(request: HttpRequest, call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Write down the method and path, and pass on a request with a name tag."""
            seen.append(f"{request.method} {request.path}")
            headers = Headers([*request.headers.items(), ("X-Name", "kim")])
            tagged = HttpRequest(request.method, request.path, request.query, headers)
            return await call_next(tagged)

        @app.websocket("/echo/<name>")
        async def echo(ws: WebSocket, request: HttpRequest, name: str) -> None:  # pyright: ignore[reportUnusedFunction]
            """Answer once with the tag the middleware added."""
            await ws.send_text(f"{request.headers.get('X-Name')}/{name}: {await ws.recv()}")

        async def client(server: AsyncServer) -> str | bytes:
            """Say hi and hear it back."""
            ws = await connect_websocket(HOST, server.port, "/echo/kim")
            await ws.send_text("hi")
            heard = await ws.recv()
            await ws.close()
            return heard

        assert serve(app, client) == "kim/kim: hi"
        assert seen == ["GET /echo/kim"]

    def test_handler_exception_closes_with_1011(self, caplog: pytest.LogCaptureFixture) -> None:
        """Log a handler's exception and close with 1011."""
        app = App()

        @app.websocket("/oops")
        async def oops(_ws: WebSocket, _request: HttpRequest) -> None:  # pyright: ignore[reportUnusedFunction]
            """Drop the phone."""
            msg = "butterfingers"
            raise RuntimeError(msg)

        async def client(server: AsyncServer) -> int:
            """Wait for the goodbye."""
            return await close_code(await connect_websocket(HOST, server.port, "/oops"))

        with caplog.at_level(logging.ERROR, logger="pyweb.server"):
            assert serve(app, client) == CLOSE_INTERNAL_ERROR
        assert any("butterfingers" in (r.exc_text or "") for r in caplog.records)

    def test_server_close_closes_with_1001(self) -> None:
        """Close live WebSockets with 1001 when the server shuts down."""
        app = echo_app()

        async def client(server: AsyncServer) -> int:
            """Stay on the line while the server closes."""
            ws = await connect_websocket(HOST, server.port, "/echo/sam")
            await ws.send_text("hi")
            await ws.recv()
            closing = spawn(server.close())
            code = await close_code(ws)
            await closing
            return code

        assert serve(app, client) == CLOSE_GOING_AWAY

    def test_upgrade_accepted_while_closing_is_ended_with_1001(self) -> None:
        """End a WebSocket at once with 1001 if closing began before it was accepted."""
        app = echo_app()
        entered, release = Event(), Event()

        @app.middleware
        async def slow(request: HttpRequest, call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Hold the request until the test lets it go."""
            entered.set()
            await release.wait()
            return await call_next(request)

        async def client(server: AsyncServer) -> int:
            """Start dialling, begin the shutdown, then let the dial finish."""
            dialling = spawn(connect_websocket(HOST, server.port, "/echo/sam"))
            await entered.wait()
            closing = spawn(server.close())
            await sleep(POLL)
            release.set()
            code = await close_code(await dialling)
            await closing
            return code

        assert serve(app, client) == CLOSE_GOING_AWAY

    def test_plain_get_is_426(self) -> None:
        """Answer a plain GET on a WebSocket path with 426 Upgrade Required."""

        async def client(server: AsyncServer) -> Reply:
            """Ask without upgrading and read the answer."""
            reader, writer = await connect(server)
            await send(writer, b"GET /echo/sam HTTP/1.1\r\n\r\n")
            reply = await read_reply(reader)
            writer.close()
            return reply

        reply = serve(echo_app(), client)
        assert reply.status == UPGRADE_REQUIRED
        assert reply.headers["upgrade"] == "websocket"

    def test_handler_that_ignores_shutdown_is_cut_off(self) -> None:
        """Say 1001 and then cancel a handler that keeps going past the grace period."""
        app = App()
        cleaned: list[bool] = []

        @app.websocket("/stubborn")
        async def stubborn(_ws: WebSocket, _request: HttpRequest) -> None:  # pyright: ignore[reportUnusedFunction]
            """Sleep through the goodbye."""
            try:
                await sleep(WAIT)
            finally:
                cleaned.append(True)

        async def client(server: AsyncServer) -> int:
            """Stay on the line while the server closes."""
            ws = await connect_websocket(HOST, server.port, "/stubborn")
            await sleep(POLL)
            closing = spawn(server.close(grace=GRACE))
            code = await close_code(ws)
            await closing
            return code

        assert serve(app, client) == CLOSE_GOING_AWAY
        assert cleaned == [True]

    def test_plain_def_handler_is_called(self) -> None:
        """Call a plain def handler too, then close with 1000."""
        app = App()
        called: list[str] = []

        @app.websocket("/plain")
        def plain(_ws: WebSocket, request: HttpRequest) -> None:  # pyright: ignore[reportUnusedFunction]
            """Write down the path and hang up straight away."""
            called.append(request.path)

        async def client(server: AsyncServer) -> int:
            """Wait for the goodbye."""
            return await close_code(await connect_websocket(HOST, server.port, "/plain"))

        assert serve(app, client) == CLOSE_NORMAL
        assert called == ["/plain"]

    def test_bad_handshake_is_400(self) -> None:
        """Answer an upgrade with no Sec-WebSocket-Key with 400 and close."""
        raw = b"GET /echo/sam HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"

        async def client(server: AsyncServer) -> tuple[int, bytes]:
            """Send the broken handshake and read the answer."""
            reader, writer = await connect(server)
            await send(writer, raw)
            reply = await read_reply(reader)
            rest = await reader.read()
            writer.close()
            return reply.status, rest

        assert serve(echo_app(), client) == (BAD_REQUEST, b"")

    def test_access_log_line(self, caplog: pytest.LogCaptureFixture) -> None:
        """Log a finished WebSocket conversation as status 101."""

        async def client(server: AsyncServer) -> None:
            """Dial and hang up."""
            ws = await connect_websocket(HOST, server.port, "/echo/sam")
            await ws.close()

        with caplog.at_level(logging.INFO, logger="pyweb.access"):
            serve(echo_app(), client)
        assert any(r.getMessage().startswith("GET /echo/sam 101 ") for r in caplog.records)
