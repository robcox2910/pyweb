"""Tests for server-sent events: encoding, EventStream, EventHub and TestClient.stream."""

from collections.abc import AsyncIterator, Coroutine
from typing import Any, Self

import pytest
from pynet.loop import run, sleep, spawn
from pynet.loop.testing import FakeClock

from pyweb.app import App
from pyweb.messages import Headers, HttpRequest, StreamingResponse, text
from pyweb.sse import (
    DEFAULT_KEEPALIVE,
    DEFAULT_MAX_PENDING,
    EventHub,
    EventStream,
    ServerEvent,
    encode_event,
)
from pyweb.testing import TestClient

OK = 200
ACCEPTED = 202
RETRY_MS = 1000
SMALL_QUEUE = 2
PUBLISHED = 5
TWO = 2
THREE = 3
NAP = 0.5
SPEC_KEEPALIVE = 15.0
SPEC_MAX_PENDING = 100


def go[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on a fresh loop with an auto-jumping fake clock."""
    return run(coro, clock=FakeClock(autojump=True))


async def numbers(count: int, closed: list[bool]) -> AsyncIterator[ServerEvent]:
    """Yield ``count`` numbered events, noting when the generator is cleaned up."""
    try:
        for n in range(1, count + 1):
            yield ServerEvent(str(n), id=str(n))
    finally:
        closed.append(True)


class TestEncoding:
    """Test how one event becomes bytes on the wire."""

    def test_plain_data(self) -> None:
        """Write a data line and a blank line."""
        assert encode_event(ServerEvent("hi")) == b"data: hi\n\n"

    def test_multi_line_data_becomes_several_data_lines(self) -> None:
        """Split data on LF, CRLF and CR."""
        event = ServerEvent("one\ntwo\r\nthree\rfour")
        assert encode_event(event) == b"data: one\ndata: two\ndata: three\ndata: four\n\n"

    def test_empty_data(self) -> None:
        """Still write one (empty) data line."""
        assert encode_event(ServerEvent("")) == b"data: \n\n"

    def test_event_id_and_retry(self) -> None:
        """Write the optional fields before the data."""
        event = ServerEvent("x", event="tick", id="7", retry=RETRY_MS)
        assert encode_event(event) == b"event: tick\nid: 7\nretry: 1000\ndata: x\n\n"

    def test_unicode_is_utf8(self) -> None:
        """Encode text as UTF-8."""
        assert encode_event(ServerEvent("café")) == "data: café\n\n".encode()

    @pytest.mark.parametrize(
        "event",
        [
            ServerEvent("x", event="a\nb"),
            ServerEvent("x", event="a\rb"),
            ServerEvent("x", id="1\n2"),
            ServerEvent("x", id="1\r2"),
        ],
    )
    def test_line_breaks_in_event_or_id_are_refused(self, event: ServerEvent) -> None:
        """Refuse a field that would split into a forged second field."""
        with pytest.raises(ValueError, match="line break"):
            encode_event(event)

    def test_negative_retry_is_refused(self) -> None:
        """Refuse a retry time below zero."""
        with pytest.raises(ValueError, match="retry"):
            encode_event(ServerEvent("x", retry=-1))


class TestEventStream:
    """Test the EventStream response object."""

    def test_headers_and_defaults(self) -> None:
        """Set the SSE headers, status 200 and a 15 s keep-alive."""
        stream = EventStream(numbers(1, []))
        assert isinstance(stream, StreamingResponse)
        assert stream.status == OK
        assert stream.keepalive == DEFAULT_KEEPALIVE == SPEC_KEEPALIVE
        assert stream.headers.get("Content-Type") == "text/event-stream"
        assert stream.headers.get("Cache-Control") == "no-store"
        assert stream.headers.get("Transfer-Encoding") == "chunked"

    def test_custom_status_and_headers_are_kept(self) -> None:
        """Keep a handler's own status and extra headers."""
        headers = Headers([("X-Room", "lobby")])
        stream = EventStream(numbers(1, []), status=ACCEPTED, headers=headers, keepalive=NAP)
        assert stream.status == ACCEPTED
        assert stream.keepalive == NAP
        assert stream.headers.get("X-Room") == "lobby"
        assert stream.headers.get("Content-Type") == "text/event-stream"

    def test_aclose_runs_the_generators_cleanup(self) -> None:
        """Close a started generator so its ``finally`` runs, once only."""
        closed: list[bool] = []

        async def main() -> None:
            """Take one event, then close twice."""
            stream = EventStream(numbers(THREE, closed))
            assert (await anext(stream.source)).data == "1"
            await stream.aclose()
            await stream.aclose()

        go(main())
        assert closed == [True]

    def test_aclose_on_a_plain_iterator_is_harmless(self) -> None:
        """Do nothing for a source without ``aclose``."""

        class Plain:
            """An async iterator with no aclose method."""

            def __aiter__(self) -> Self:
                """Return itself."""
                return self

            async def __anext__(self) -> ServerEvent:
                """Never yield anything."""
                raise StopAsyncIteration

        go(EventStream(Plain()).aclose())


class TestEventHub:
    """Test the broadcast hub."""

    def test_fan_out(self) -> None:
        """Deliver one published event to every subscriber."""

        async def main() -> tuple[int, ServerEvent, ServerEvent]:
            """Subscribe twice and publish once."""
            hub = EventHub()
            first, second = hub.subscribe(), hub.subscribe()
            reached = hub.publish(ServerEvent("hello"))
            return reached, await anext(first), await anext(second)

        reached, a, b = go(main())
        assert reached == TWO
        assert a == b == ServerEvent("hello")

    def test_waiting_subscriber_is_woken(self) -> None:
        """Wake a subscriber that was already waiting when the event came."""

        async def main() -> ServerEvent:
            """Start waiting, then publish a little later."""
            hub = EventHub()
            subscription = hub.subscribe()
            waiting = spawn(_first(subscription))
            await sleep(NAP)
            hub.publish(ServerEvent("late"))
            return await waiting

        assert go(main()) == ServerEvent("late")

    def test_slow_subscriber_drops_oldest_and_counts(self) -> None:
        """Keep only the newest events for a slow listener; a fast one gets all."""

        async def main() -> tuple[list[str], int, list[str], int]:
            """Publish five events to a small queue and a default one."""
            hub = EventHub()
            slow = hub.subscribe(max_pending=SMALL_QUEUE)
            fast = hub.subscribe()
            for n in range(1, PUBLISHED + 1):
                hub.publish(ServerEvent(str(n)))
            hub.close()
            slow_got = [event.data async for event in slow]
            fast_got = [event.data async for event in fast]
            return slow_got, slow.dropped, fast_got, fast.dropped

        slow_got, slow_dropped, fast_got, fast_dropped = go(main())
        assert slow_got == ["4", "5"]
        assert slow_dropped == PUBLISHED - SMALL_QUEUE
        assert fast_got == ["1", "2", "3", "4", "5"]
        assert fast_dropped == 0

    def test_close_ends_every_subscription(self) -> None:
        """End iteration for subscribers, even ones already waiting."""

        async def main() -> tuple[list[ServerEvent], int, int]:
            """Wait on a subscription, then close the hub."""
            hub = EventHub()
            subscription = hub.subscribe()
            collected = spawn(_collect(subscription))
            await sleep(NAP)
            hub.close()
            return await collected, hub.subscriber_count, hub.publish(ServerEvent("x"))

        got, count, reached = go(main())
        assert got == []
        assert count == 0
        assert reached == 0

    def test_subscribe_after_close_ends_at_once(self) -> None:
        """Give a late subscriber an already-finished subscription."""

        async def main() -> list[ServerEvent]:
            """Close first, then subscribe."""
            hub = EventHub()
            hub.close()
            subscription = hub.subscribe()
            got = await _collect(subscription)
            with pytest.raises(StopAsyncIteration):
                await anext(subscription)  # still finished when asked again
            return got

        assert go(main()) == []

    def test_aclose_unsubscribes(self) -> None:
        """Remove a subscriber when its subscription is closed."""

        async def main() -> tuple[int, int, int]:
            """Subscribe, then close the subscription."""
            hub = EventHub()
            subscription = hub.subscribe()
            before = hub.subscriber_count
            await subscription.aclose()
            await subscription.aclose()
            return before, hub.subscriber_count, hub.publish(ServerEvent("x"))

        assert go(main()) == (1, 0, 0)

    def test_default_and_bad_max_pending(self) -> None:
        """Default to 100 pending events and refuse fewer than one."""
        assert DEFAULT_MAX_PENDING == SPEC_MAX_PENDING
        with pytest.raises(ValueError, match="max_pending"):
            EventHub().subscribe(max_pending=0)


async def _first(subscription: AsyncIterator[ServerEvent]) -> ServerEvent:
    """Wait for the next event."""
    return await anext(subscription)


async def _collect(subscription: AsyncIterator[ServerEvent]) -> list[ServerEvent]:
    """Gather every event until the subscription ends."""
    return [event async for event in subscription]


class TestClientStream:
    """Test TestClient.stream."""

    def test_takes_count_events_then_closes_the_source(self) -> None:
        """Return the first events and run the generator's cleanup."""
        app = App()
        closed: list[bool] = []

        @app.get("/ticks")
        def ticks(_request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
            """Stream numbered ticks."""
            return EventStream(numbers(PUBLISHED, closed))

        events = go(TestClient(app).stream("/ticks", count=TWO))
        assert [event.data for event in events] == ["1", "2"]
        assert closed == [True]

    def test_short_stream_gives_fewer_events(self) -> None:
        """Stop early when the source runs out."""
        app = App()

        @app.get("/ticks")
        def ticks(_request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
            """Stream one tick."""
            return EventStream(numbers(1, []))

        events = go(TestClient(app).stream("/ticks", count=THREE))
        assert [event.data for event in events] == ["1"]

    def test_last_event_id_reaches_the_handler(self) -> None:
        """Pass headers such as Last-Event-ID through to the handler."""
        app = App()

        @app.get("/ticks")
        def ticks(request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
            """Say where the listener left off."""

            async def resume() -> AsyncIterator[ServerEvent]:
                """Yield one event naming the last id."""
                yield ServerEvent(f"after {request.last_event_id}")

            return EventStream(resume())

        client = TestClient(app)
        events = go(client.stream("/ticks", count=1, headers={"Last-Event-ID": "41"}))
        assert events == [ServerEvent("after 41")]

    def test_non_stream_answer_is_an_error(self) -> None:
        """Refuse to stream from a route that answers normally."""
        app = App()

        @app.get("/plain")
        def plain(_request: HttpRequest) -> object:  # pyright: ignore[reportUnusedFunction]
            """Answer with text."""
            return text("no stream here")

        with pytest.raises(TypeError, match="EventStream"):
            go(TestClient(app).stream("/plain", count=1))
