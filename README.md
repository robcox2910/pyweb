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

All projects use TDD, comprehensive documentation with real-world
analogies, and are designed for learners aged 12+.

## License

MIT
