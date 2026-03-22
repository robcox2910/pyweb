"""Tests for HTTP response building.

A response is the reply letter. These tests verify that responses
are built and serialized correctly.
"""

from pyweb.response import (
    Response,
    StatusCode,
    html_response,
    json_response,
    not_found,
    text_response,
)

STATUS_200 = 200
STATUS_404 = 404


class TestResponseSerialization:
    """Verify response serialization to bytes."""

    def test_basic_response(self) -> None:
        """A basic response should serialize with status line and body."""
        resp = Response(status=STATUS_200, body="Hello")
        raw = resp.to_bytes()
        assert b"HTTP/1.1 200 OK" in raw
        assert b"Hello" in raw

    def test_content_length_auto_set(self) -> None:
        """Content-Length should be set automatically."""
        resp = Response(body="Hello")
        raw = resp.to_bytes()
        assert b"Content-Length: 5" in raw

    def test_custom_header(self) -> None:
        """Custom headers should appear in the response."""
        resp = Response(headers={"X-Custom": "value"})
        raw = resp.to_bytes()
        assert b"X-Custom: value" in raw


class TestResponseHelpers:
    """Verify convenience response functions."""

    def test_html_response(self) -> None:
        """html_response should set Content-Type to text/html."""
        resp = html_response("<h1>Hi</h1>")
        assert resp.headers["Content-Type"] == "text/html; charset=utf-8"
        assert resp.body == "<h1>Hi</h1>"
        assert resp.status == STATUS_200

    def test_json_response(self) -> None:
        """json_response should set Content-Type to application/json."""
        resp = json_response('{"ok": true}')
        assert resp.headers["Content-Type"] == "application/json"

    def test_text_response(self) -> None:
        """text_response should set Content-Type to text/plain."""
        resp = text_response("hello")
        assert resp.headers["Content-Type"] == "text/plain"

    def test_not_found(self) -> None:
        """not_found should return a 404 response."""
        resp = not_found()
        assert resp.status == STATUS_404
        assert "404" in resp.body

    def test_html_with_status(self) -> None:
        """html_response should accept a custom status code."""
        resp = html_response("<h1>Created</h1>", status=StatusCode.CREATED)
        assert resp.status == StatusCode.CREATED


class TestStatusCode:
    """Verify the StatusCode enum."""

    def test_ok(self) -> None:
        """200 should be OK."""
        assert StatusCode.OK == STATUS_200

    def test_not_found_code(self) -> None:
        """404 should be NOT_FOUND."""
        assert StatusCode.NOT_FOUND == STATUS_404
