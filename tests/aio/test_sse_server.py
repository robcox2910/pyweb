"""Tests for streaming server-sent events over real sockets."""

import logging
from collections.abc import AsyncIterator

import pytest
from pynet.loop import get_running_loop, sleep, spawn
from pynet.streams import StreamReader

from pyweb.app import App
from pyweb.aserver import AsyncServer
from pyweb.messages import HttpRequest
from pyweb.sse import EventHub, EventStream, ServerEvent, encode_event
from tests.aio.test_aserver import (
    GRACE,
    OK,
    SERVER_ERROR,
    WAIT,
    connect,
    exchange,
    read_reply,
    send,
    serve,
)

TICK = 0.1
POLL = 0.01
THREE = 3
GET_EVENTS = b"GET /events HTTP/1.1\r\nHost: x\r\n\r\n"
KEEP_ALIVE_COMMENT = b": keep-alive\n\n"


async def read_chunk(reader: StreamReader) -> bytes:
    """Read one chunk of a chunked body; ``b""`` is the terminating chunk."""
    size_line = await reader.read_until(b"\r\n")
    size = int(size_line[:-2], 16)
    data = await reader.read_exactly(size + 2)
    assert data.endswith(b"\r\n")
    return data[:-2]


async def wait_until(check: object, *, budget: float = WAIT) -> None:
    """Poll ``check()`` until it is true, failing after ``budget`` seconds."""
    assert callable(check)
    loop = get_running_loop()
    deadline = loop.time() + budget
    while not check():
        assert loop.time() < deadline, "condition never became true"
        await sleep(POLL)


