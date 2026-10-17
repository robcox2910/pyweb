"""Tests for the HTTP/1.1 byte layer."""

import math

import pytest

from pyweb.http1 import (
    KNOWN_METHODS,
    STATUS_REASONS,
    HttpParseError,
    Limits,
    RequestHead,
    body_length,
    encode_chunk,
    encode_response_head,
    parse_head,
    split_target,
    wants_keep_alive,
)

LIMITS = Limits()
BAD_REQUEST = 400
LENGTH_REQUIRED = 411
HEADER_TOO_LARGE = 431
NOT_IMPLEMENTED = 501
BAD_VERSION = 505
FIVE = 5
TOO_MANY = 101
DEFAULT_HEADERS = 100
DEFAULT_CONNECTIONS = 1_000


def head(headers: list[tuple[str, str]], version: str = "HTTP/1.1") -> RequestHead:
    """Build a request head with the given headers."""
    return RequestHead("GET", "/", version, headers)


class TestLimits:
    """Test the Limits dataclass."""

    def test_defaults(self) -> None:
        """Check the spec defaults."""
        limits = Limits()
        assert limits.max_head_bytes == 16 * 1024
        assert limits.max_headers == DEFAULT_HEADERS
        assert limits.max_body_bytes == 1024 * 1024
        assert limits.max_connections == DEFAULT_CONNECTIONS

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_body_bytes": 0},
            {"max_head_bytes": -1},
            {"max_headers": 0},
            {"header_timeout": 0.0},
            {"body_timeout": math.inf},
            {"keepalive_timeout": math.nan},
            {"write_timeout": -1.0},
            {"max_connections": 0},
        ],
    )
    def test_invalid_values_raise(self, kwargs: dict[str, float]) -> None:
        """Reject non-positive or non-finite values."""
        with pytest.raises(ValueError, match="positive"):
            Limits(**kwargs)  # pyright: ignore[reportArgumentType]


class TestParseHead:
    """Test parse_head."""

    def test_valid_get(self) -> None:
        """Parse a simple GET."""
        result = parse_head(b"GET /a?x=1 HTTP/1.1\r\nHost: example\r\nX-A:  b  ", LIMITS)
        assert result == RequestHead(
            "GET", "/a?x=1", "HTTP/1.1", [("Host", "example"), ("X-A", "b")]
        )

    def test_valid_post_http10(self) -> None:
        """Parse a POST on HTTP/1.0 with no headers."""
        result = parse_head(b"POST /submit HTTP/1.0", LIMITS)
        assert result.method == "POST"
        assert result.version == "HTTP/1.0"
        assert result.headers == []

    def test_known_methods(self) -> None:
        """Expose the supported methods."""
        assert "GET" in KNOWN_METHODS
        assert "TRACE" not in KNOWN_METHODS

    @pytest.mark.parametrize(
        ("raw", "status"),
        [
            (b"GET / HTTP/2.0", BAD_VERSION),
            (b"GET / HTTP/1.2", BAD_VERSION),
            (b"BREW / HTTP/1.1", NOT_IMPLEMENTED),
            (b"get / HTTP/1.1", NOT_IMPLEMENTED),
            (b"GET /", BAD_REQUEST),
            (b"GET  / HTTP/1.1", BAD_REQUEST),
            (b"GET / FTP/1.1", BAD_REQUEST),
            (b"", BAD_REQUEST),
            (b"GET / HTTP/1.1\r\nHost : x", BAD_REQUEST),
            (b"GET / HTTP/1.1\r\nHost: x\r\n folded", BAD_REQUEST),
            (b"GET / HTTP/1.1\r\nHost: x\r\n\tfolded", BAD_REQUEST),
            (b"GET / HTTP/1.1\r\nnocolon", BAD_REQUEST),
            (b"GET / HTTP/1.1\r\n: empty", BAD_REQUEST),
            (b"GET / HTTP/1.1\r\nBad Name: x", BAD_REQUEST),
        ],
    )
    def test_rejections(self, raw: bytes, status: int) -> None:
        """Map each malformed head to its status."""
        with pytest.raises(HttpParseError) as info:
            parse_head(raw, LIMITS)
        assert info.value.status == status

    def test_too_many_headers(self) -> None:
        """Reject 101 headers with 431."""
        lines = [b"GET / HTTP/1.1"] + [b"X-%d: v" % i for i in range(TOO_MANY)]
        with pytest.raises(HttpParseError) as info:
            parse_head(b"\r\n".join(lines), LIMITS)
        assert info.value.status == HEADER_TOO_LARGE

    def test_exactly_max_headers_allowed(self) -> None:
        """Accept exactly 100 headers."""
        lines = [b"GET / HTTP/1.1"] + [b"X-%d: v" % i for i in range(TOO_MANY - 1)]
        assert len(parse_head(b"\r\n".join(lines), LIMITS).headers) == TOO_MANY - 1


