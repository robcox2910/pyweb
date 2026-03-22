"""Tests for HTTP request parsing.

A request is the incoming letter. These tests verify that raw HTTP
text is parsed correctly into structured Request objects.
"""

import pytest

from pyweb.errors import ParseError
from pyweb.request import Request, parse_request


class TestParseRequestLine:
    """Verify parsing of the request line (method, path, version)."""

    def test_get_request(self) -> None:
        """A simple GET request should parse correctly."""
        req = parse_request("GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert req.method == "GET"
        assert req.path == "/"

    def test_post_request(self) -> None:
        """A POST request should parse correctly."""
        req = parse_request("POST /api/data HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert req.method == "POST"
        assert req.path == "/api/data"

    def test_method_case_insensitive(self) -> None:
        """Method should be uppercased."""
        req = parse_request("get /test HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert req.method == "GET"

    def test_unsupported_method_raises(self) -> None:
        """An unsupported method should raise ParseError."""
        with pytest.raises(ParseError, match="Unsupported"):
            parse_request("CONNECT / HTTP/1.1\r\n\r\n")

    def test_empty_request_raises(self) -> None:
        """An empty request should raise ParseError."""
        with pytest.raises(ParseError, match="Empty"):
            parse_request("")

    def test_malformed_request_raises(self) -> None:
        """A request line with missing parts should raise ParseError."""
        with pytest.raises(ParseError, match="Malformed"):
            parse_request("GET\r\n\r\n")


class TestParseHeaders:
    """Verify header parsing."""

    def test_single_header(self) -> None:
        """A single header should be parsed."""
        req = parse_request("GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        assert req.headers["Host"] == "example.com"

    def test_multiple_headers(self) -> None:
        """Multiple headers should all be parsed."""
        raw = "GET / HTTP/1.1\r\nHost: example.com\r\nAccept: text/html\r\n\r\n"
        req = parse_request(raw)
        assert req.headers["Host"] == "example.com"
        assert req.headers["Accept"] == "text/html"


class TestParseBody:
    """Verify body parsing."""

    def test_body_present(self) -> None:
        """A POST with a body should include it."""
        raw = "POST /api HTTP/1.1\r\nHost: localhost\r\n\r\n{\"key\": \"value\"}"
        req = parse_request(raw)
        assert req.body == '{"key": "value"}'

    def test_no_body(self) -> None:
        """A GET with no body should have empty body."""
        req = parse_request("GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert req.body == ""


class TestParseQueryParams:
    """Verify query string parsing."""

    def test_single_param(self) -> None:
        """A single query parameter should be parsed."""
        req = parse_request("GET /search?q=hello HTTP/1.1\r\n\r\n")
        assert req.query_params["q"] == "hello"
        assert req.path == "/search"

    def test_multiple_params(self) -> None:
        """Multiple query parameters should be parsed."""
        req = parse_request("GET /search?q=hello&page=2 HTTP/1.1\r\n\r\n")
        assert req.query_params["q"] == "hello"
        assert req.query_params["page"] == "2"

    def test_no_params(self) -> None:
        """A path with no query string should have empty params."""
        req = parse_request("GET /about HTTP/1.1\r\n\r\n")
        assert req.query_params == {}


class TestNewlineHandling:
    """Verify both \\r\\n and \\n line endings work."""

    def test_unix_line_endings(self) -> None:
        """Requests with \\n-only should parse."""
        req = parse_request("GET / HTTP/1.1\nHost: localhost\n\n")
        assert req.method == "GET"
