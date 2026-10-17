"""A radio station that broadcasts the time -- server-sent events.

Run it with: uv run python examples/sse_clock.py
It starts a server, tunes in a listener for three ticks, then stops.
"""

from collections.abc import AsyncIterator

from pynet.loop import run, sleep
from pynet.streams import StreamReader, open_connection

from pyweb.app import App
from pyweb.aserver import AsyncServer
from pyweb.messages import HttpRequest
from pyweb.sse import EventStream, ServerEvent

TICKS = 3
TICK_SECONDS = 0.1

app = App()


@app.get("/clock")
def clock(_request: HttpRequest) -> EventStream:
    """Broadcast a numbered tick every little while."""

    async def ticks() -> AsyncIterator[ServerEvent]:
        """Yield tick 1, 2, 3, ... forever."""
        count = 0
        while True:
            count += 1
            yield ServerEvent(f"tick {count}")
            await sleep(TICK_SECONDS)

    return EventStream(ticks())


async def read_chunk(reader: StreamReader) -> str:
    """Read one chunk of the chunked reply and return its text."""
    size = int((await reader.read_until(b"\r\n"))[:-2], 16)
    return (await reader.read_exactly(size + 2))[:-2].decode()


async def demo() -> None:
    """Listen to the clock for a few ticks."""
    async with AsyncServer(app, access_log=False) as server:
        reader, writer = await open_connection("127.0.0.1", server.port)
        writer.write(b"GET /clock HTTP/1.1\r\n\r\n")
        await writer.drain()
        head = await reader.read_until(b"\r\n\r\n")
        print("Tuned in:", head.decode().split("\r\n")[0])
        for _ in range(TICKS):
            print("Heard:", (await read_chunk(reader)).strip())
        writer.close()
        print("Listener switched off.")


def main() -> None:
    """Run the demo."""
    run(demo())


if __name__ == "__main__":
    main()
