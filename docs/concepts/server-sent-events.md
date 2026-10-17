# Server-Sent Events

## The Radio Station

When you want to know the football score you could phone a friend every
minute: "Any goals yet?" "No." "Any goals yet?" "No." That is tiring for
both of you. It is much easier to **turn on the radio**. You tune in once,
and the presenter tells you the news the moment it happens.

**Server-sent events** (SSE) turn a web page into a radio. The browser
makes one ordinary request, and the server keeps the reply open, sending
a little message every time something happens -- a job finishing, a new
chat message, a score changing. When nothing has happened for a while,
the presenter says "still here!" (a *keep-alive comment*), so nobody
thinks the station has gone off air.

| On the radio | In PyWeb |
|--------------|----------|
| One bit of news | A `ServerEvent` |
| A listener's radio, tuned in | An `EventStream` response |
| The studio that broadcasts to everyone | An `EventHub` |
| Tuning in | `hub.subscribe()` |
| Reading out the news | `hub.publish(event)` |
| "Still here!" | `: keep-alive` every 15 seconds of quiet |
| Switching the radio off | The browser closing the tab |

## Try It

A studio, one listener over a real socket, and then a sleepy listener
who doesn't keep up:

```python
from pynet.loop import run, sleep
from pynet.streams import open_connection

from pyweb.app import App
from pyweb.aserver import AsyncServer
from pyweb.sse import EventHub, EventStream, ServerEvent

app = App()
studio = EventHub()


@app.get("/radio")
def radio(request):
    return EventStream(studio.subscribe())


async def read_chunk(reader):
    size = int((await reader.read_until(b"\r\n"))[:-2], 16)
    return (await reader.read_exactly(size + 2))[:-2]


async def main():
    async with AsyncServer(app, access_log=False) as server:
        reader, writer = await open_connection("127.0.0.1", server.port)
        writer.write(b"GET /radio HTTP/1.1\r\n\r\n")
        await writer.drain()
        head = await reader.read_until(b"\r\n\r\n")
        print(head.decode().split("\r\n")[0])
        while studio.subscriber_count == 0:
            await sleep(0.01)

        studio.publish(ServerEvent("Good morning!"))
        studio.publish(ServerEvent("Sunny\nwith a light breeze", event="weather"))
        studio.publish(ServerEvent("Goal!", event="sport", id="3"))
        for _ in range(3):
            print((await read_chunk(reader)).decode(), end="")

        writer.close()  # the listener switches off
        while studio.subscriber_count > 0:
            await sleep(0.01)
        print("listeners left:", studio.subscriber_count)

    # A slow listener only keeps the newest news.
    sleepy = studio.subscribe(max_pending=2)
    for n in range(1, 6):
        studio.publish(ServerEvent(f"news {n}"))
    studio.close()
    print("sleepy hears:", [event.data async for event in sleepy])
    print("sleepy missed:", sleepy.dropped)


run(main())
```

Output:

```
HTTP/1.1 200 OK
data: Good morning!

event: weather
data: Sunny
data: with a light breeze

event: sport
id: 3
data: Goal!

listeners left: 0
sleepy hears: ['news 4', 'news 5']
sleepy missed: 3
```

Each event is a few `name: value` lines and a blank line. Multi-line
data becomes several `data:` lines, and the browser glues them back
together. The `event:` name lets a page listen for just one kind of news
(`source.addEventListener("weather", ...)`), and the `id:` is what a
reconnecting browser sends back as `Last-Event-ID` -- your handler can
read it as `request.last_event_id` and carry on from where it left off.

On the wire every event travels as one **chunk** (`Transfer-Encoding:
chunked`), because nobody knows in advance how long the broadcast will
be. The reply also says `Cache-Control: no-store` -- you would not want
yesterday's news from a cache -- and `Connection: close`: once a
connection has carried a stream, it is not used for anything else.

## The Hub

The `EventHub` is the studio. `publish` sends one event to every
listener and returns how many it reached. It **never waits**: each
listener has their own small queue (`max_pending`, 100 by default), so
one slow browser can't hold up the presenter or any other listener.
`close()` goes off air: every subscription ends once it has heard what
was already queued.

## Why Slow Listeners Get the Newest News

Imagine a friend who nodded off and missed an hour of radio. When they
wake up, do they want every update from that hour, read out in order?
No -- they want to know what is happening **now**.

So when a listener's queue is full, the hub throws away the **oldest**
event to make room for the new one, and adds one to that listener's
`dropped` count. The sleepy listener above had room for 2, so out of 5
bits of news it heard the newest two and missed 3. The server's memory
never fills up with news nobody is reading, and a page that sees
`dropped` go up knows it should reload the whole picture.

## When the Listener Leaves

The server keeps one ear on the connection while it streams. When the
browser closes the tab, the socket reaches its end, and the server:

1. stops waiting for the next event,
2. closes the event source (`aclose()`), so a generator's `finally`
   runs and a hub subscription is removed, and
3. closes the connection.

That is why `listeners left: 0` in the output: nobody has to remember
to unsubscribe. When the **server** shuts down, every open stream is
signed off properly with the last (empty) chunk, so browsers know the
broadcast ended on purpose. A `HEAD` request gets the head only, and the
source is closed straight away.

## What We Test

- Encoding: plain, empty and multi-line data; `event`, `id` and `retry`;
  a line break in `event` or `id` is refused (it could forge a field).
- The hub: fan-out to every subscriber, waking a waiting subscriber,
  drop-oldest with `dropped` counted for a slow subscriber (a fast one
  gets everything), and `close()` ending every subscription.
- Over a real socket: the SSE head and one chunk per event, a
  `: keep-alive` comment after `keepalive` quiet seconds, and
  **a client that disconnects has its source closed and its hub
  subscription removed**.
- Graceful shutdown ends an open stream with the last chunk; a failing
  source is logged and the stream ends cleanly; `Last-Event-ID` reaches
  the handler.
- `TestClient.stream(path, count=...)` collects events without a socket
  and then closes the source.

## Next Up

Need messages going **both** ways, like a phone call instead of a
radio? That is what [WebSockets](websockets-in-pyweb.md) are for.
