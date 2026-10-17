"""Tests for EventPump with stand-in sockets, for failures real sockets rarely show."""

from collections.abc import AsyncIterator
from typing import cast

from pynet.errors import ConnectionClosedError
from pynet.loop import Event, run, sleep
from pynet.loop.testing import FakeClock
from pynet.streams import StreamReader, StreamWriter

from pyweb._streaming import EventPump
from pyweb.http1 import encode_chunk
from pyweb.sse import EventStream, ServerEvent, encode_event

WRITE_TIMEOUT = 1.0
HEAD = b"HTTP/1.1 200 OK\r\n\r\n"


class FakeReader:
    """A reader that sends some bytes, then waits, fails or ends."""

    def __init__(self, chunks: list[bytes], *, fail: bool = False, hang: bool = False) -> None:
        """Remember what to hand out."""
        self._chunks = chunks
        self._fail = fail
        self._hang = hang

    async def read(self, _n: int) -> bytes:
        """Return the next chunk, then fail, hang or return EOF."""
        if self._chunks:
            return self._chunks.pop(0)
        if self._fail:
            raise ConnectionResetError
        if self._hang:
            await Event().wait()
        return b""


class FakeWriter:
    """A writer that records data, and may fail or never drain."""

    def __init__(self, *, fail_after: int | None = None, stall: bool = False) -> None:
        """Choose how the writer misbehaves."""
        self.written: list[bytes] = []
        self.aborted = False
        self._fail_after = fail_after
        self._stall = stall

    def write(self, data: bytes) -> None:
        """Record data, or fail once ``fail_after`` writes have happened."""
        if self._fail_after is not None and len(self.written) >= self._fail_after:
            raise ConnectionClosedError
        self.written.append(data)

    async def drain(self) -> None:
        """Return at once, or never when stalled."""
        if self._stall:
            await Event().wait()

    def abort(self) -> None:
        """Note the abort."""
        self.aborted = True


async def forever(closed: list[bool]) -> AsyncIterator[ServerEvent]:
    """Yield an event every tick, noting cleanup."""
    try:
        n = 0
        while True:
            n += 1
            yield ServerEvent(str(n))
            await sleep(WRITE_TIMEOUT / 10)
    finally:
        closed.append(True)


def pump(reader: FakeReader, writer: FakeWriter, closed: list[bool]) -> EventPump:
    """Build a pump over the stand-ins."""
    stream = EventStream(forever(closed))
    return EventPump(
        stream, cast("StreamReader", reader), cast("StreamWriter", writer), WRITE_TIMEOUT
    )


def go(job: EventPump) -> None:
    """Run the pump to the end on a fake clock."""
    run(job.run(HEAD), clock=FakeClock(autojump=True))


class TestPumpFailures:
    """Test that every way of losing the client closes the source."""

    def test_failed_head_write_closes_the_source(self) -> None:
        """Stop before streaming when the head cannot be written."""
        closed: list[bool] = []
        writer = FakeWriter(fail_after=0)
        go(pump(FakeReader([], hang=True), writer, closed))
        assert writer.written == []
        assert closed == []  # the generator never started, so has nothing to clean up

    def test_failed_event_write_closes_the_source(self) -> None:
        """Stop when writing an event fails."""
        closed: list[bool] = []
        writer = FakeWriter(fail_after=2)
        go(pump(FakeReader([], hang=True), writer, closed))
        assert writer.written == [HEAD, encode_chunk(encode_event(ServerEvent("1")))]
        assert closed == [True]

    def test_stalled_client_is_aborted(self) -> None:
        """Abort a client that stops reading for longer than the write timeout."""
        closed: list[bool] = []
        writer = FakeWriter(stall=True)
        go(pump(FakeReader([], hang=True), writer, closed))
        assert writer.aborted
        assert closed == []

    def test_reader_error_counts_as_gone(self) -> None:
        """Treat a reset while watching the reader as the client leaving."""
        closed: list[bool] = []
        go(pump(FakeReader([b"stray bytes"], fail=True), FakeWriter(), closed))
        assert closed == [True]
