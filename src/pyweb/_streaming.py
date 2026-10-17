"""Write one ``EventStream`` to a socket: the radio presenter at work.

The presenter reads out each bit of news the moment it arrives (one
chunk per event), says "still here!" when it has been quiet for a while
(a keep-alive comment), keeps one ear on the door to notice a listener
leaving (the socket reaching end-of-file), and signs off properly
(the terminating chunk) when the station closes for the night.

This module is private: ``pyweb._conn.Connection`` uses ``EventPump``.
"""

import logging

from pynet.errors import ConnectionClosedError
from pynet.loop import Event, Task, spawn, timeout
from pynet.streams import IncompleteReadError, StreamReader, StreamWriter

from pyweb.http1 import encode_chunk
from pyweb.sse import KEEPALIVE_COMMENT, EventStream, ServerEvent, encode_event

logger = logging.getLogger("pyweb.server")

_READ_SIZE = 4096
_GONE = (ConnectionClosedError, IncompleteReadError, OSError)


async def _next_event(stream: EventStream) -> ServerEvent | None:
    """Return the source's next event, or ``None`` when it has run out."""
    try:
        return await anext(stream.source)
    except StopAsyncIteration:
        return None


async def _settle(task: Task[object]) -> None:
    """Wait until ``task`` has finished, without raising its result."""
    if not task.done():
        finished = Event()
        task.add_done_callback(lambda _task: finished.set())
        await finished.wait()
    if not task.cancelled():
        task.exception()  # mark any error as seen


class EventPump:
    """Send events from one stream until it ends, the client leaves, or we stop.

    Args:
        stream: The response to send.
        reader: The socket's reader, watched for the client hanging up.
        writer: The socket's writer.
        write_timeout: Seconds a write may take before the client counts as gone.

    Attributes:
        body_bytes: How many body bytes (chunk framing included) were written.

    """

    def __init__(
        self,
        stream: EventStream,
        reader: StreamReader,
        writer: StreamWriter,
        write_timeout: float,
    ) -> None:
        """Remember the stream and the socket."""
        self._stream = stream
        self._reader = reader
        self._writer = writer
        self._write_timeout = write_timeout
        self._wake = Event()
        self._stopping = False
        self._gone = False
        self.body_bytes = 0

    def stop(self) -> None:
        """Ask the pump to sign off (graceful shutdown)."""
        self._stopping = True
        self._wake.set()

    async def run(self, head: bytes) -> None:
        """Write the head, then the events; always close the source at the end."""
        pending: Task[object] | None = None
        watcher: Task[object] | None = None
        try:
            if not await self._send(head, body=False):
                return
            watcher = spawn(self._watch_reader())
            pending = self._fetch()
            while True:
                self._wake.clear()
                if self._gone:
                    return
                if self._stopping:
                    await self._send(encode_chunk(b""))
                    return
                if pending.done():
                    data = self._encoded(pending)
                    if data is None:
                        await self._send(encode_chunk(b""))
                        return
                    if await self._send(encode_chunk(data)):
                        pending = self._fetch()
                    continue
                await self._idle()
        finally:
            for task in (watcher, pending):
                if task is not None:
                    task.cancel()
                    await _settle(task)
            await self._stream.aclose()

    def _fetch(self) -> Task[object]:
        """Start waiting for the next event; wake the loop when it comes."""
        task: Task[object] = spawn(_next_event(self._stream))
        task.add_done_callback(lambda _task: self._wake.set())
        return task

    def _result(self, task: Task[object]) -> ServerEvent | None:
        """Return a finished fetch's event; a broken source counts as ended."""
        error = task.exception()
        if error is not None:
            logger.error("The event source failed", exc_info=error)
            return None
        result = task.result()
        return result if isinstance(result, ServerEvent) else None

    def _encoded(self, task: Task[object]) -> bytes | None:
        """Return the next event's bytes, or ``None`` to end the stream.

        An event that cannot be encoded (say, a line break in its id) is
        logged and ends the stream, just like a source that fails.
        """
        event = self._result(task)
        if event is None:
            return None
        try:
            return encode_event(event)
        except ValueError:
            logger.exception("An event could not be encoded; ending the stream")
            return None

    async def _idle(self) -> None:
        """Wait for something to happen, sending a keep-alive if nothing does."""
        try:
            async with timeout(self._stream.keepalive):
                await self._wake.wait()
        except TimeoutError:
            await self._send(encode_chunk(KEEPALIVE_COMMENT))

    async def _send(self, data: bytes, *, body: bool = True) -> bool:
        """Write and flush ``data``; on failure mark the client gone and return False."""
        try:
            async with timeout(self._write_timeout):
                self._writer.write(data)
                await self._writer.drain()
        except TimeoutError:
            self._writer.abort()  # the client stopped reading
            self._gone = True
        except _GONE:
            self._gone = True
        else:
            if body:
                self.body_bytes += len(data)
            return True
        return False

    async def _watch_reader(self) -> None:
        """Read (and ignore) until the client hangs up, then wake the loop."""
        try:
            while await self._reader.read(_READ_SIZE):
                pass
        except _GONE:
            pass
        self._gone = True
        self._wake.set()
