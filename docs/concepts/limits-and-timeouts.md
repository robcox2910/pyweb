# Limits and Timeouts

## The Post Office Rules

A post office has rules on the wall: parcels no heavier than 20 kg,
labels must fit on the box, and if you get to the front of the queue and
just stand there, the clerk calls the next person. Without rules, one
awkward customer could jam up the whole post office.

A web server is open to *anyone* on the network, including people who
are careless or naughty. So PyWeb has the same kind of rules, kept in a
`Limits` object:

| Rule | Default | What happens if you break it |
|------|---------|------------------------------|
| `max_head_bytes` | 16 KiB | `431` -- your label is too long |
| `max_headers` | 100 | `431` -- too many stickers |
| `max_body_bytes` | 1 MiB | `413` -- parcel too heavy (refused *before* reading it) |
| `header_timeout` | 10 s | `408` if you start a request and stall |
| `body_timeout` | 30 s | `408` if the body stops arriving |
| `keepalive_timeout` | 5 s | an idle connection is closed quietly |
| `write_timeout` | 30 s | a client that stops reading is cut off |
| `max_connections` | 1 000 | `503` -- "we're full, come back later" |

After any of these the server closes the connection.

## Try It

Here the rules are made tiny so we can break them on purpose:

```python
from pynet.loop import run
from pynet.streams import open_connection

from pyweb.aserver import AsyncServer
from pyweb.app import App
from pyweb.http1 import Limits

app = App()


@app.post("/letters")
def post_letter(request):
    return {"got": len(request.body)}


async def send(server, raw):
    reader, writer = await open_connection("127.0.0.1", server.port)
    writer.write(raw)
    await writer.drain()
    reply = await reader.read()  # every one of these closes afterwards
    writer.close()
    head, _, body = reply.partition(b"\r\n\r\n")
    print(head.split(b"\r\n")[0].decode(), body.decode())


async def main():
    limits = Limits(max_body_bytes=10, max_head_bytes=200, header_timeout=0.2)
    async with AsyncServer(app, limits=limits, access_log=False) as server:
        print("1. a small letter")
        await send(server, b"POST /letters HTTP/1.1\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
        print("2. a parcel that is too big (we never even send it)")
        await send(server, b"POST /letters HTTP/1.1\r\nContent-Length: 5000\r\n\r\n")
        print("3. a label that is far too long")
        await send(server, b"POST /letters HTTP/1.1\r\nX-Note: " + b"blah" * 100 + b"\r\n\r\n")
        print("4. someone who starts talking, then stops")
        await send(server, b"POST /letters HTTP/1.1\r\n")


run(main())
```

Output:

```
1. a small letter
HTTP/1.1 200 OK {"got": 5}
2. a parcel that is too big (we never even send it)
HTTP/1.1 413 Content Too Large {"error": "Content Too Large", "message": "the request body is too large"}
3. a label that is far too long
HTTP/1.1 431 Request Header Fields Too Large {"error": "Request Header Fields Too Large", "message": "the request head is too large"}
4. someone who starts talking, then stops
HTTP/1.1 408 Request Timeout {"error": "Request Timeout", "message": "the request head took too long"}
```

Error replies are JSON, or a small HTML page if the request said
`Accept: text/html` -- just like `HTTPError` from a handler.

## Quiet or `408`?

There are two kinds of silence:

- A connection that has sent **nothing at all** (a brand-new one, or a
  kept-alive one between requests) is closed *quietly*. Browsers often
  open a spare connection "just in case", and shouldn't get a telling-off.
- A connection that **started** a request and then stalled gets `408
  Request Timeout`, then is closed.

## Tricky Requests

Some requests are refused because two programs might read them
differently -- the trick behind "request smuggling":

- Two different `Content-Length` values, or `Content-Length` together
  with `Transfer-Encoding` -> `400`.
- `Transfer-Encoding` on its own -> `411` (PyWeb only accepts bodies with
  a `Content-Length`).
- A lone carriage return, line feed or NUL byte inside a line -> `400`.
- An unknown method such as `BREW` -> `501`; `HTTP/2.0` -> `505`.

## What We Test

- `413` for a too-big `Content-Length`, sent without the body.
- `431` for a head longer than `max_head_bytes`, while a head of exactly
  `max_head_bytes` is fine.
- `400` for conflicting framing and bare CR/LF/NUL, `411` for
  `Transfer-Encoding`, `501` for unknown methods, `505` for unknown
  versions -- and the connection closes after each.
- A stalled head gets `408` within half a second when
  `header_timeout=0.2`; a stalled body gets `408` too.
- A silent new connection and an idle keep-alive connection close
  without any reply.
- A client that never reads a huge reply is cut off by `write_timeout`.
- With `max_connections=2`, a third connection gets `503`.
- A client hanging up mid-request is forgotten quietly.