class TestBodyLength:
    """Test body_length."""

    def test_absent_is_zero(self) -> None:
        """Return zero without Content-Length."""
        assert body_length(head([])) == 0

    def test_single_value(self) -> None:
        """Read a Content-Length."""
        assert body_length(head([("content-length", "5")])) == FIVE

    def test_duplicate_equal_values(self) -> None:
        """Accept repeated equal values."""
        assert body_length(head([("Content-Length", "5"), ("Content-Length", "5")])) == FIVE

    def test_comma_list_equal_values(self) -> None:
        """Accept a comma list of equal values."""
        assert body_length(head([("Content-Length", "5, 5")])) == FIVE

    @pytest.mark.parametrize(
        "values",
        [["5", "6"], ["-1"], ["abc"], [""], ["+5"], ["5 5"]],
    )
    def test_bad_values(self, values: list[str]) -> None:
        """Reject conflicting or non-digit values with 400."""
        headers = [("Content-Length", v) for v in values]
        with pytest.raises(HttpParseError) as info:
            body_length(head(headers))
        assert info.value.status == BAD_REQUEST

    def test_te_and_cl_is_400(self) -> None:
        """Reject both framings with 400."""
        headers = [("Content-Length", "5"), ("Transfer-Encoding", "chunked")]
        with pytest.raises(HttpParseError) as info:
            body_length(head(headers))
        assert info.value.status == BAD_REQUEST

    def test_te_alone_is_411(self) -> None:
        """Reject Transfer-Encoding alone with 411."""
        with pytest.raises(HttpParseError) as info:
            body_length(head([("Transfer-Encoding", "chunked")]))
        assert info.value.status == LENGTH_REQUIRED


class TestSplitTarget:
    """Test split_target."""

    def test_path_and_query(self) -> None:
        """Decode the path and parse the query."""
        assert split_target("/a%20b?x=1&x=2&y=") == (
            "/a b",
            [("x", "1"), ("x", "2"), ("y", "")],
        )

    def test_plus_kept_in_path(self) -> None:
        """Keep a plus sign in the path."""
        assert split_target("/a+b") == ("/a+b", [])

    @pytest.mark.parametrize("target", ["/%ff", "a", "", "*"])
    def test_rejections(self, target: str) -> None:
        """Reject bad encodings and non-origin targets."""
        with pytest.raises(HttpParseError) as info:
            split_target(target)
        assert info.value.status == BAD_REQUEST


class TestKeepAlive:
    """Test wants_keep_alive."""

    @pytest.mark.parametrize(
        ("version", "headers", "expected"),
        [
            ("HTTP/1.1", [], True),
            ("HTTP/1.1", [("Connection", "close")], False),
            ("HTTP/1.1", [("connection", "Keep-Alive, CLOSE")], False),
            ("HTTP/1.1", [("Connection", "keep-alive")], True),
            ("HTTP/1.0", [], False),
            ("HTTP/1.0", [("Connection", "keep-alive")], True),
            ("HTTP/1.0", [("Connection", "Upgrade, Keep-Alive")], True),
            ("HTTP/1.0", [("Connection", "close")], False),
        ],
    )
    def test_table(self, version: str, headers: list[tuple[str, str]], expected: bool) -> None:
        """Decide persistence by version and Connection tokens."""
        assert wants_keep_alive(head(headers, version)) is expected


class TestEncoding:
    """Test response encoding helpers."""

    def test_chunk(self) -> None:
        """Frame a chunk."""
        assert encode_chunk(b"hi") == b"2\r\nhi\r\n"

    def test_final_chunk(self) -> None:
        """Frame the terminating chunk."""
        assert encode_chunk(b"") == b"0\r\n\r\n"

    def test_large_chunk_hex(self) -> None:
        """Use hexadecimal sizes."""
        assert encode_chunk(b"x" * 255).startswith(b"ff\r\n")

    def test_response_head(self) -> None:
        """Encode status line and headers."""
        data = encode_response_head(200, "OK", [("Content-Length", "2")])
        assert data == b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n"

    def test_status_reasons(self) -> None:
        """Cover every framework status."""
        needed = {200, 204, 301, 302, 303, 304, 400, 401, 403, 404, 405, 408, 411}
        needed |= {413, 415, 429, 431, 500, 501, 503, 505}
        assert needed <= set(STATUS_REASONS)
        assert STATUS_REASONS[404] == "Not Found"
