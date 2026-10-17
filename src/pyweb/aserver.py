"""AsyncServer: serve an ``App`` over HTTP/1.1 on PyNet's event loop.

Think of a shop. The *door* is the listening socket: customers (browser
connections) come in through it. Each customer gets their own counter
(a ``Connection``) and the one shopkeeper -- the event loop -- moves
between counters whenever a customer is busy thinking, so a slow
customer never holds up the queue. There is a fire-safety limit on how
many people may be inside (``max_connections``); anyone over the limit
is politely told "come back later" (``503``).

At closing time the shopkeeper locks the door, tells anyone who is just
browsing to leave, lets customers already being served finish (up to
``grace`` seconds), then turns off the lights (``app.shutdown()``).

Example:
    >>> from pynet.loop import run
    >>> async def main():
    ...     async with AsyncServer(App()) as server:
    ...         print(server.port > 0)
    >>> run(main())
    True

"""

from types import TracebackType
from typing import Self

from pynet.loop import Event, timeout
from pynet.streams import Server, StreamReader, StreamWriter, start_server

from pyweb._conn import Connection
from pyweb.app import App
from pyweb.http1 import Limits

__all__ = ["DEFAULT_GRACE", "AsyncServer"]

DEFAULT_GRACE = 5.0
_DEFAULT_LIMITS = Limits()
_UNAVAILABLE = 503


class AsyncServer:
    """Serve an ``App`` on a TCP port, one task per connection.

    Args:
        app: The application to answer requests with.
        limits: Size, count and time limits (see ``pyweb.http1.Limits``).
        access_log: Log one line per request to the ``pyweb.access`` logger.

    """

    def __init__(
        self, app: App, *, limits: Limits = _DEFAULT_LIMITS, access_log: bool = True
    ) -> None:
        """Prepare the server; nothing listens until ``start``."""
        self._app = app
        self._limits = limits
        self._access_log = access_log
        self._server: Server | None = None
        self._connections: set[Connection] = set()
        self._drained = Event()
        self._drained.set()
        self._closing = False
        self._closed = Event()

    @property
    def port(self) -> int:
        """Return the port the server is listening on.

        Raises:
            RuntimeError: If the server has not started.

        """
        if self._server is None:
            msg = "the server has not started yet"
            raise RuntimeError(msg)
        return self._server.port

    @property
    def connection_count(self) -> int:
        """Return how many connections are being served right now."""
        return len(self._connections)

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> None:
        """Run the app's startup hooks, then start listening.

        Port 0 asks the operating system for any free port; read it back
        from ``port``.

        Raises:
            RuntimeError: If the server was already started.

        """
        if self._server is not None:
            msg = "the server was already started"
            raise RuntimeError(msg)
        await self._app.startup()
        try:
            self._server = await start_server(self._on_connection, host, port)
        except BaseException:
            await self._app.shutdown()
            raise

    async def serve_forever(self) -> None:
        """Serve until this task is cancelled, then close gracefully."""
        if self._server is None:
            await self.start()
        try:
            await Event().wait()  # nobody sets it: wait until cancelled
        finally:
            await self.close()

    async def close(self, grace: float = DEFAULT_GRACE) -> None:
        """Stop accepting, let in-flight requests finish, then shut the app down.

        Idle keep-alive connections are closed at once. Requests still
        running after ``grace`` seconds are cancelled. Closing twice is
        harmless; the second call waits for the first to finish.
        """
        if self._server is None:
            return
        if self._closing:
            await self._closed.wait()
            return
        self._closing = True
        self._server.close()
        await self._server.wait_closed()
        for connection in list(self._connections):
            connection.closing = True
            if connection.idle:
                connection.cancel()
        try:
            async with timeout(grace):
                await self._drained.wait()
        except TimeoutError:
            for connection in list(self._connections):
                connection.cancel()
            await self._drained.wait()
        await self._app.shutdown()
        self._closed.set()

    async def __aenter__(self) -> Self:
        """Start (on a free port, if not already started) and return the server."""
        if self._server is None:
            await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the server gracefully."""
        await self.close()

    async def _on_connection(self, reader: StreamReader, writer: StreamWriter) -> None:
        """Serve one accepted connection, or turn it away if we are full."""
        connection = Connection(
            self._app, self._limits, reader, writer, access_log=self._access_log
        )
        if self._closing or len(self._connections) >= self._limits.max_connections:
            await connection.reject(_UNAVAILABLE)
            return
        self._connections.add(connection)
        self._drained.clear()
        try:
            await connection.serve()
        finally:
            self._connections.discard(connection)
            if not self._connections:
                self._drained.set()
