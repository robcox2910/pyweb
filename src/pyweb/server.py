"""HTTP server -- the post office that listens for letters.

The server listens on a port, accepts connections, reads HTTP requests,
dispatches them to the router, and sends back responses. This is the
piece that makes the web server actually *serve*.

Think of it like a post office: it opens its doors (binds to a port),
waits for people to walk in (accepts connections), reads their letters
(parses requests), sorts them (routes), and hands back replies
(sends responses).
"""

import socket
import sys

from pyweb.errors import ParseError
from pyweb.request import parse_request
from pyweb.response import Response, StatusCode, html_response
from pyweb.router import Router

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
BUFFER_SIZE = 4096
BACKLOG = 5


def _content_length(header_text: str) -> int:
    """Read the Content-Length header value, or 0 if it's absent.

    Content-Length is the sender writing "this letter is N pages long"
    on the envelope so we know when we've read the whole thing.
    """
    for line in header_text.replace("\r\n", "\n").split("\n"):
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() == "content-length":
            try:
                return int(value.strip())
            except ValueError:
                return 0
    return 0


def _read_request(conn: socket.socket) -> str:
    """Read one whole HTTP request from a socket, body and all.

    A big POST body can arrive in several chunks -- like a long letter
    split across many envelopes. A naive ``recv`` grabs only the first
    envelope and truncates the rest, so we keep reading: first until the
    blank line that ends the headers, then until we've collected the
    number of body bytes the Content-Length header promised.

    Args:
        conn: The connected client socket to read from.

    Returns:
        The full raw HTTP request as a string.

    """
    data = bytearray()

    # 1. Read until the blank line that separates headers from body.
    while b"\r\n\r\n" not in data and b"\n\n" not in data:
        chunk = conn.recv(BUFFER_SIZE)
        if not chunk:
            return data.decode("utf-8", errors="replace")
        data.extend(chunk)

    # 2. Find where the body starts.
    header_end = data.find(b"\r\n\r\n")
    separator_len = 4
    if header_end == -1:
        header_end = data.find(b"\n\n")
        separator_len = 2
    body_start = header_end + separator_len

    # 3. Keep reading until the whole promised body has arrived.
    header_text = data[:header_end].decode("utf-8", errors="replace")
    content_length = _content_length(header_text)
    while len(data) - body_start < content_length:
        chunk = conn.recv(BUFFER_SIZE)
        if not chunk:
            break
        data.extend(chunk)

    return data.decode("utf-8", errors="replace")


class Server:
    """A simple HTTP server.

    Create a TCP socket, listen for connections, and dispatch
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
        except ParseError:
            # The client sent us garbage -- that's their mistake, not ours.
            response = html_response(
                "<h1>400 Bad Request</h1>",
                status=StatusCode.BAD_REQUEST,
            )
        else:
            try:
                response = self._router.dispatch(request)
            except Exception:  # noqa: BLE001
                # Our own mistake -- never leak internal details to the client.
                response = html_response(
                    "<h1>500 Internal Server Error</h1>",
                    status=StatusCode.INTERNAL_ERROR,
                )
        self._log_request(raw, response)
        return response.to_bytes()

    def _log_request(self, raw: str, response: Response) -> None:
        """Log a request and its response status."""
        # Extract the request line for logging.
        first_line = raw.split("\n", maxsplit=1)[0].strip() if raw else "?"
        sys.stderr.write(f"{first_line} -> {response.status}\n")

    def serve_forever(self) -> None:
        """Start accepting connections and serving requests.

        Block until ``stop()`` is called or the process is interrupted.

        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self._host, self._port))
            sock.listen(BACKLOG)
            sock.settimeout(1.0)
            self._running = True

            while self._running:
                try:
                    conn, _addr = sock.accept()
                except TimeoutError:
                    continue

                with conn:
                    raw = _read_request(conn)
                    if raw:
                        response_bytes = self.handle_request(raw)
                        conn.sendall(response_bytes)

    def stop(self) -> None:
        """Signal the server to stop accepting connections."""
        self._running = False
