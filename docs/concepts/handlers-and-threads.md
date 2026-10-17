# Handlers and Threads

## One Waiter, Many Tables

Picture a busy café with just **one waiter**. The waiter takes an order
at table 1, hands it to the kitchen, and -- instead of standing there
waiting -- goes to table 2, then table 3. When the kitchen rings the
bell, the waiter carries the food out. One waiter can serve a whole room
because they never stand still waiting.

PyWeb's new `App` runs on PyNet's **event loop**, and the loop is that
waiter. Every request is a table. A **handler** is the recipe for one
dish (one page).

There are three kinds of handler, and they treat the waiter differently:

| Kind | How you write it | What the waiter does |
|------|------------------|----------------------|
| **async** | `async def` | Starts it, and while it `await`s something, serves other tables. |
| **plain** | `def` | Cooks it *personally*, right now, start to finish. |
| **blocking** | `def` with `blocking=True` | Hands it to a helper in the back kitchen (a thread) and keeps serving. |

## Warning: a plain `def` handler blocks everyone

> **While a plain `def` handler runs, nobody else is served. At all.**
>
> The waiter is busy cooking your dish, so every other table waits --
> every other request, on every connection. A plain `def` handler that
> takes one second makes *the whole server* freeze for one second.

Plain `def` handlers are fine for quick jobs (building a small page,
adding up a few numbers). If a handler does something slow that you
cannot `await` -- `time.sleep`, reading a huge file, a slow database
library -- mark it `blocking=True`, so it runs on a helper thread
using `pynet.loop.run_in_thread`.

There are only **four helpers** in the back kitchen (PyNet's thread
pool), and they are shared: the same four also read files for
`mount_static`. A fifth slow job has to queue until a helper is free. So
if many visitors hit slow `blocking=True` handlers at once, *everyone*
who needs a helper waits -- even someone just asking for a picture.
Keep blocking jobs as short as you can, and prefer `async def` with
`await` where a library lets you.

`blocking=True` only works on a plain `def`. Putting it on an
`async def` is an error (`ValueError`), because an async handler should
`await` instead of blocking.

## Try It

```python
import time

from pynet.loop import run, spawn

from pyweb.app import App
from pyweb.testing import TestClient

app = App()


@app.get("/fast")
async def fast(request):
    return {"speed": "fast"}


@app.get("/hello/<name>")
def hello(request, name):
    return f"<h1>Hello, {name}!</h1>"


@app.get("/report", blocking=True)
def report(request):
    time.sleep(0.2)  # pretend this is a slow database query
    return {"report": "ready"}


async def main():
    client = TestClient(app)
    print((await client.get("/hello/Zoe")).text)
    slow = spawn(client.get("/report"))
    print((await client.get("/fast")).json())
    print((await slow).json())


run(main())
```

Output:

```
<h1>Hello, Zoe!</h1>
{'speed': 'fast'}
{'report': 'ready'}
```

The `/report` handler sleeps on a helper thread, so `/fast` is answered
while it is still working.

## Path Parameters Become Arguments

A pattern like `/hello/<name>` or `/users/<int:id>` hands the captured
values to your handler as **keyword arguments**, already converted:
`<int:id>` gives you a real `int`. They are also kept in
`request.path_params`.

## What Handlers May Return

| You return | You get |
|------------|---------|
| an `HttpResponse` (from `text`, `html`, `json`, `redirect`, ...) | sent as it is |
| a streaming response (such as server-sent events) | sent as it is |
| a `dict` or `list` | a JSON response |
| a `str` | an HTML page |
| anything else | a `500 Internal Server Error` (and a log message) |

`NaN` and infinity are not real JSON, so a `dict` holding
`float("nan")` is a logged `500` too, and a request body containing
`NaN` or `Infinity` makes `request.json()` answer `400`. So does a body that nests lists or
objects more than `MAX_JSON_NESTING` (100) levels deep, which no honest
client needs; the check is the same on every platform.

To answer with an error on purpose, `raise HTTPError(409, "already
taken")`. If your handler crashes with any other exception, the
visitor gets a plain `500` with **no** traceback (secrets stay secret),
and the full traceback is logged on the `pyweb.app` logger for you.

A `HEAD` request is answered by the `GET` handler; the App keeps the
headers and throws the body away.

## What We Test

- `async def` handlers are awaited; plain `def` handlers run on the
  loop's own thread.
- A `blocking=True` handler runs on a different thread, and another
  request is answered while it is still running.
- `blocking=True` on an `async def` is refused.
- `<int:...>` parameters arrive as `int` keyword arguments.
- `dict`/`list` become JSON, `str` becomes HTML, anything else is a
  logged `500`; so is a `dict` holding `NaN`, and `NaN` or `Infinity` in
  a request body is a `400`.
- `HTTPError(409)` answers `409`; a crash answers a generic `500` with
  no traceback in the body, and is logged.
- A wrong method answers `405` with an `Allow` header such as
  `GET, HEAD`; `HEAD` gets `GET`'s headers with an empty body.
- Startup hooks run in order, shutdown hooks in reverse.

## Next Up

Want to do something for *every* request? See [Middleware](middleware.md).

## See also

- [PyNet: the event loop](https://robcox2910.github.io/pynet/concepts/event-loop/) -- the loop PyWeb runs on, built from scratch.
