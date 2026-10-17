"""Tests for AsyncServer over real sockets on PyNet's loop."""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from pynet.errors import NetConnectionError
from pynet.loop import Event, get_running_loop, run, sleep, spawn, timeout
from pynet.streams import StreamReader, StreamWriter, open_connection

from pyweb.app import App
from pyweb.aserver import AsyncServer
from pyweb.http1 import Limits
from pyweb.messages import Headers, HttpRequest, HttpResponse, empty, text

HOST = "127.0.0.1"
OK = 200
NO_CONTENT = 204
BAD_REQUEST = 400
NOT_FOUND = 404
TIMEOUT = 408
LENGTH_REQUIRED = 411
TOO_LARGE = 413
HEADERS_TOO_LARGE = 431
SERVER_ERROR = 500
NOT_IMPLEMENTED = 501
UNAVAILABLE = 503
BAD_VERSION = 505
WAIT = 1.0
GRACE = 0.2
SHORT = 0.2
STALL_BUDGET = 0.5
HEAD_LIMIT = 1024
MAX_CONNECTIONS = 2
BIG_BODY = 64 * 1024 * 1024

FAST = Limits(header_timeout=WAIT, body_timeout=WAIT, keepalive_timeout=WAIT, write_timeout=WAIT)


@dataclass
class Reply:
    """One HTTP response as the client saw it."""

    status: int
    headers: dict[str, str]
    body: bytes


async def read_reply(reader: StreamReader, *, head_only: bool = False) -> Reply:
    """Read one response; trust Content-Length for the body unless ``head_only``."""
    raw = await reader.read_until(b"\r\n\r\n")
    status_line, *lines = raw[:-4].decode("latin-1").split("\r\n")
    headers: dict[str, str] = {}
    for line in lines:
        name, _, value = line.partition(":")
        headers[name.lower()] = value.strip()
    length = 0 if head_only else int(headers.get("content-length", "0"))
    body = await reader.read_exactly(length)
    return Reply(int(status_line.split(" ")[1]), headers, body)


async def connect(server: AsyncServer) -> tuple[StreamReader, StreamWriter]:
    """Open a client connection to the server."""
    return await open_connection(HOST, server.port)


async def send(writer: StreamWriter, raw: bytes) -> None:
    """Write raw bytes and flush them."""
    writer.write(raw)
    await writer.drain()


async def exchange(server: AsyncServer, raw: bytes) -> tuple[Reply, bytes]:
    """Send raw bytes on a new connection; return the reply and what follows it."""
    reader, writer = await connect(server)
    await send(writer, raw)
    reply = await read_reply(reader)
    rest = await reader.read()
    writer.close()
    return reply, rest


def serve[T](
    app: App,
    client: Callable[[AsyncServer], Awaitable[T]],
    limits: Limits = FAST,
    *,
    access_log: bool = True,
) -> T:
    """Start a server on a free port, run ``client`` against it, then close."""

    async def main() -> T:
        """Run the client with a deadline and always close the server."""
        server = AsyncServer(app, limits=limits, access_log=access_log)
        await server.start(HOST, 0)
        try:
            async with timeout(WAIT * 2):
                return await client(server)
        finally:
            await server.close(grace=GRACE)

    return run(main())


