# Middleware

## Passing the Parcel

Remember the party game *pass the parcel*? The present is wrapped in
layer after layer of paper. Each person unwraps one layer on the way in.

Middleware is like those layers, wrapped around your handlers. A request
travels **in** through every layer to reach the handler, and the
response travels back **out** through the same layers in reverse order.
Each layer can:

- look at the request on the way in (who is this? are they logged in?),
- change the response on the way out (add a header, record the time),
- or **stop the parcel** and answer straight away ("no entry!"), so the
  inner layers and the handler never run.

## Writing Middleware

A middleware is an `async def` that takes the `request` and a function
called `call_next`. Calling `await call_next(request)` passes the parcel
to the next layer and gives you back its answer.

The **first** middleware you register is the **outermost** layer.

## Try It

```python
from pynet.loop import run

from pyweb.app import App
from pyweb.messages import text
from pyweb.testing import TestClient

app = App()


@app.middleware
async def stamp(request, call_next):
    print("stamp: before", request.path)
    response = await call_next(request)
    response.headers.set("X-Stamped", "yes")
    print("stamp: after", response.status)
    return response


@app.middleware
async def guard(request, call_next):
    if request.path.startswith("/secret"):
        print("guard: no entry!")
        return text("Keep out", 403)
    return await call_next(request)


@app.get("/")
def home(request):
    print("handler: home")
    return "<h1>Home</h1>"


@app.get("/secret")
def secret(request):
    return "<h1>Treasure map</h1>"


async def main():
    client = TestClient(app)
    home_page = await client.get("/")
    print(home_page.status, home_page.headers.get("X-Stamped"))
    secret_page = await client.get("/secret")
    print(secret_page.status, secret_page.text)


run(main())
```

Output:

```
stamp: before /
handler: home
stamp: after 200
200 yes
stamp: before /secret
guard: no entry!
stamp: after 403
403 Keep out
```

Look at the order: `stamp` is outermost, so it sees every request first
and every response last -- even the `403` that `guard` sent without
asking the handler.

## Errors Become Responses

If no route matches, middleware still sees the answer: a `404` (or a
`405`) *response*, not an exception, so a logging middleware can count
it. If a middleware itself raises `HTTPError`, that becomes the
response; if it crashes, the visitor gets a generic `500` and the
traceback goes to the `pyweb.app` log.

A middleware that forgets to `return` hands back `None` -- like a waiter
who walks back from the kitchen with empty hands. PyWeb notices, logs
which kind of value came back, and answers a generic `500`, so the
connection keeps working. (A `dict`, `list` or `str` is fine: it becomes
JSON or HTML, just like a handler's.)

## What We Test

- Two middlewares run in registration order on the way in and in
  reverse on the way out.
- A middleware that returns without calling `call_next` stops the
  handler from running.
- Middleware sees `404` as a response.
- `HTTPError` from middleware becomes its response; a crash becomes a
  logged `500`.
- A middleware that forgets `return` (or answers `42`) gives a logged
  `500`, over a real socket and in the `TestClient`, and the next request
  on the same connection still works.
- Middleware can add headers to the outgoing response.

## Next Up

How do you check all this works without starting a real server? See
[Testing Apps](testing-apps.md).
