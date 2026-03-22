"""A simple PyWeb server -- your first web server from scratch!

Run this with: uv run python examples/hello_server.py
Then visit http://127.0.0.1:8000 in your browser.
"""

from pyweb.request import Request
from pyweb.response import Response, html_response, json_response, text_response
from pyweb.router import Router
from pyweb.server import Server
from pyweb.template import render

router = Router()


@router.get("/")
def homepage(_request: Request) -> Response:
    """Serve the homepage with a template."""
    return html_response(render(
        "<html><body>"
        "<h1>Welcome to {{ title }}!</h1>"
        "<p>This server was built from scratch in Python.</p>"
        "<ul>"
        "<li><a href='/about'>About</a></li>"
        "<li><a href='/greet?name=Alice'>Greet Alice</a></li>"
        "<li><a href='/api/info'>API Info (JSON)</a></li>"
        "</ul>"
        "</body></html>",
        {"title": "PyWeb"},
    ))


@router.get("/about")
def about(_request: Request) -> Response:
    """Serve the about page."""
    return html_response("<html><body><h1>About PyWeb</h1>"
                         "<p>An educational web server built from scratch.</p>"
                         "<p><a href='/'>Back home</a></p></body></html>")


@router.get("/greet")
def greet(request: Request) -> Response:
    """Greet someone by name using query parameters."""
    name = request.query_params.get("name", "World")
    return html_response(render(
        "<html><body><h1>Hello, {{ name }}!</h1>"
        "<p><a href='/'>Back home</a></p></body></html>",
        {"name": name},
    ))


@router.get("/api/info")
def api_info(_request: Request) -> Response:
    """Return JSON data."""
    return json_response({
        "server": "PyWeb",
        "version": "0.1.0",
        "routes": ["/", "/about", "/greet", "/api/info"],
    })


if __name__ == "__main__":
    server = Server(router)
    print(f"PyWeb serving on http://{server.host}:{server.port}")  # noqa: T201
    print("Press Ctrl+C to stop.")  # noqa: T201
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")  # noqa: T201
