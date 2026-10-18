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
import threading

from pyweb.errors import ParseError
from pyweb.request import parse_request
from pyweb.response import STATUS_PHRASES, Response, StatusCode, html_response
from pyweb.router import Router

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
BUFFER_SIZE = 4096
BACKLOG = 5

# How long (seconds) one client may keep us waiting before we hang up.
# Our server serves one visitor at a time, so a silent visitor would
# otherwise block the queue forever.
CLIENT_TIMEOUT = 5.0

# The biggest envelope and letter we'll accept. Without limits, one visitor
# could hand us an endless letter and we'd keep reading until memory ran out.
MAX_HEADER_BYTES = 16 * 1024
MAX_BODY_BYTES = 1024 * 1024

# Only spaces and tabs pad a header value. A bare strip() would also remove
# look-alike blanks such as "\x0b", which another server might not ignore.
_PADDING = " \t"
_MAX_LENGTH_DIGITS = 18


class RequestRefusedError(Exception):
    """We won't read this request; answer with a status and hang up.

    Args:
        status: The HTTP status to answer with.

    """

    def __init__(self, status: StatusCode) -> None:
        """Remember which status to send back."""
        super().__init__(STATUS_PHRASES[status])
        self.status = status


class RequestTooLargeError(RequestRefusedError):
    """The client sent more than we're willing to read (413 or 431)."""


def _content_length(header_text: str) -> int:
    """Read the Content-Length header value, or 0 if it's absent.

    Content-Length is the sender writing "this letter is N pages long"
    on the envelope so we know when we've read the whole thing.

    Raises:
        RequestRefusedError: ``400`` if the value is not 1 to 18 plain ASCII
            digits (spaces and tabs around it are fine), or if repeated
            Content-Length headers disagree (identical repeats are fine, as in
            ``http1.body_length``). Two programs that
            read an odd length differently could disagree about where the
            body ends -- the trick behind "request smuggling".

    """
    tokens: list[str] = []
    for line in header_text.replace("\r\n", "\n").split("\n"):
        name, sep, value = line.partition(":")
        if sep and name.strip(_PADDING).lower() == "content-length":
            tokens.extend(t.strip(_PADDING) for t in value.split(","))
    if not tokens:
        return 0
    if not all(t.isascii() and t.isdigit() and len(t) <= _MAX_LENGTH_DIGITS for t in tokens):
        raise RequestRefusedError(StatusCode.BAD_REQUEST)
    if len({int(t) for t in tokens}) != 1:
        raise RequestRefusedError(StatusCode.BAD_REQUEST)
    return int(tokens[0])


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
        if len(data) > MAX_HEADER_BYTES:
            raise RequestTooLargeError(StatusCode.HEADERS_TOO_LARGE)
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
    if content_length > MAX_BODY_BYTES:
        raise RequestTooLargeError(StatusCode.PAYLOAD_TOO_LARGE)
    while len(data) - body_start < content_length:
        chunk = conn.recv(BUFFER_SIZE)
        if not chunk:
            break
        data.extend(chunk)

    return data.decode("utf-8", errors="replace")


def _drain_and_close(conn: socket.socket) -> None:
    """Politely hang up on a client who is still talking.

    If we close while unread bytes are waiting, the operating system sends a
    rude "reset" and the client may never see our reply. So we first say
    "I'm done sending", then read and throw away whatever is left (up to a
    limit, and only until the client timeout) before closing.
    """
    conn.shutdown(socket.SHUT_WR)
    discarded = 0
    while discarded <= MAX_BODY_BYTES:
        chunk = conn.recv(BUFFER_SIZE)
        if not chunk:
            return
        discarded += len(chunk)


class Server:
    """A simple HTTP server.

    Create a TCP socket, listen for connections, and dispatch
    requests through a Router.

    Args:
        router: The router to dispatch requests to.
        host: The hostname to bind to.
        port: The port to listen on.

    """

    __slots__ = ("_client_timeout", "_host", "_port", "_ready", "_router", "_running")

    def __init__(
        self,
        router: Router,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        client_timeout: float = CLIENT_TIMEOUT,
    ) -> None:
        """Create a server with the given router.

        Pass ``port=0`` to let the operating system pick a free port; the
        ``port`` property reports the real one once the server is listening.
        """
        self._router = router
        self._host = host
        self._port = port
        self._client_timeout = client_timeout
        self._running = False
        self._ready = threading.Event()

    @property
    def host(self) -> str:
        """Return the server's host address."""
        return self._host

    @property
    def port(self) -> int:
        """Return the server's port number."""
        return self._port

    @property
    def ready(self) -> threading.Event:
        """Return an event that is set once the server is listening."""
        return self._ready

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
            self._port = sock.getsockname()[1]  # the real port, if we asked for 0
            sock.listen(BACKLOG)
            sock.settimeout(1.0)
            self._running = True
            self._ready.set()

            while self._running:
                try:
                    conn, _addr = sock.accept()
                except TimeoutError:
                    continue

                with conn:
                    self._serve_connection(conn)

    def _serve_connection(self, conn: socket.socket) -> None:
        """Answer one visitor -- and never let them take the post office down.

        A visitor might hang up halfway (connection reset), say nothing at
        all (timeout), or post an endless letter (too large). Each of those is
        *their* problem: we log it, drop that one connection, and carry on
        serving everyone else.
        """
        conn.settimeout(self._client_timeout)
        try:
            try:
                raw = _read_request(conn)
            except RequestRefusedError as exc:
                status = exc.status
                page = html_response(f"<h1>{status} {exc}</h1>", status=status)
                conn.sendall(page.to_bytes())
                _drain_and_close(conn)
                return
            if raw:
                conn.sendall(self.handle_request(raw))
        except OSError as exc:  # timeouts, resets and broken pipes are all OSErrors
            sys.stderr.write(f"dropped a connection: {exc!r}\n")

    def stop(self) -> None:
        """Signal the server to stop accepting connections."""
        self._running = False
