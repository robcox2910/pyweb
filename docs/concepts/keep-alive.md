# Keep-Alive

## "Anything Else?"

At an ice-cream van you don't walk away and join the back of the queue
after every scoop. You stay at the window: "One vanilla, please... and a
mint... oh, and a lemon -- that's all, thanks!" The seller keeps serving
you until you say you're done, or until you stand there so long without
ordering that they move on.

HTTP connections work the same way. Opening a new connection is like
joining the queue -- it takes time. So an HTTP/1.1 browser keeps the
connection open and sends its next request down the same one. That is
**keep-alive**.

| At the van | In HTTP |
|------------|---------|
| Staying at the window | Keeping the connection open |
| "That's all, thanks!" | The header `Connection: close` |
| Standing there saying nothing | An idle connection |
| The seller moving on | `keepalive_timeout` running out |

## Try It

Three scoops, one connection. The last request says `Connection: close`,
so the server answers and then hangs up:

```python
from pynet.loop import run
from pynet.streams import open_connection

from pyweb.aserver import AsyncServer
from pyweb.app import App

app = App()


@app.get("/scoop/<flavour>")
def scoop(request, flavour):
    return f"<p>One scoop of {flavour}</p>"


async def ask(reader, writer, request):
    writer.write(request)
    await writer.drain()
    head = await reader.read_until(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    length = next(int(line.split(": ")[1]) for line in lines if line.startswith("Content-Length"))
    body = await reader.read_exactly(length)
    connection = [line for line in lines if line.startswith("Connection")]
    print(lines[0], body.decode(), connection)


async def main():
    async with AsyncServer(app, access_log=False) as server:
        reader, writer = await open_connection("127.0.0.1", server.port)
        await ask(reader, writer, b"GET /scoop/vanilla HTTP/1.1\r\n\r\n")
        await ask(reader, writer, b"GET /scoop/mint HTTP/1.1\r\n\r\n")
        print("connections open:", server.connection_count)
        await ask(reader, writer, b"GET /scoop/lemon HTTP/1.1\r\nConnection: close\r\n\r\n")
        print("anything more?", await reader.read())
        writer.close()


run(main())
```

Output:

```
HTTP/1.1 200 OK <p>One scoop of vanilla</p> []
HTTP/1.1 200 OK <p>One scoop of mint</p> []
connections open: 1
HTTP/1.1 200 OK <p>One scoop of lemon</p> ['Connection: close']
anything more? b''
```

Notice how `Content-Length` matters here: on a kept-alive connection it
is the *only* way the client knows where one reply ends and the next
begins.

## The Rules

- **HTTP/1.1** keeps the connection open unless the request says
  `Connection: close` (or the handler sets `Connection: close` on its
  response).
- **HTTP/1.0** closes after one reply unless the request says
  `Connection: keep-alive` (and then the reply says it too).
- After an **error about the request itself** (a bad head, a body that
  is too big, a timeout) the server always closes: it can't be sure where
  the next request would start.
- While the server is **shutting down**, the next reply says
  `Connection: close`.
- An idle connection is closed **quietly** -- no reply at all -- after
  `keepalive_timeout` seconds (5 by default).

A client may even send two requests at once without waiting
("pipelining"); the server answers them one at a time, in order.

## What We Test

- Two requests on one HTTP/1.1 connection both get answers.
- Two requests sent in one packet are answered in order.
- `Connection: close` is honoured: the reply says so and the socket closes.
- A handler that sets `Connection: close` gets the connection closed
  after its reply.
- HTTP/1.0 closes by default and stays open with `Connection: keep-alive`.
- A `404` does not close the connection; the next request still works.
- An idle keep-alive connection closes after `keepalive_timeout` without
  sending anything.

## Next Up

What stops someone from standing at the window forever, or ordering a
million scoops? See [Limits and Timeouts](limits-and-timeouts.md).
