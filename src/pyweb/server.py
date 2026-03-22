"""HTTP server -- the post office that listens for letters.

The server listens on a port, accepts connections, reads HTTP requests,
dispatches them to the router, and sends back responses. This is the
piece that makes the web server actually *serve*.
"""

import socket

from pyweb.request import parse_request
from pyweb.response import Response, StatusCode, html_response
from pyweb.router import Router

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
BUFFER_SIZE = 4096
BACKLOG = 5


class Server:
    """A simple HTTP server.

    Creates a TCP socket, listens for connections, and dispatches
    requests through a Router.

    Args:
        router: The router to dispatch requests to.
        host: The hostname to bind to.
        port: The port to listen on.

    """

    __slots__ = ("_host", "_port", "_router", "_running")

    def __init__(
        self,
        router: Router,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        """Create a server with the given router."""
        self._router = router
        self._host = host
        self._port = port
        self._running = False

    @property
    def host(self) -> str:
        """Return the server's host address."""
        return self._host

    @property
    def port(self) -> int:
        """Return the server's port number."""
        return self._port

    def handle_request(self, raw: str) -> bytes:
        """Process a raw HTTP request and return the response bytes.

        This is the core logic, separated from networking for testability.

        Args:
            raw: The raw HTTP request string.

        Returns:
            The HTTP response as bytes.

        """
        try:
            request = parse_request(raw)
            response = self._router.dispatch(request)
        except Exception as exc:  # noqa: BLE001
            response = html_response(
                f"<h1>500 Internal Server Error</h1><p>{exc}</p>",
                status=StatusCode.INTERNAL_ERROR,
            )
        return response.to_bytes()

    def serve_forever(self) -> None:
        """Start accepting connections and serving requests.

        Blocks until ``stop()`` is called or the process is interrupted.

        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self._host, self._port))
            sock.listen(BACKLOG)
            sock.settimeout(1.0)  # Allow periodic stop-check.
            self._running = True

            while self._running:
                try:
                    conn, _addr = sock.accept()
                except TimeoutError:
                    continue

                with conn:
                    raw = conn.recv(BUFFER_SIZE).decode("utf-8", errors="replace")
                    if raw:
                        response_bytes = self.handle_request(raw)
                        conn.sendall(response_bytes)

    def stop(self) -> None:
        """Signal the server to stop accepting connections."""
        self._running = False
