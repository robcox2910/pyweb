# WebSockets in PyWeb

## A Phone Call Instead of Letters

Ordinary HTTP is like **sending letters**. You write a question, post it,
and wait for the answer to come back. If you want to say something else,
you write another letter. The other person can never write to you first
-- they can only answer.

A **WebSocket** is like a **phone call**. You dial once, the other person
picks up, and from then on either of you can talk whenever you like,
until somebody says "goodbye" and hangs up. That is perfect for chat
rooms, games and anything where the server has news for *you* while you
are also sending news to *it*.

A WebSocket call even starts as a letter: the browser sends a normal
`GET` request that says "please switch this line to a phone call"
(`Upgrade: websocket`). If the server agrees it answers
`101 Switching Protocols`, and the same connection becomes the phone line.

| On the phone | In PyWeb |
|--------------|----------|
| Dialling a number | A `GET` with `Upgrade: websocket` |
| The receptionist who checks you may be put through | Your middleware |
| Picking up | The handshake (`101 Switching Protocols`) |
| The person you talk to | Your `@app.websocket(...)` handler |
| Saying something | `await ws.send_text(...)` / `send_bytes(...)` |
| Listening | `await ws.recv()` |
| "Goodbye!" | A close code: 1000 normal, 1011 crashed, 1001 going away |
| Posting a letter to a phone number | A plain `GET`: `426 Upgrade Required` |

## Try It

A phone line with a receptionist (middleware) that only puts callers
with a gold ticket through:

```python
from pynet.loop import run, spawn
from pynet.streams import open_connection
from pynet.ws import WebSocketClosed, connect_websocket

from pyweb.app import App
from pyweb.aserver import AsyncServer
from pyweb.messages import text

app = App()


@app.middleware
async def members_only(request, call_next):
    if request.query.get("ticket") != "gold":
        return text("no ticket, no call", 401)
    return await call_next(request)


@app.websocket("/call/<name>")
async def call(ws, request, name):
    await ws.send_text(f"Hello {name}, you're through!")
    while True:
        message = await ws.recv()
        if message == "bye":
            return  # hanging up closes with 1000
        await ws.send_text(f"You said: {message}")


async def main():
    async with AsyncServer(app, access_log=False) as server:
        # A caller with a ticket gets put through.
        ws = await connect_websocket("127.0.0.1", server.port, "/call/Sam?ticket=gold")
        print(await ws.recv())
        await ws.send_text("Is it sunny?")
        print(await ws.recv())
        await ws.send_text("bye")
        try:
            await ws.recv()
        except WebSocketClosed as closed:
            print("hung up with code", closed.code)

        # A caller without a ticket is stopped by the middleware.
        reader, writer = await open_connection("127.0.0.1", server.port)
        writer.write(
            b"GET /call/Sam HTTP/1.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        await writer.drain()
        print((await reader.read_until(b"\r\n")).decode().strip())
        writer.close()

        # A plain letter (GET) to a phone line is told to dial instead.
        reader, writer = await open_connection("127.0.0.1", server.port)
        writer.write(b"GET /call/Sam?ticket=gold HTTP/1.1\r\n\r\n")
        await writer.drain()
        print((await reader.read_until(b"\r\n")).decode().strip())
        writer.close()

        # The office closes while someone is still on the line.
        ws = await connect_websocket("127.0.0.1", server.port, "/call/Kim?ticket=gold")
        print(await ws.recv())
        closing = spawn(server.close())
        try:
            await ws.recv()
        except WebSocketClosed as closed:
            print("server closing: code", closed.code)
        await closing


run(main())
```

Output:

```
Hello Sam, you're through!
You said: Is it sunny?
hung up with code 1000
HTTP/1.1 401 Unauthorized
HTTP/1.1 426 Upgrade Required
Hello Kim, you're through!
server closing: code 1001
```

A WebSocket handler gets the WebSocket, the upgrade request and the path
parameters: `async def call(ws, request, name)`. It talks for as long as
it likes. When it **returns**, PyWeb says goodbye with code `1000`
("all done"). If it **crashes**, the error is logged on the
`pyweb.server` logger and the caller hears `1011` ("something went
wrong at our end") -- never a silent drop.

## The Receptionist Comes First

The upgrade request goes through **every middleware**, exactly like a
normal request. Only at the very end, where a normal request would reach
its handler, the App answers with a `WebSocketUpgrade` ("put them
through"). The server sees that answer, does the handshake, and starts
your handler.

So if any middleware answers instead -- like `members_only` above with
its `401` -- that answer is sent as an ordinary HTTP response, the
connection is closed, and the handler never runs. This is how login
checks protect WebSockets too: the same middleware guards the letters
and the phone calls.

One thing is different for phone calls: a WebSocket handshake cannot
carry a [CSRF token](csrf.md), but the browser still adds your cookie,
even when a page from *another* site dials. So the phone line checks
which house the call comes from. When the caller is logged in by
cookie, `SessionAuth` only puts them through if the handshake's `Origin`
header matches the site's own `Host` (or an origin you listed in
`allowed_origins`); otherwise the answer is `403`. Your own
`members_only`-style middleware does not do this for you -- if it
trusts cookies, check `request.headers.get("Origin")` too. See
[CSRF Protection](csrf.md#websockets-which-house-is-calling).

Some other rules on the line:

- A handshake that is broken (for example no `Sec-WebSocket-Key`) gets
  `400 Bad Request` and the line is put down.
- A message may be as large as a request body (`max_body_bytes`, 1 MiB
  by default). A bigger one ends the call with `1009` ("too big").
- A plain `GET` to a WebSocket-only path is answered
  `426 Upgrade Required`, with an `Upgrade: websocket` header to say
  how to ask properly.

## Closing Time

When the server shuts down gracefully, every open call is told
**`1001` -- going away** ("sorry, the office is closing"), so the browser
knows it was not a crash and can try again later. A handler waiting in
`recv()` sees `WebSocketClosed` and finishes normally. One that ignores
the goodbye and keeps going is cancelled when the `grace` period ends.

## What We Test

- An echo route with a path parameter: text and bytes come back over a
  real socket; a handler that returns closes with `1000`.
- Middleware runs **before** the upgrade: a middleware's `401` is sent
  and the connection closes; when it lets the call through it runs
  once, with the method `GET`.
- A handler that raises: the error is logged and the client hears
  `1011`.
- Server shutdown: a live call hears `1001`, also when shutdown began
  while the call was still being put through; a stubborn handler is
  cancelled after `grace`.
- A plain `GET` gets `426` with `Upgrade: websocket`; a broken handshake
  gets `400`; the access log shows the call as `101`.

## See also

- [PyNet: real WebSockets](https://robcox2910.github.io/pynet/concepts/websocket-real/) -- the phone-line machinery that PyWeb borrows for its live calls.