def ticks_app(closed: list[bool]) -> App:
    """Build an app whose ``/events`` streams three numbered ticks."""
    app = App()

    @app.get("/events")
    def events(_request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
        """Stream three ticks, then end."""

        async def source() -> AsyncIterator[ServerEvent]:
            """Yield the ticks; note the cleanup."""
            try:
                for n in range(1, THREE + 1):
                    yield ServerEvent(str(n), event="tick")
            finally:
                closed.append(True)

        return EventStream(source())

    return app


def hub_app(hub: EventHub, *, keepalive: float = WAIT) -> App:
    """Build an app whose ``/events`` follows a hub."""
    app = App()

    @app.get("/events")
    def events(_request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
        """Stream whatever the hub publishes."""
        return EventStream(hub.subscribe(), keepalive=keepalive)

    return app


class TestStreaming:
    """Test the bytes the server writes for an EventStream."""

    def test_events_are_sent_as_chunks_then_the_connection_closes(self) -> None:
        """Send the SSE head, one chunk per event, the last chunk, then close."""
        closed: list[bool] = []

        async def client(server: AsyncServer) -> tuple[int, dict[str, str], list[bytes], bytes]:
            """Read the whole stream."""
            reader, writer = await connect(server)
            await send(writer, GET_EVENTS)
            reply = await read_reply(reader, head_only=True)
            chunks = [await read_chunk(reader) for _ in range(THREE + 1)]
            rest = await reader.read()
            writer.close()
            return reply.status, reply.headers, chunks, rest

        status, headers, chunks, rest = serve(ticks_app(closed), client)
        assert status == OK
        assert headers["content-type"] == "text/event-stream"
        assert headers["cache-control"] == "no-store"
        assert headers["transfer-encoding"] == "chunked"
        assert headers["connection"] == "close"
        assert "content-length" not in headers
        expected = [encode_event(ServerEvent(str(n), event="tick")) for n in range(1, THREE + 1)]
        assert chunks == [*expected, b""]
        assert rest == b""
        assert closed == [True]

    def test_keep_alive_comment_when_idle(self) -> None:
        """Send a comment chunk after ``keepalive`` quiet seconds, then real events."""
        hub = EventHub()

        async def client(server: AsyncServer) -> tuple[bytes, bytes]:
            """Wait for the comment, then publish one event."""
            reader, writer = await connect(server)
            await send(writer, GET_EVENTS)
            await read_reply(reader, head_only=True)
            comment = await read_chunk(reader)
            hub.publish(ServerEvent("news"))
            event = await read_chunk(reader)
            writer.close()
            return comment, event

        comment, event = serve(hub_app(hub, keepalive=TICK), client)
        assert comment == KEEP_ALIVE_COMMENT
        assert event == b"data: news\n\n"

    def test_client_disconnect_closes_source_and_unsubscribes(self) -> None:
        """Notice a browser closing the tab and drop its hub subscription."""
        hub = EventHub()

        async def client(server: AsyncServer) -> tuple[bytes, int]:
            """Read one event, hang up, then wait for the server to notice."""
            reader, writer = await connect(server)
            await send(writer, GET_EVENTS)
            await read_reply(reader, head_only=True)
            await wait_until(lambda: hub.subscriber_count == 1)
            hub.publish(ServerEvent("first"))
            first = await read_chunk(reader)
            writer.close()
            await wait_until(lambda: hub.subscriber_count == 0)
            await wait_until(lambda: server.connection_count == 0)
            return first, hub.publish(ServerEvent("nobody hears this"))

        first, reached = serve(hub_app(hub), client)
        assert first == b"data: first\n\n"
        assert reached == 0

    def test_disconnect_runs_a_generator_sources_cleanup(self) -> None:
        """Run the ``finally`` of a generator that was waiting for its next event."""
        closed: list[bool] = []
        app = App()

        @app.get("/events")
        def events(_request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
            """Yield one event, then wait for a long time."""

            async def source() -> AsyncIterator[ServerEvent]:
                """Say hello, then sleep."""
                try:
                    yield ServerEvent("hello")
                    await sleep(WAIT * 10)
                finally:
                    closed.append(True)

            return EventStream(source())

        async def client(server: AsyncServer) -> bytes:
            """Read the event, then hang up."""
            reader, writer = await connect(server)
            await send(writer, GET_EVENTS)
            await read_reply(reader, head_only=True)
            event = await read_chunk(reader)
            writer.close()
            await wait_until(lambda: bool(closed))
            return event

        assert serve(app, client) == b"data: hello\n\n"
        assert closed == [True]

    def test_graceful_close_ends_an_open_stream(self) -> None:
        """End a stream with the last chunk when the server shuts down."""
        hub = EventHub()

        async def client(server: AsyncServer) -> tuple[bytes, bytes, float]:
            """Start listening, then close the server under the stream."""
            reader, writer = await connect(server)
            await send(writer, GET_EVENTS)
            await read_reply(reader, head_only=True)
            await wait_until(lambda: hub.subscriber_count == 1)
            loop = get_running_loop()
            started = loop.time()
            closing = spawn(server.close(grace=WAIT))
            last = await read_chunk(reader)
            rest = await reader.read()
            await closing
            writer.close()
            return last, rest, loop.time() - started

        last, rest, took = serve(hub_app(hub), client)
        assert last == b""
        assert rest == b""
        assert took < WAIT / 2
        assert hub.subscriber_count == 0

    def test_last_event_id_reaches_the_handler(self) -> None:
        """Give the handler the Last-Event-ID a reconnecting browser sends."""
        app = App()

        @app.get("/events")
        def events(request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
            """Say where the listener left off."""

            async def resume() -> AsyncIterator[ServerEvent]:
                """Yield one event naming the last id."""
                yield ServerEvent(f"after {request.last_event_id}")

            return EventStream(resume())

        async def client(server: AsyncServer) -> bytes:
            """Reconnect with a Last-Event-ID header."""
            reader, writer = await connect(server)
            await send(writer, b"GET /events HTTP/1.1\r\nLast-Event-ID: 41\r\n\r\n")
            await read_reply(reader, head_only=True)
            event = await read_chunk(reader)
            writer.close()
            return event

        assert serve(app, client) == b"data: after 41\n\n"

    def test_head_sends_only_the_head_and_closes_the_source(self) -> None:
        """Answer HEAD with the head alone and still close the hub subscription."""
        hub = EventHub()
        raw = b"HEAD /events HTTP/1.1\r\nConnection: close\r\n\r\n"
        reply, rest = serve(hub_app(hub), lambda s: exchange(s, raw))
        assert reply.status == OK
        assert reply.headers["content-type"] == "text/event-stream"
        assert rest == b""
        assert hub.subscriber_count == 0

    def test_failing_source_is_logged_and_the_stream_ends(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log a source that raises, then end the stream cleanly."""
        app = App()

        @app.get("/events")
        def events(_request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
            """Yield one event, then break."""

            async def source() -> AsyncIterator[ServerEvent]:
                """Fail after the first event."""
                yield ServerEvent("ok")
                msg = "the source broke"
                raise RuntimeError(msg)

            return EventStream(source())

        async def client(server: AsyncServer) -> list[bytes]:
            """Read until the stream ends."""
            reader, writer = await connect(server)
            await send(writer, GET_EVENTS)
            await read_reply(reader, head_only=True)
            chunks = [await read_chunk(reader), await read_chunk(reader)]
            writer.close()
            return chunks

        with caplog.at_level(logging.ERROR, logger="pyweb.server"):
            chunks = serve(app, client)
        assert chunks == [b"data: ok\n\n", b""]
        assert any("event source" in r.getMessage() for r in caplog.records)

    def test_unencodable_event_is_logged_and_the_stream_ends(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log an event that cannot be encoded, then end the stream cleanly."""
        closed: list[bool] = []
        app = App()

        @app.get("/events")
        def events(_request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
            """Yield a good event, then one whose id holds a line break."""

            async def source() -> AsyncIterator[ServerEvent]:
                """Send one good and one bad event."""
                try:
                    yield ServerEvent("ok")
                    yield ServerEvent("x", id="1\n2")
                    yield ServerEvent("never sent")
                finally:
                    closed.append(True)

            return EventStream(source())

        async def client(server: AsyncServer) -> tuple[list[bytes], bytes]:
            """Read until the stream ends."""
            reader, writer = await connect(server)
            await send(writer, GET_EVENTS)
            await read_reply(reader, head_only=True)
            chunks = [await read_chunk(reader), await read_chunk(reader)]
            rest = await reader.read()
            writer.close()
            return chunks, rest

        with caplog.at_level(logging.ERROR, logger="pyweb.server"):
            chunks, rest = serve(app, client)
        assert chunks == [b"data: ok\n\n", b""]
        assert rest == b""
        assert closed == [True]
        assert any("could not be encoded" in r.getMessage() for r in caplog.records)

    def test_bad_stream_header_is_500_and_closes_the_source(self) -> None:
        """Answer 500 for a stream whose headers cannot be sent, closing its source."""
        hub = EventHub()
        app = App()

        @app.get("/events")
        def events(_request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
            """Return a stream with a header value that would split the response."""
            stream = EventStream(hub.subscribe())
            stream.headers._items.append(("X-Bad", "a\r\nb"))
            return stream

        reply, rest = serve(app, lambda s: exchange(s, GET_EVENTS))
        assert reply.status == SERVER_ERROR
        assert rest == b""
        assert hub.subscriber_count == 0

    def test_stream_started_while_closing_ends_at_once(self) -> None:
        """End a stream at once if the server began closing while the handler ran."""
        hub = EventHub()
        app = App()
        holder: list[AsyncServer] = []

        @app.get("/events")
        async def events(_request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
            """Start closing the server, then answer with a stream."""
            closing = spawn(holder[0].close(grace=WAIT))
            await sleep(TICK)
            del closing
            return EventStream(hub.subscribe())

        async def client(server: AsyncServer) -> tuple[int, bytes, bytes]:
            """Ask for the stream and read what comes back."""
            holder.append(server)
            reader, writer = await connect(server)
            await send(writer, GET_EVENTS)
            reply = await read_reply(reader, head_only=True)
            last = await read_chunk(reader)
            rest = await reader.read()
            writer.close()
            return reply.status, last, rest

        status, last, rest = serve(app, client)
        assert status == OK
        assert last == b""
        assert rest == b""
        assert hub.subscriber_count == 0

    def test_shutdown_runs_a_waiting_generators_cleanup(self) -> None:
        """Run the ``finally`` of a generator source when the server shuts down."""
        closed: list[bool] = []
        app = App()

        @app.get("/events")
        def events(_request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
            """Yield one event, then take a long time over the next."""

            async def source() -> AsyncIterator[ServerEvent]:
                """Say hello, then sleep."""
                try:
                    yield ServerEvent("hello")
                    await sleep(WAIT * 10)
                finally:
                    closed.append(True)

            return EventStream(source())

        async def client(server: AsyncServer) -> bytes:
            """Read the event, then close the server with a short grace."""
            reader, writer = await connect(server)
            await send(writer, GET_EVENTS)
            await read_reply(reader, head_only=True)
            event = await read_chunk(reader)
            await server.close(grace=GRACE)
            writer.close()
            return event

        assert serve(app, client) == b"data: hello\n\n"
        assert closed == [True]
