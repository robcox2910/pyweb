# PyWeb

An educational HTTP web server built from scratch in Python.

Every time you visit a website, your browser sends a letter to a server
and gets a reply. PyWeb is that server -- built from scratch so you can
see exactly how it works. Think of it as a post office: letters arrive,
get sorted, and replies are sent back.

## Features

- **HTTP request parsing** -- read the incoming letter (method, path, headers, body, query params)
- **Response building** -- write the reply (status codes, headers, body)
- **URL routing** -- the mailroom that sorts letters to the right handler
- **Path parameters** -- dynamic URLs like `/users/<id>`
- **Template engine** -- form letters with `{{ blanks }}` to fill in
- **Static files** -- serve CSS, JS, images straight from a folder
- **JSON support** -- parse JSON request bodies, return JSON responses
- **Logging** -- see every request and response in the terminal
- **405 handling** -- proper "Method Not Allowed" for wrong HTTP verbs

## Example

```python
from pyweb.router import Router
from pyweb.response import html_response, json_response
from pyweb.server import Server
from pyweb.template import render

router = Router()

@router.get("/")
def homepage(request):
    return html_response(render(
        "<h1>Hello, {{ name }}!</h1>",
        {"name": "World"}
    ))

@router.get("/users/<id>")
def get_user(request):
    return json_response({"user_id": request.params["id"]})

Server(router).serve_forever()
```

## Quick Start

```bash
# Install dependencies
uv sync --all-extras

# Run the example server
uv run python examples/hello_server.py
# Visit http://127.0.0.1:8000

# Run tests
uv run pytest
```

## Documentation

Every concept is explained with real-world analogies:

| Concept | Doc | Analogy |
|---------|-----|---------|
| Requests | [requests.md](docs/concepts/requests.md) | The incoming letter |
| Responses | [responses.md](docs/concepts/responses.md) | The reply letter |
| Routing | [routing.md](docs/concepts/routing.md) | The mailroom |
| Templates | [templates.md](docs/concepts/templates.md) | Form letters with blanks |
| Server | [server.md](docs/concepts/server.md) | The post office |

## Async PyWeb

PyWeb also has an async framework that runs on [PyNet](https://github.com/robcox2910/pynet)'s
own event loop: one shopkeeper serving many customers at once, with
server-sent events, WebSockets, sessions and roles.

```python
from pynet.loop import run
from pynet.streams import open_connection

from pyweb.app import App
from pyweb.aserver import AsyncServer

app = App()


@app.get("/hello/<name>")
async def hello(request, name):
    return {"greeting": f"Hello, {name}!"}


async def main():
    async with AsyncServer(app, access_log=False) as server:
        reader, writer = await open_connection("127.0.0.1", server.port)
        writer.write(b"GET /hello/Sam HTTP/1.1\r\nConnection: close\r\n\r\n")
        await writer.drain()
        print((await reader.read()).split(b"\r\n\r\n")[1].decode())
        writer.close()


run(main())
```

Output: `{"greeting": "Hello, Sam!"}`

Run the live demo (a page, a clock that ticks every second, and a
WebSocket echo) and visit http://127.0.0.1:8000, then press Ctrl-C to stop:

```bash
uv run pyweb --async
```

More programs live in `examples/`: `async_hello.py`, `sse_clock.py`,
`ws_echo_page.py` and `login_demo.py`. The step-by-step lessons start at
"Part 2: A Busy Restaurant" in [docs/index.md](docs/index.md).

### Developing alongside a sibling

PyWeb uses PyNet and PyCrypt from pinned git tags. To work on a sibling
checkout at the same time, install it editable and tell `uv` not to
re-sync over it:

```bash
uv pip install -e ../pynet
uv run --no-sync pytest
```

## Releasing

Other projects in the series ([PyStack](https://github.com/robcox2910/pystack)) use a pinned release of this one,
not whatever is on `main`. To publish a change for them:

1. Merge the change to `main`.
2. Tag it: `git tag -a vX.Y.Z -m vX.Y.Z && git push origin vX.Y.Z`.
   A change to the public API bumps the minor version (`v0.1.0` → `v0.2.0`);
   anything else bumps the patch (`v0.1.0` → `v0.1.1`).
3. In each project that uses it, edit the tag in `[tool.uv.sources]`, run
   `uv lock --upgrade-package <name>`, and open a pull request so CI checks
   the pair together.

## Related Projects

PyWeb is part of an educational series where every layer of the
computing stack is built from scratch:

| Project | What It Teaches |
|---------|----------------|
| [PyOS](https://github.com/robcox2910/py-os) | Operating systems |
| [Pebble](https://github.com/robcox2910/pebble-lang) | Compilers and programming languages |
| [PyDB](https://github.com/robcox2910/pydb) | Relational databases |
| [PyStack](https://github.com/robcox2910/pystack) | Full-stack integration |
| [PyGit](https://github.com/robcox2910/pygit) | Version control |
| [PyCrypt](https://github.com/robcox2910/pycrypt) | Cryptography |
| [PyNet](https://github.com/robcox2910/pynet) | Networking |
| [PySearch](https://github.com/robcox2910/pysearch) | Full-text search |
| [PyMQ](https://github.com/robcox2910/pymq) | Message queues |

All projects use TDD, comprehensive documentation with real-world
analogies, and are designed for learners aged 12+.

## License

MIT