def hello_app() -> App:
    """Build an app with a few simple routes."""
    app = App()

    @app.get("/hello")
    def hello(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Say hello."""
        return text("hello")

    @app.post("/echo")
    def echo(request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Send the body back."""
        return HttpResponse(body=request.body)

    @app.get("/client")
    def client(request: HttpRequest) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        """Report who is asking."""
        assert request.client is not None
        return {"host": request.client[0]}

    @app.get("/nothing")
    def nothing(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Answer 204."""
        return empty()

    return app


GET_HELLO = b"GET /hello HTTP/1.1\r\nHost: x\r\n\r\n"
GET_HELLO_CLOSE = b"GET /hello HTTP/1.1\r\nConnection: close\r\n\r\n"


class TestRoundTrip:
    """Test requests and responses over one connection."""

    def test_get(self) -> None:
        """Answer a GET with standard headers added."""
        reply = serve(hello_app(), lambda s: exchange(s, GET_HELLO_CLOSE))[0]
        assert reply.status == OK
        assert reply.body == b"hello"
        assert reply.headers["content-length"] == "5"
        assert reply.headers["server"] == "PyWeb"
        assert reply.headers["date"].endswith("GMT")
        assert reply.headers["content-type"] == "text/plain; charset=utf-8"

    def test_post_with_body(self) -> None:
        """Read the body by Content-Length and hand it to the handler."""
        raw = (
            b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 4\r\nConnection: close\r\n\r\nping"
        )
        reply, rest = serve(hello_app(), lambda s: exchange(s, raw))
        assert reply.status == OK
        assert reply.body == b"ping"
        assert rest == b""

    def test_client_address(self) -> None:
        """Pass the peer address to the request."""
        raw = b"GET /client HTTP/1.1\r\nConnection: close\r\n\r\n"
        reply, _ = serve(hello_app(), lambda s: exchange(s, raw))
        assert reply.body == b'{"host": "127.0.0.1"}'

    def test_two_requests_on_one_connection(self) -> None:
        """Keep an HTTP/1.1 connection open for a second request."""

        async def client(server: AsyncServer) -> list[Reply]:
            """Send two requests one after the other."""
            reader, writer = await connect(server)
            replies: list[Reply] = []
            for _ in range(2):
                await send(writer, GET_HELLO)
                replies.append(await read_reply(reader))
            writer.close()
            return replies

        replies = serve(hello_app(), client)
        assert [r.status for r in replies] == [OK, OK]
        assert all("connection" not in r.headers for r in replies)

    def test_pipelined_requests(self) -> None:
        """Answer two requests that arrive in one packet, in order."""

        async def client(server: AsyncServer) -> list[Reply]:
            """Send both requests at once."""
            reader, writer = await connect(server)
            echo = b"POST /echo HTTP/1.1\r\nContent-Length: 2\r\n\r\nhi"
            await send(writer, echo + GET_HELLO)
            replies = [await read_reply(reader), await read_reply(reader)]
            writer.close()
            return replies

        first, second = serve(hello_app(), client)
        assert (first.body, second.body) == (b"hi", b"hello")

    def test_connection_close_honoured(self) -> None:
        """Close after answering when the client asks."""
        raw = b"GET /hello HTTP/1.1\r\nConnection: close\r\n\r\n"
        reply, rest = serve(hello_app(), lambda s: exchange(s, raw))
        assert reply.headers["connection"] == "close"
        assert rest == b""

    def test_handler_connection_close_closes_after_the_response(self) -> None:
        """Close after the response when the handler sets ``Connection: close``."""
        app = App()

        @app.get("/bye")
        def bye(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Answer and ask for the connection to close."""
            response = text("bye")
            response.headers.set("Connection", "Close")
            return response

        reply, rest = serve(app, lambda s: exchange(s, b"GET /bye HTTP/1.1\r\n\r\n" + GET_HELLO))
        assert reply.body == b"bye"
        assert reply.headers["connection"] == "close"
        assert rest == b""

    def test_http10_closes_by_default(self) -> None:
        """Close an HTTP/1.0 connection unless it asks for keep-alive."""
        reply, rest = serve(hello_app(), lambda s: exchange(s, b"GET /hello HTTP/1.0\r\n\r\n"))
        assert reply.status == OK
        assert reply.headers["connection"] == "close"
        assert rest == b""

    def test_http10_keep_alive(self) -> None:
        """Keep an HTTP/1.0 connection that asks, and say so."""

        async def client(server: AsyncServer) -> list[Reply]:
            """Send two keep-alive HTTP/1.0 requests."""
            reader, writer = await connect(server)
            replies: list[Reply] = []
            for _ in range(2):
                await send(writer, b"GET /hello HTTP/1.0\r\nConnection: keep-alive\r\n\r\n")
                replies.append(await read_reply(reader))
            writer.close()
            return replies

        replies = serve(hello_app(), client)
        assert [r.headers["connection"] for r in replies] == ["keep-alive", "keep-alive"]

    def test_no_content_has_no_length(self) -> None:
        """Send no Content-Length on a 204."""
        raw = b"GET /nothing HTTP/1.1\r\nConnection: close\r\n\r\n"
        reply, rest = serve(hello_app(), lambda s: exchange(s, raw))
        assert reply.status == NO_CONTENT
        assert "content-length" not in reply.headers
        assert rest == b""

    def test_not_found(self) -> None:
        """Answer an unknown path with 404 and keep the connection."""

        async def client(server: AsyncServer) -> list[int]:
            """Ask for a missing page, then a real one."""
            reader, writer = await connect(server)
            await send(writer, b"GET /missing HTTP/1.1\r\n\r\n" + GET_HELLO)
            statuses = [(await read_reply(reader)).status, (await read_reply(reader)).status]
            writer.close()
            return statuses

        assert serve(hello_app(), client) == [NOT_FOUND, OK]


class TestHead:
    """Test HEAD over the wire."""

    def test_head_sends_get_length_and_no_body(self) -> None:
        """Send the GET Content-Length but no body, and keep the connection usable."""

        async def client(server: AsyncServer) -> list[Reply]:
            """Send HEAD then GET on one connection."""
            reader, writer = await connect(server)
            await send(writer, b"HEAD /hello HTTP/1.1\r\n\r\n" + GET_HELLO)
            replies = [await read_reply(reader, head_only=True), await read_reply(reader)]
            writer.close()
            return replies

        head, get = serve(hello_app(), client)
        assert head.headers["content-length"] == "5"
        assert get.body == b"hello"

    def test_head_on_streaming_response_sends_only_the_head(self) -> None:
        """Answer HEAD on a stream with the head alone."""
        app = App()

        class Stream:
            """A minimal streaming response."""

            def __init__(self) -> None:
                """Set status and headers."""
                self.status = OK
                self.headers = Headers([("Content-Type", "text/event-stream")])

        @app.get("/events")
        def events(_request: HttpRequest) -> Stream:  # pyright: ignore[reportUnusedFunction]
            """Return the stream."""
            return Stream()

        raw = b"HEAD /events HTTP/1.1\r\nConnection: close\r\n\r\n"
        reply, rest = serve(app, lambda s: exchange(s, raw))
        assert reply.status == OK
        assert reply.headers["content-type"] == "text/event-stream"
        assert "content-length" not in reply.headers
        assert rest == b""


class TestConcurrency:
    """Test that connections do not wait for each other."""

    def test_slow_handler_does_not_block_other_connections(self) -> None:
        """Answer B while A's handler is still waiting."""
        app = App()
        gate = Event()
        started = Event()
        order: list[str] = []

        @app.get("/slow")
        async def slow(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Wait for the gate."""
            started.set()
            await gate.wait()
            order.append("slow handler done")
            return text("slow")

        @app.get("/fast")
        async def fast(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Answer at once."""
            return text("fast")

        async def client(server: AsyncServer) -> tuple[Reply, Reply]:
            """Start A, finish B, then release A."""
            reader_a, writer_a = await connect(server)
            await send(writer_a, b"GET /slow HTTP/1.1\r\n\r\n")
            await started.wait()
            reader_b, writer_b = await connect(server)
            await send(writer_b, b"GET /fast HTTP/1.1\r\n\r\n")
            reply_b = await read_reply(reader_b)
            order.append("fast reply")
            gate.set()
            reply_a = await read_reply(reader_a)
            writer_a.close()
            writer_b.close()
            return reply_a, reply_b

        reply_a, reply_b = serve(app, client)
        assert (reply_a.body, reply_b.body) == (b"slow", b"fast")
        assert order == ["fast reply", "slow handler done"]


class TestTimeouts:
    """Test header, idle, body and write timeouts."""

    def test_stalled_head_gets_408(self) -> None:
        """Answer 408 and close when the head stops arriving."""
        limits = Limits(header_timeout=SHORT)

        async def client(server: AsyncServer) -> tuple[Reply, bytes, float]:
            """Send half a head and wait."""
            loop = get_running_loop()
            began = loop.time()
            reply, rest = await exchange(server, b"GET / HTTP/1.1\r\n")
            return reply, rest, loop.time() - began

        reply, rest, took = serve(hello_app(), client, limits)
        assert reply.status == TIMEOUT
        assert reply.headers["connection"] == "close"
        assert rest == b""
        assert took < STALL_BUDGET

    def test_idle_keep_alive_closes_silently(self) -> None:
        """Close an idle keep-alive connection without a response."""
        limits = Limits(keepalive_timeout=SHORT)

        async def client(server: AsyncServer) -> bytes:
            """Make one request, then go quiet."""
            reader, writer = await connect(server)
            await send(writer, GET_HELLO)
            await read_reply(reader)
            rest = await reader.read()
            writer.close()
            return rest

        assert serve(hello_app(), client, limits) == b""

    def test_silent_new_connection_closes_without_response(self) -> None:
        """Close a connection that never sends a byte, without a response."""
        limits = Limits(header_timeout=SHORT)

        async def client(server: AsyncServer) -> bytes:
            """Connect and say nothing."""
            reader, writer = await connect(server)
            rest = await reader.read()
            writer.close()
            return rest

        assert serve(hello_app(), client, limits) == b""

    def test_slow_body_gets_408(self) -> None:
        """Answer 408 when the body stops arriving."""
        limits = Limits(body_timeout=SHORT)
        raw = b"POST /echo HTTP/1.1\r\nContent-Length: 10\r\n\r\nabc"
        reply, _ = serve(hello_app(), lambda s: exchange(s, raw), limits)
        assert reply.status == TIMEOUT

    def test_client_that_never_reads_is_cut_off(self) -> None:
        """Drop a connection whose client stops reading a big response."""
        app = App()

        @app.get("/big")
        def big(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Answer with far more than the socket buffers hold."""
            return HttpResponse(body=b"x" * BIG_BODY)

        limits = Limits(write_timeout=SHORT)

        async def client(server: AsyncServer) -> int:
            """Ask for the big page, never read, wait for the server to give up."""
            _reader, writer = await connect(server)
            await send(writer, b"GET /big HTTP/1.1\r\n\r\n")
            await sleep(STALL_BUDGET)
            writer.close()
            return server.connection_count

        assert serve(app, client, limits) == 0


class TestErrors:
    """Test error answers for bad requests and bad responses."""

    @pytest.mark.parametrize(
        ("raw", "status"),
        [
            (b"POST /echo HTTP/1.1\r\nContent-Length: 999999999\r\n\r\n", TOO_LARGE),
            (b"POST /echo HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n", BAD_REQUEST),
            (
                b"POST /echo HTTP/1.1\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\n",
                BAD_REQUEST,
            ),
            (b"POST /echo HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n", LENGTH_REQUIRED),
            (b"BREW /pot HTTP/1.1\r\n\r\n", NOT_IMPLEMENTED),
            (b"GET / HTTP/2.0\r\n\r\n", BAD_VERSION),
            (b"GET / HTTP/1.1\r\nX-A: one\ntwo\r\n\r\n", BAD_REQUEST),
            (b"GET nowhere HTTP/1.1\r\n\r\n", BAD_REQUEST),
            (b"GET /%ff HTTP/1.1\r\n\r\n", BAD_REQUEST),
        ],
    )
    def test_bad_request_status_and_close(self, raw: bytes, status: int) -> None:
        """Answer each bad request with its status, then close."""
        reply, rest = serve(hello_app(), lambda s: exchange(s, raw))
        assert reply.status == status
        assert reply.headers["connection"] == "close"
        assert reply.headers["content-type"] == "application/json"
        assert rest == b""

    def test_error_page_is_html_when_wanted(self) -> None:
        """Render a framing error as HTML for a browser."""
        raw = b"POST /echo HTTP/1.1\r\nAccept: text/html\r\nContent-Length: 999999999\r\n\r\n"
        reply, _ = serve(hello_app(), lambda s: exchange(s, raw))
        assert reply.status == TOO_LARGE
        assert b"<h1>413" in reply.body

    def test_head_too_large_is_431(self) -> None:
        """Answer 431 when the head is longer than max_head_bytes."""
        limits = Limits(max_head_bytes=HEAD_LIMIT)
        raw = b"GET /hello HTTP/1.1\r\nX-Big: " + b"a" * HEAD_LIMIT + b"\r\n\r\n"
        reply, rest = serve(hello_app(), lambda s: exchange(s, raw), limits)
        assert reply.status == HEADERS_TOO_LARGE
        assert rest == b""

    def test_head_of_exactly_max_head_bytes_is_accepted(self) -> None:
        """Accept a head of exactly max_head_bytes."""
        limits = Limits(max_head_bytes=HEAD_LIMIT)
        start = b"GET /hello HTTP/1.1\r\nConnection: close\r\nX-Pad: "
        raw = start + b"a" * (HEAD_LIMIT - len(start)) + b"\r\n\r\n"
        reply, _ = serve(hello_app(), lambda s: exchange(s, raw), limits)
        assert reply.status == OK

    def test_response_splitting_becomes_500(self, caplog: pytest.LogCaptureFixture) -> None:
        """Answer 500 rather than send a header value with CR/LF in it."""
        app = App()

        @app.get("/evil")
        def evil(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Sneak a newline past the Headers checks."""
            response = text("evil")
            response.headers._items.append(("X-Evil", "a\r\nSet-Cookie: x=1"))
            return response

        raw = b"GET /evil HTTP/1.1\r\n\r\n"
        with caplog.at_level(logging.ERROR, logger="pyweb.server"):
            reply, rest = serve(app, lambda s: exchange(s, raw))
        assert reply.status == SERVER_ERROR
        assert "set-cookie" not in reply.headers
        assert rest == b""
        assert any("could not be sent" in r.getMessage() for r in caplog.records)

    def test_unknown_streaming_outcome_is_500(self) -> None:
        """Answer 500 for a kind of stream the server does not know how to write."""
        app = App()

        class Stream:
            """A minimal streaming response."""

            def __init__(self) -> None:
                """Set status and headers."""
                self.status = OK
                self.headers = Headers()

        @app.get("/events")
        def events(_request: HttpRequest) -> Stream:  # pyright: ignore[reportUnusedFunction]
            """Return the stream."""
            return Stream()

        reply, rest = serve(app, lambda s: exchange(s, b"GET /events HTTP/1.1\r\n\r\n"))
        assert reply.status == SERVER_ERROR
        assert rest == b""


class TestClientGoesAway:
    """Test clients that hang up part-way through."""

    @pytest.mark.parametrize(
        "raw",
        [b"GET /hello HTTP/1.1\r\n", b"POST /echo HTTP/1.1\r\nContent-Length: 9\r\n\r\nabc"],
    )
    def test_hang_up_mid_request_is_quiet(
        self, raw: bytes, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Forget a connection whose client hangs up mid-request, logging nothing."""

        async def client(server: AsyncServer) -> int:
            """Send part of a request, hang up, wait for the server to notice."""
            _reader, writer = await connect(server)
            await send(writer, raw)
            while not server.connection_count:
                await sleep(0)
            writer.close()
            while server.connection_count:
                await sleep(0)
            return server.connection_count

        with caplog.at_level(logging.INFO):
            assert serve(hello_app(), client) == 0
        assert not caplog.records

    def test_app_that_raises_gives_500(self, caplog: pytest.LogCaptureFixture) -> None:
        """Answer 500 even if the App itself raises."""

        class BrokenApp(App):
            """An app whose handle method crashes."""

            async def handle(self, request: HttpRequest) -> HttpResponse:
                """Crash."""
                msg = f"broken for {request.path}"
                raise RuntimeError(msg)

        with caplog.at_level(logging.ERROR, logger="pyweb.server"):
            reply, _ = serve(BrokenApp(), lambda s: exchange(s, GET_HELLO_CLOSE))
        assert reply.status == SERVER_ERROR
        assert any("App failed" in r.getMessage() for r in caplog.records)


class TestConnectionLimit:
    """Test max_connections."""

    def test_extra_connection_gets_503(self) -> None:
        """Turn away a connection beyond max_connections with 503."""
        limits = Limits(max_connections=MAX_CONNECTIONS)

        async def client(server: AsyncServer) -> tuple[int, Reply, bytes, int]:
            """Hold two connections open, then try a third."""
            held: list[StreamWriter] = []
            for _ in range(MAX_CONNECTIONS):
                reader, writer = await connect(server)
                await send(writer, GET_HELLO)
                await read_reply(reader)
                held.append(writer)
            count = server.connection_count
            reply, rest = await exchange(server, GET_HELLO)
            for writer in held:
                writer.close()
            return count, reply, rest, server.connection_count

        count, reply, rest, after = serve(hello_app(), client, limits)
        assert count == MAX_CONNECTIONS
        assert reply.status == UNAVAILABLE
        assert reply.headers["connection"] == "close"
        assert rest == b""
        assert after == MAX_CONNECTIONS


class TestLifecycle:
    """Test start, graceful close and hooks."""

    def test_port_before_start_raises(self) -> None:
        """Refuse to report a port before the server starts."""
        with pytest.raises(RuntimeError, match="not started"):
            _ = AsyncServer(App()).port

    def test_start_twice_raises(self) -> None:
        """Refuse to start a server that is already running."""

        async def main() -> None:
            """Start, then start again."""
            async with AsyncServer(App()) as server:
                with pytest.raises(RuntimeError, match="already started"):
                    await server.start()

        run(main())

    def test_failed_start_runs_shutdown(self) -> None:
        """Undo startup if the port cannot be bound."""
        app = App()
        calls: list[str] = []
        app.on_startup(lambda: calls.append("startup"))
        app.on_shutdown(lambda: calls.append("shutdown"))

        async def main() -> None:
            """Take a port, then try to take it again."""
            async with AsyncServer(App()) as first:
                with pytest.raises(NetConnectionError):
                    await AsyncServer(app).start(HOST, first.port)

        run(main())
        assert calls == ["startup", "shutdown"]

    def test_close_before_start_does_nothing(self) -> None:
        """Close a server that never started without running hooks."""
        app = App()
        calls: list[str] = []
        app.on_shutdown(lambda: calls.append("shutdown"))
        run(AsyncServer(app).close())
        assert calls == []

    def test_hooks_run_and_new_connections_are_refused(self) -> None:
        """Run startup on start and shutdown on close; stop listening."""
        app = App()
        calls: list[str] = []
        app.on_startup(lambda: calls.append("startup"))
        app.on_shutdown(lambda: calls.append("shutdown"))

        async def main() -> None:
            """Start, close, then try to connect."""
            server = AsyncServer(app)
            await server.start(HOST, 0)
            assert calls == ["startup"]
            port = server.port
            await server.close()
            await server.close()  # closing twice is harmless
            assert calls == ["startup", "shutdown"]
            with pytest.raises(NetConnectionError):
                await open_connection(HOST, port)

        run(main())

    def test_close_twice_after_a_failing_shutdown_returns(self) -> None:
        """Let a second close return promptly even if the first one's shutdown raised."""

        class BrokenApp(App):
            """An app whose shutdown raises."""

            async def shutdown(self) -> None:
                """Fail on purpose."""
                msg = "shutdown broke"
                raise RuntimeError(msg)

        async def main() -> None:
            """Close twice; the second must not wait forever."""
            server = AsyncServer(BrokenApp())
            await server.start(HOST, 0)
            with pytest.raises(RuntimeError, match="shutdown broke"):
                await server.close()
            async with timeout(SHORT):
                await server.close()

        run(main())

    def test_graceful_close_lets_in_flight_request_finish(self) -> None:
        """Finish a request already being handled when close begins."""
        app = App()
        started = Event()
        calls: list[str] = []
        app.on_shutdown(lambda: calls.append("shutdown"))

        @app.get("/slow")
        async def slow(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Take a little while."""
            started.set()
            await sleep(SHORT / 2)
            calls.append("handler done")
            return text("done")

        async def client(server: AsyncServer) -> Reply:
            """Start a request, close the server, read the answer."""
            reader, writer = await connect(server)
            await send(writer, b"GET /slow HTTP/1.1\r\n\r\n")
            await started.wait()
            closing = spawn(server.close(grace=STALL_BUDGET))
            reply = await read_reply(reader)
            await closing
            writer.close()
            return reply

        reply = serve(app, client)
        assert reply.status == OK
        assert reply.body == b"done"
        assert reply.headers["connection"] == "close"
        assert calls == ["handler done", "shutdown"]

    def test_request_longer_than_grace_is_cut_off(self) -> None:
        """Cancel a request still running when the grace period ends."""
        app = App()
        started = Event()
        calls: list[str] = []
        app.on_shutdown(lambda: calls.append("shutdown"))

        @app.get("/forever")
        async def forever(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Never finish on its own."""
            started.set()
            await Event().wait()
            return text("never")  # pragma: no cover - cancelled first

        async def client(server: AsyncServer) -> tuple[bytes, float]:
            """Start a request, close with a short grace, see the cut."""
            loop = get_running_loop()
            reader, writer = await connect(server)
            await send(writer, b"GET /forever HTTP/1.1\r\n\r\n")
            await started.wait()
            began = loop.time()
            await server.close(grace=SHORT)
            took = loop.time() - began
            rest = await reader.read()
            writer.close()
            return rest, took

        rest, took = serve(app, client)
        assert rest == b""
        assert took < STALL_BUDGET
        assert calls == ["shutdown"]

    def test_idle_connections_close_at_once(self) -> None:
        """Close idle keep-alive connections without waiting for the grace."""

        async def client(server: AsyncServer) -> tuple[bytes, float, int]:
            """Leave a connection idle and close the server."""
            loop = get_running_loop()
            reader, writer = await connect(server)
            await send(writer, GET_HELLO)
            await read_reply(reader)
            began = loop.time()
            await server.close(grace=WAIT)
            took = loop.time() - began
            rest = await reader.read()
            writer.close()
            return rest, took, server.connection_count

        rest, took, count = serve(hello_app(), client)
        assert rest == b""
        assert took < SHORT
        assert count == 0

    def test_async_with_starts_and_closes(self) -> None:
        """Start on entry and close on exit."""
        app = hello_app()
        calls: list[str] = []
        app.on_shutdown(lambda: calls.append("shutdown"))

        async def main() -> Reply:
            """Serve one request inside the block."""
            async with AsyncServer(app) as server:
                reply, _ = await exchange(server, GET_HELLO_CLOSE)
            return reply

        assert run(main()).status == OK
        assert calls == ["shutdown"]

    def test_serve_forever_until_cancelled(self) -> None:
        """Start, serve until the task is cancelled, then close."""
        app = hello_app()
        calls: list[str] = []
        app.on_shutdown(lambda: calls.append("shutdown"))

        async def main() -> tuple[Reply, bool]:
            """Run serve_forever in a task, use it, cancel it."""
            server = AsyncServer(app)
            task = spawn(server.serve_forever())
            while not _started(server):
                await sleep(0)
            reply, _ = await exchange(server, GET_HELLO_CLOSE)
            task.cancel()
            async with timeout(WAIT):
                while not task.done():
                    await sleep(0)
            return reply, task.cancelled()

        reply, cancelled = run(main())
        assert reply.status == OK
        assert cancelled
        assert calls == ["shutdown"]


def _started(server: AsyncServer) -> bool:
    """Return whether the server is listening yet."""
    try:
        _ = server.port
    except RuntimeError:
        return False
    return True


class TestAccessLog:
    """Test the access log."""

    def test_record_format(self, caplog: pytest.LogCaptureFixture) -> None:
        """Log method, path, status, bytes and milliseconds, but not the query."""
        raw = b"GET /hello?token=secret HTTP/1.1\r\nConnection: close\r\n\r\n"
        with caplog.at_level(logging.INFO, logger="pyweb.access"):
            serve(hello_app(), lambda s: exchange(s, raw))
        messages = [r.getMessage() for r in caplog.records if r.name == "pyweb.access"]
        assert len(messages) == 1
        assert re.fullmatch(r"GET /hello 200 5 \d+\.\dms", messages[0])

    def test_error_is_logged_with_dashes(self, caplog: pytest.LogCaptureFixture) -> None:
        """Log a request that could not be parsed with dashes for method and path."""
        with caplog.at_level(logging.INFO, logger="pyweb.access"):
            serve(hello_app(), lambda s: exchange(s, b"GET / HTTP/2.0\r\n\r\n"))
        messages = [r.getMessage() for r in caplog.records if r.name == "pyweb.access"]
        assert len(messages) == 1
        assert messages[0].startswith("- - 505 ")

    def test_odd_path_characters_are_escaped(self, caplog: pytest.LogCaptureFixture) -> None:
        """Escape control characters so a path cannot forge log lines."""
        raw = b"GET /a\x1bb HTTP/1.1\r\nConnection: close\r\n\r\n"
        with caplog.at_level(logging.INFO, logger="pyweb.access"):
            serve(hello_app(), lambda s: exchange(s, raw))
        messages = [r.getMessage() for r in caplog.records if r.name == "pyweb.access"]
        assert messages[0].startswith("GET /a%1Bb 404 ")

    def test_access_log_can_be_turned_off(self, caplog: pytest.LogCaptureFixture) -> None:
        """Write no access records when access_log is False."""
        with caplog.at_level(logging.INFO, logger="pyweb.access"):
            serve(hello_app(), lambda s: exchange(s, GET_HELLO_CLOSE), access_log=False)
        assert not [r for r in caplog.records if r.name == "pyweb.access"]
