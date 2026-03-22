# PyWeb

An educational HTTP web server built from scratch in Python.

PyWeb is a fully functional web server that handles HTTP requests,
routes URLs to handlers, renders templates, and serves responses --
all written from the ground up as a learning project.

## Example

```python
from pyweb.router import Router
from pyweb.response import html_response
from pyweb.server import Server
from pyweb.template import render

router = Router()

@router.get("/")
def index(request):
    return html_response(render(
        "<h1>Hello, {{ name }}!</h1>",
        {"name": "World"}
    ))

@router.get("/about")
def about(request):
    return html_response("<h1>About PyWeb</h1>")

server = Server(router)
server.serve_forever()
```

## Quick Start

```bash
uv sync --all-extras
uv run pyweb            # Start the demo server
uv run pytest           # Run tests
```

## Features

- **HTTP request parsing** -- method, path, headers, body, query params
- **Response building** -- status codes, headers, body, serialization
- **URL routing** -- match paths to handler functions with decorators
- **Template engine** -- `{{ placeholder }}` replacement in HTML
- **TCP server** -- listen, accept, serve (with graceful shutdown)

## License

MIT
