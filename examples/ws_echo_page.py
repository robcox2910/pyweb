"""A web page plus a WebSocket echo -- a phone line that repeats what you say.

Run it with: uv run python examples/ws_echo_page.py
It starts a server, fetches the page, chats over the WebSocket, and stops.
"""

from pynet.loop import run
from pynet.streams import open_connection
from pynet.ws import WebSocket, connect_websocket

from pyweb.app import App
from pyweb.aserver import AsyncServer
from pyweb.messages import HttpRequest, HttpResponse, html

PAGE = """<!doctype html>
<h1>Echo chamber</h1>
<input id="msg"><button onclick="socket.send(msg.value)">Say it</button>
<script>
const socket = new WebSocket("ws://" + location.host + "/echo");
socket.onmessage = (e) => document.title = e.data;
</script>
"""

app = App()


@app.get("/")
def page(_request: HttpRequest) -> HttpResponse:
    """Serve the page that talks to the echo."""
    return html(PAGE)


@app.websocket("/echo")
async def echo(ws: WebSocket, _request: HttpRequest) -> None:
    """Repeat each message back with ``echo:`` in front."""
    while True:
        message = await ws.recv()
        await ws.send_text(f"echo: {message}")


async def demo() -> None:
    """Fetch the page, then chat with the echo."""
    async with AsyncServer(app, access_log=False) as server:
        reader, writer = await open_connection("127.0.0.1", server.port)
        writer.write(b"GET / HTTP/1.1\r\nConnection: close\r\n\r\n")
        await writer.drain()
        reply = await reader.read()
        writer.close()
        print("Page:", reply.split(b"\r\n")[0].decode())
        ws = await connect_websocket("127.0.0.1", server.port, "/echo")
        for word in ("hello", "anyone there?"):
            await ws.send_text(word)
            print("Heard:", await ws.recv())
        await ws.close()
        print("Hung up.")


def main() -> None:
    """Run the demo."""
    run(demo())


if __name__ == "__main__":
    main()
