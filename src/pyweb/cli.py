"""PyWeb command-line interface.

Start a web server that serves your routes.
"""

import sys

from pyweb.response import html_response
from pyweb.router import Router
from pyweb.server import DEFAULT_HOST, DEFAULT_PORT, Server


def main() -> None:
    """Entry point for the ``pyweb`` command. Start a demo server."""
    router = Router()

    @router.get("/")
    def index(request):  # noqa: ARG001, ANN001, ANN202
        """Serve the homepage."""
        return html_response("<h1>Welcome to PyWeb!</h1><p>Your web server is running.</p>")

    server = Server(router, host=DEFAULT_HOST, port=DEFAULT_PORT)
    sys.stdout.write(f"PyWeb serving on http://{DEFAULT_HOST}:{DEFAULT_PORT}\n")
    sys.stdout.write("Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stdout.write("\nServer stopped.\n")
