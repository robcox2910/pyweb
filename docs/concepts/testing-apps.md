# Testing Apps

## The Flight Simulator

Pilots practise in a **flight simulator** before they fly a real plane.
Every dial and button behaves just like the real cockpit, but nobody
leaves the ground.

`TestClient` is a flight simulator for your App. It builds exactly the
same `HttpRequest` the real server would build -- the path is split from
the query and percent-decoded the same way -- and hands it straight to
`app.handle`. No sockets, no ports, no waiting.

It also behaves like a browser in one important way: it keeps a
**cookie jar**. When the App sends `Set-Cookie`, the client remembers
the cookie and sends it back on later requests. When the App deletes a
cookie (`Max-Age=0`), the client forgets it.

## Try It

```python
from pynet.loop import run

from pyweb.app import App
from pyweb.messages import text
from pyweb.testing import TestClient

app = App()


@app.post("/login")
def login(request):
    name = request.form().get("name")
    response = text(f"Welcome, {name}")
    response.set_cookie("player", name)
    return response


@app.get("/me")
def me(request):
    return {"player": request.cookies.get("player")}


@app.get("/scores/<int:level>")
def scores(request, level):
    return {"level": level, "top": request.query.get("top")}


async def main():
    client = TestClient(app)
    print((await client.get("/me")).json())
    print((await client.post("/login", form={"name": "Ada"})).text)
    print((await client.get("/me")).json())
    print(client.cookies)
    print((await client.get("/scores/3", query={"top": "5"})).json())
    print((await client.delete("/me")).status)


run(main())
```

Output:

```
{'player': None}
Welcome, Ada
{'player': 'Ada'}
{'player': 'Ada'}
{'level': 3, 'top': '5'}
405
```

## What You Can Send

Every method -- `get`, `post`, `put`, `patch`, `delete`, `head` --
takes a path and these optional keyword arguments:

| Argument | What it does |
|----------|--------------|
| `query={"q": "cats"}` | adds `?q=cats` to the path |
| `json={"score": 3}` | sends a JSON body (with `Content-Type: application/json`) |
| `form={"name": "Ada"}` | sends a form body, like an HTML `<form>` |
| `body=b"raw bytes"` | sends exactly these bytes |
| `headers={"Accept": "text/html"}` | adds request headers |

Use only one of `json`, `form` and `body` at a time. The request comes
from `client=("testclient", 0)` and carries `Host: testserver`.

The answer is a `TestResponse` with `status`, `headers`, `body`, a
`text` property and a `json()` method.

Remember that the client's methods are `async`: run your test inside
`pynet.loop.run(...)`.

## What We Test

- Query pairs, headers and the client address arrive as the server
  would deliver them; the path is percent-decoded.
- JSON, form and raw bodies are encoded with the right `Content-Type`
  and `Content-Length`; giving two bodies is refused.
- `HEAD` returns an empty body.
- The cookie jar stores cookies from `Set-Cookie`, sends them back, and
  forgets cookies deleted with `Max-Age=0`.
