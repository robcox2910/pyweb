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

## Concepts

Every concept is explained with real-world analogies:

| Concept | Doc | Analogy |
|---------|-----|---------|
| Requests | [requests.md](docs/concepts/requests.md) | The incoming letter |
| Responses | [responses.md](docs/concepts/responses.md) | The reply letter |
| Routing | [routing.md](docs/concepts/routing.md) | The mailroom |
| Templates | [templates.md](docs/concepts/templates.md) | Form letters with blanks |
| Server | [server.md](docs/concepts/server.md) | The post office |

<!-- docs:start -->
## Documentation

Full docs (with kid-friendly explanations of every idea): https://robcox2910.github.io/pyweb/

## Install

[![PyPI](https://img.shields.io/pypi/v/fromscratch-pyweb)](https://pypi.org/project/fromscratch-pyweb/)

```bash
pip install fromscratch-pyweb
```

Or get the whole series at once: `pip install fromscratch-pystack`.
<!-- docs:end -->

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

PyWeb uses PyNet and PyCrypt from PyPI (`fromscratch-pynet` and
`fromscratch-pycrypt`). To work on a sibling
checkout at the same time, install it editable and tell `uv` not to
re-sync over it:

```bash
uv pip install -e ../pynet
uv run --no-sync pytest
```

## Releasing

Other projects in the series ([PyStack](https://github.com/robcox2910/pystack)) use a released version of this one from PyPI,
not whatever is on `main`. To publish a change for them:

1. Merge the change to `main`.
2. Release it: `gh release create vX.Y.Z --target main --generate-notes`
   (or push a `vX.Y.Z` tag); the release workflow publishes it to PyPI.
   A change to the public API bumps the minor version (`v0.1.0` → `v0.2.0`);
   anything else bumps the patch (`v0.1.0` → `v0.1.1`).
3. In each project that uses it, raise the `fromscratch-pyweb>=X.Y.Z` floor in
   `pyproject.toml`, run `uv lock --upgrade-package fromscratch-pyweb`, and open
   a pull request so CI checks the pair together.

<!-- series:start -->
## Related Projects

This project is one of a series that builds real systems "from scratch in Python" to teach how they work. See the [series home](https://robcox2910.github.io/) for the full story.

| Project | What it teaches | Repository | Docs | PyPI |
| --- | --- | --- | --- | --- |
| PyOS | Operating systems | [py-os](https://github.com/robcox2910/py-os) | [Docs](https://robcox2910.github.io/py-os/) | [fromscratch-pyos](https://pypi.org/project/fromscratch-pyos/) |
| Pebble | Compilers and programming languages | [pebble-lang](https://github.com/robcox2910/pebble-lang) | [Docs](https://robcox2910.github.io/pebble-lang/) | [fromscratch-pebble](https://pypi.org/project/fromscratch-pebble/) |
| PyDB | Relational databases | [pydb](https://github.com/robcox2910/pydb) | [Docs](https://robcox2910.github.io/pydb/) | [fromscratch-pydb](https://pypi.org/project/fromscratch-pydb/) |
| PyKV | Key-value stores | [pykv](https://github.com/robcox2910/pykv) | [Docs](https://robcox2910.github.io/pykv/) | [fromscratch-pykv](https://pypi.org/project/fromscratch-pykv/) |
| PyDocDB | Document databases | [pydoc-db](https://github.com/robcox2910/pydoc-db) | [Docs](https://robcox2910.github.io/pydoc-db/) | [fromscratch-pydocdb](https://pypi.org/project/fromscratch-pydocdb/) |
| PyGraphDB | Graph databases | [pygraphdb](https://github.com/robcox2910/pygraphdb) | [Docs](https://robcox2910.github.io/pygraphdb/) | [fromscratch-pygraphdb](https://pypi.org/project/fromscratch-pygraphdb/) |
| PyTSDB | Time-series databases | [pytsdb](https://github.com/robcox2910/pytsdb) | [Docs](https://robcox2910.github.io/pytsdb/) | [fromscratch-pytsdb](https://pypi.org/project/fromscratch-pytsdb/) |
| PyVecDB | Vector databases | [pyvecdb](https://github.com/robcox2910/pyvecdb) | [Docs](https://robcox2910.github.io/pyvecdb/) | [fromscratch-pyvecdb](https://pypi.org/project/fromscratch-pyvecdb/) |
| PyColDB | Column-family databases | [pycoldb](https://github.com/robcox2910/pycoldb) | [Docs](https://robcox2910.github.io/pycoldb/) | [fromscratch-pycoldb](https://pypi.org/project/fromscratch-pycoldb/) |
| PyCrypt | Cryptography | [pycrypt](https://github.com/robcox2910/pycrypt) | [Docs](https://robcox2910.github.io/pycrypt/) | [fromscratch-pycrypt](https://pypi.org/project/fromscratch-pycrypt/) |
| PyNet | Computer networking | [pynet](https://github.com/robcox2910/pynet) | [Docs](https://robcox2910.github.io/pynet/) | [fromscratch-pynet](https://pypi.org/project/fromscratch-pynet/) |
| **PyWeb** | **HTTP web servers** | [pyweb](https://github.com/robcox2910/pyweb) | [Docs](https://robcox2910.github.io/pyweb/) | [fromscratch-pyweb](https://pypi.org/project/fromscratch-pyweb/) |
| PyGit | Version control | [pygit](https://github.com/robcox2910/pygit) | [Docs](https://robcox2910.github.io/pygit/) | [fromscratch-pygit](https://pypi.org/project/fromscratch-pygit/) |
| PySearch | Search engines | [pysearch](https://github.com/robcox2910/pysearch) | [Docs](https://robcox2910.github.io/pysearch/) | [fromscratch-pysearch](https://pypi.org/project/fromscratch-pysearch/) |
| PyMQ | Message queues | [pymq](https://github.com/robcox2910/pymq) | [Docs](https://robcox2910.github.io/pymq/) | [fromscratch-pymq](https://pypi.org/project/fromscratch-pymq/) |
| PyLLM | Language models (the brain) | [pyllm](https://github.com/robcox2910/pyllm) | [Docs](https://robcox2910.github.io/pyllm/) | [fromscratch-pyllm](https://pypi.org/project/fromscratch-pyllm/) |
| PyScheduler | Job scheduling | [pyscheduler](https://github.com/robcox2910/pyscheduler) | [Docs](https://robcox2910.github.io/pyscheduler/) | [fromscratch-pyscheduler](https://pypi.org/project/fromscratch-pyscheduler/) |
| PyStack | Full-stack integration | [pystack](https://github.com/robcox2910/pystack) | [Docs](https://robcox2910.github.io/pystack/) | [fromscratch-pystack](https://pypi.org/project/fromscratch-pystack/) |
<!-- series:end -->

## License

MIT
