"""Your first async PyWeb app -- a tiny shop that serves many customers at once.

Run it with: uv run python examples/async_hello.py
It starts a server on a free port, asks it for a greeting, and stops.
"""

from pynet.loop import run
from pynet.streams import open_connection

from pyweb.app import App
from pyweb.aserver import AsyncServer
from pyweb.messages import HttpRequest

app = App()


@app.get("/hello/<name>")
async def hello(_request: HttpRequest, name: str) -> dict[str, str]:
    """Greet someone by name."""
    return {"greeting": f"Hello, {name}!"}


async def demo() -> None:
    """Start the server, send one raw HTTP letter and print the reply."""
    async with AsyncServer(app, access_log=False) as server:
        print(f"The shop is open on port {server.port}.")
        reader, writer = await open_connection("127.0.0.1", server.port)
        writer.write(b"GET /hello/Sam HTTP/1.1\r\nHost: shop\r\nConnection: close\r\n\r\n")
        await writer.drain()
        reply = await reader.read()
        writer.close()
        head, _, body = reply.partition(b"\r\n\r\n")
        print("Status:", head.split(b"\r\n")[0].decode())
        print("Body:", body.decode())
    print("The shop is closed.")


def main() -> None:
    """Run the demo."""
    run(demo())


if __name__ == "__main__":
    main()
