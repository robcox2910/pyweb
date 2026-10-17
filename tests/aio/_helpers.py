"""Shared helpers for real-socket tests: start a server, send bytes, read replies."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pynet.loop import run, timeout
from pynet.streams import StreamReader, StreamWriter, open_connection

from pyweb.app import App
from pyweb.aserver import AsyncServer
from pyweb.http1 import Limits

HOST = "127.0.0.1"
OK = 200
BAD_REQUEST = 400
SERVER_ERROR = 500
WAIT = 1.0
GRACE = 0.2

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
