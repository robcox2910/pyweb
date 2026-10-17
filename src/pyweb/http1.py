"""HTTP/1.1 byte layer: parse request heads and encode responses.

Think of this module as the post office sorting room. Letters (bytes) arrive
in a bag; this code checks the address label (the request line), reads each
sticker on the envelope (the headers), and refuses anything that looks
suspicious. It never talks to a socket or an event loop, so it is pure and
easy to test.
"""

import math
import string
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote_to_bytes

KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

STATUS_REASONS: dict[int, str] = {
    200: "OK",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    303: "See Other",
    304: "Not Modified",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    411: "Length Required",
    413: "Content Too Large",
    415: "Unsupported Media Type",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
    500: "Internal Server Error",
    501: "Not Implemented",
    503: "Service Unavailable",
    505: "HTTP Version Not Supported",
}

_BAD_REQUEST = 400
_LENGTH_REQUIRED = 411
_HEADERS_TOO_LARGE = 431
_NOT_IMPLEMENTED = 501
_BAD_VERSION = 505
_REQUEST_LINE_PARTS = 3
_MAX_LENGTH_DIGITS = 18
_SUPPORTED_VERSIONS = frozenset({"HTTP/1.0", "HTTP/1.1"})
_TOKEN_CHARS = frozenset(string.ascii_letters + string.digits + "!#$%&'*+-.^_`|~")


@dataclass(frozen=True)
class Limits:
    """Size, count, and time limits for one HTTP server.

    Every value must be positive and finite.
    """

    max_head_bytes: int = 16 * 1024
    max_headers: int = 100
    max_body_bytes: int = 1024 * 1024
    header_timeout: float = 10.0
    body_timeout: float = 30.0
    keepalive_timeout: float = 5.0
    write_timeout: float = 30.0
    max_connections: int = 1_000

    def __post_init__(self) -> None:
        """Validate that every limit is positive and finite."""
        for name, value in vars(self).items():
            if not math.isfinite(value) or value <= 0:
                msg = f"{name} must be positive and finite, got {value!r}"
                raise ValueError(msg)


class HttpParseError(Exception):
    """Raise when a request cannot be accepted; carry the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        """Store the status code and message."""
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RequestHead:
    """A parsed request line plus headers."""

    method: str
    target: str
    version: str
    headers: list[tuple[str, str]]


def _fail(status: int, message: str) -> HttpParseError:
    """Build an HttpParseError."""
    return HttpParseError(status, message)


def _parse_header_line(line: str) -> tuple[str, str]:
    """Split one header line into a (name, value) pair."""
    if line[:1] in (" ", "\t"):
        raise _fail(_BAD_REQUEST, "obsolete line folding is not allowed")
    name, sep, value = line.partition(":")
    if not sep or not name:
        raise _fail(_BAD_REQUEST, "malformed header line")
    if not all(ch in _TOKEN_CHARS for ch in name):
        raise _fail(_BAD_REQUEST, "invalid header name")
    return name, value.strip(" \t")


def parse_head(raw: bytes, limits: Limits) -> RequestHead:
    """Parse the request head (everything before the blank line)."""
    try:
        text = raw.decode("latin-1")
    except UnicodeDecodeError as exc:  # pragma: no cover - latin-1 decodes any byte
        raise _fail(_BAD_REQUEST, "head is not latin-1") from exc
    request_line, *header_lines = text.split("\r\n")
    parts = request_line.split(" ")
    if len(parts) != _REQUEST_LINE_PARTS or not all(parts):
        raise _fail(_BAD_REQUEST, "malformed request line")
    method, target, version = parts
    if not version.startswith("HTTP/"):
        raise _fail(_BAD_REQUEST, "malformed request line")
    if version not in _SUPPORTED_VERSIONS:
        raise _fail(_BAD_VERSION, f"unsupported version {version}")
    if method not in KNOWN_METHODS:
        raise _fail(_NOT_IMPLEMENTED, f"unsupported method {method}")
    if len(header_lines) > limits.max_headers:
        raise _fail(_HEADERS_TOO_LARGE, "too many headers")
    headers = [_parse_header_line(line) for line in header_lines]
    return RequestHead(method, target, version, headers)


def _values(head: RequestHead, name: str) -> list[str]:
    """Return every value of a header, case-insensitively."""
    return [v for k, v in head.headers if k.lower() == name]


def body_length(head: RequestHead) -> int:
    """Return the declared body length, rejecting ambiguous framing."""
    lengths = _values(head, "content-length")
    has_te = bool(_values(head, "transfer-encoding"))
    if lengths and has_te:
        raise _fail(_BAD_REQUEST, "both Content-Length and Transfer-Encoding")
    if has_te:
        raise _fail(_LENGTH_REQUIRED, "Transfer-Encoding is not supported")
    if not lengths:
        return 0
    tokens = [t.strip() for value in lengths for t in value.split(",")]
    if not all(t.isascii() and t.isdigit() and len(t) <= _MAX_LENGTH_DIGITS for t in tokens):
        raise _fail(_BAD_REQUEST, "invalid Content-Length")
    if len({int(t) for t in tokens}) != 1:
        raise _fail(_BAD_REQUEST, "conflicting Content-Length values")
    return int(tokens[0])


def split_target(target: str) -> tuple[str, list[tuple[str, str]]]:
    """Split a request target into a decoded path and query pairs."""
    if not target.startswith("/"):
        raise _fail(_BAD_REQUEST, "target must start with /")
    raw_path, _, query = target.partition("?")
    try:
        # The head was decoded as latin-1, so re-encode to get the original bytes.
        path = unquote_to_bytes(raw_path.encode("latin-1")).decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError) as exc:
        raise _fail(_BAD_REQUEST, "invalid percent-encoding in path") from exc
    return path, parse_qsl(query, keep_blank_values=True)


def wants_keep_alive(head: RequestHead) -> bool:
    """Return whether the connection should stay open after this request."""
    tokens = {t.strip().lower() for value in _values(head, "connection") for t in value.split(",")}
    if head.version == "HTTP/1.1":
        return "close" not in tokens
    return "keep-alive" in tokens and "close" not in tokens


def encode_response_head(status: int, reason: str, headers: list[tuple[str, str]]) -> bytes:
    """Encode a status line and headers, ending with the blank line."""
    lines = [f"HTTP/1.1 {status} {reason}", *(f"{k}: {v}" for k, v in headers), "", ""]
    return "\r\n".join(lines).encode("latin-1")


def encode_chunk(data: bytes) -> bytes:
    """Frame data as one chunk; empty data gives the terminating chunk."""
    if not data:
        return b"0\r\n\r\n"
    return b"%x\r\n%b\r\n" % (len(data), data)
