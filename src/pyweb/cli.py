"""PyWeb command-line interface.

Start a web server that serves your routes. With no flag you get the
original threaded demo; with ``--async`` you get the busy-restaurant
demo: a page, a live clock (server-sent events) and a WebSocket echo.
"""

import argparse
import sys
from collections.abc import AsyncIterator, Sequence

from pynet.loop import run, sleep
from pynet.ws import WebSocket

from pyweb.app import App
from pyweb.aserver import AsyncServer
from pyweb.messages import HttpRequest, HttpResponse, html
from pyweb.request import Request
from pyweb.response import Response, html_response
from pyweb.router import Router
from pyweb.server import DEFAULT_HOST, DEFAULT_PORT, Server
from pyweb.sse import EventStream, ServerEvent

__all__ = ["demo_app", "main"]

_PAGE = """<!doctype html>
<title>PyWeb Async</title>
<h1>Welcome to async PyWeb!</h1>
<p>The time is: <b id="clock">waiting...</b></p>
<p><input id="msg" value="hello"> <button id="send">Send</button> <span id="reply"></span></p>
<script>
const events = new EventSource("/clock");
events.onmessage = (e) => { document.getElementById("clock").textContent = e.data; };
const socket = new WebSocket("ws://" + location.host + "/echo");
socket.onmessage = (e) => { document.getElementById("reply").textContent = e.data; };
document.getElementById("send").onclick = () => socket.send(document.getElementById("msg").value);
</script>
"""


def demo_app() -> App:
    """Build the app behind ``pyweb --async``: a page, a clock and an echo."""
    app = App()

    @app.get("/")
    def index(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Serve the demo page."""
        return html(_PAGE)

    @app.get("/clock")
    def clock(_request: HttpRequest) -> EventStream:  # pyright: ignore[reportUnusedFunction]
        """Stream one tick per second."""

        async def ticks() -> AsyncIterator[ServerEvent]:
            """Yield a tick now and then one every second."""
            count = 0
            while True:
                count += 1
                yield ServerEvent(f"tick {count}")
                await sleep(1.0)

        return EventStream(ticks())

    @app.websocket("/echo")
    async def echo(ws: WebSocket, _request: HttpRequest) -> None:  # pyright: ignore[reportUnusedFunction]
        """Send every message back with ``echo:`` in front."""
        while True:
            message = await ws.recv()
            await ws.send_text(f"echo: {message}")

    return app


def _run_async() -> None:
    """Run the async demo on 127.0.0.1:8000 until Ctrl-C."""

    async def serve() -> None:
        """Start the server, say hello and wait."""
        server = AsyncServer(demo_app())
        await server.start(DEFAULT_HOST, DEFAULT_PORT)
        sys.stdout.write(f"Async PyWeb serving on http://{DEFAULT_HOST}:{server.port}\n")
        sys.stdout.write("Press Ctrl+C to stop.\n")
        sys.stdout.flush()
        try:
            await server.serve_forever()
        finally:
            await server.close()

    try:
        run(serve())
    except KeyboardInterrupt:
        sys.stdout.write("\nServer stopped.\n")


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point for the ``pyweb`` command. Start a demo server."""
    parser = argparse.ArgumentParser(prog="pyweb", description="Start a PyWeb demo server.")
    parser.add_argument(
        "--async", dest="use_async", action="store_true", help="serve the async demo instead"
    )
    args = parser.parse_args(argv)
    if args.use_async:
        _run_async()
        return

    router = Router()

    @router.get("/")
    def index(_request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        """Serve the homepage."""
        return html_response("<h1>Welcome to PyWeb!</h1><p>Your web server is running.</p>")

    server = Server(router, host=DEFAULT_HOST, port=DEFAULT_PORT)
    sys.stdout.write(f"PyWeb serving on http://{DEFAULT_HOST}:{DEFAULT_PORT}\n")
    sys.stdout.write("Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stdout.write("\nServer stopped.\n")
