# The Async Server

## One Shopkeeper, Many Counters

Imagine a small shop with **one shopkeeper** and lots of counters. A
customer walks in and steps up to a counter. While that customer is
deciding what to buy, the shopkeeper doesn't stand and stare -- they go
and help someone at another counter, then come back when the first
customer is ready.

`AsyncServer` is that shop:

| In the shop | In PyWeb |
|-------------|----------|
| The front door | The listening socket on a port |
| A customer at a counter | One connection from a browser |
| The shopkeeper | PyNet's event loop |
| The stock room | Your `App` (routes, middleware, handlers) |
| "Come back later, we're full!" | `503 Service Unavailable` |
| Opening and closing up | `on_startup` and `on_shutdown` hooks |

Every connection gets its own task, so a slow customer at counter A
never holds up a quick one at counter B -- as long as the handler
`await`s while it waits (see [Handlers and Threads](handlers-and-threads.md)).

## Try It

This starts a real server on a free port, talks to it with raw bytes --
exactly what a browser would send -- and shuts it down again:

```python
from pynet.loop import run
from pynet.streams import open_connection

from pyweb.aserver import AsyncServer
from pyweb.app import App

app = App()


@app.on_startup
def opening():
    print("The shop is open!")


@app.on_shutdown
def closing():
    print("The shop is closed.")


@app.get("/hello/<name>")
async def hello(request, name):
    return {"greeting": f"Hello, {name}!"}


async def main():
    async with AsyncServer(app, access_log=False) as server:
        print("listening:", server.port > 0)
        reader, writer = await open_connection("127.0.0.1", server.port)
        writer.write(b"GET /hello/Sam HTTP/1.1\r\nHost: shop\r\nConnection: close\r\n\r\n")
        await writer.drain()
        reply = await reader.read()  # read until the server closes
        writer.close()
        head, _, body = reply.partition(b"\r\n\r\n")
        print(head.split(b"\r\n")[0].decode())
        print(body.decode())


run(main())
```

Output:

```
The shop is open!
listening: True
HTTP/1.1 200 OK
{"greeting": "Hello, Sam!"}
The shop is closed.
```

`async with` starts the server on a free port (port `0` means "any free
port, please") and closes it at the end. You can also call
`await server.start(host, port)` and `await server.close()` yourself, or
`await server.serve_forever()` to keep serving until the task is
cancelled (for example with Ctrl+C).

## What the Server Adds

Your handler only builds the parcel. The server sticks on the postage:

- `Date` -- when the reply was sent.
- `Server: PyWeb` -- who sent it.
- `Content-Length` -- how many bytes the body has, so the browser knows
  where the reply ends. (Not on `204 No Content` or `304`, which never
  have a body.)
- `Connection: close` -- only when the server is about to hang up.

For a `HEAD` request the server sends the same headers as `GET` --
including the `Content-Length` the `GET` body *would* have had -- but no
body.

The server also refuses to send a header containing a newline. A sneaky
newline could split one reply into two ("response splitting"), so
instead the visitor gets a plain `500`.

## Closing Up Gracefully

`await server.close(grace=5.0)` works like closing a shop at the end of
the day:

1. **Lock the door** -- stop accepting new connections.
2. **Tell browsers to leave** -- idle connections close straight away.
3. **Let people finish paying** -- requests already being handled get up
   to `grace` seconds to finish. Their reply says `Connection: close`.
4. **Turn out the lights** -- anything still running after `grace` is
   cancelled, then the app's `on_shutdown` hooks run.

## The Access Log

Every request writes one line to the `pyweb.access` logger, like:

```
GET /hello/Sam 200 27 0.4ms
```

That is method, path, status, body bytes and how long it took. The query
string is left out on purpose -- it might hold a secret token. Turn it
off with `AsyncServer(app, access_log=False)`.

## What We Test

- A `GET` and a `POST` with a body round-trip over a real socket, with
  `Date`, `Server` and `Content-Length` added.
- **A slow handler on one connection does not delay a fast request on
  another.**
- `HEAD` sends the `GET` length but no body; `HEAD` on a stream sends
  only the head.
- A response header with a newline becomes a `500`, never two replies.
- With `max_connections=2`, a third connection gets `503` and is closed.
- `close(grace=...)` lets an in-flight request finish, cuts off one that
  runs too long, closes idle connections at once, ends open event
  streams, and runs the startup and shutdown hooks. Closing twice is
  safe, even when the first close's shutdown raised.
- The access log line has the right shape and never includes the query.

## Next Up

How can one connection carry many requests? See [Keep-Alive](keep-alive.md).
