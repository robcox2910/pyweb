"""Tests for headers, requests, responses and cookies."""

import pytest

from pyweb.messages import (
    Headers,
    HTTPError,
    HttpRequest,
    HttpResponse,
    QueryParams,
    empty,
    html,
    json,
    redirect,
    text,
)

STATUS_OK = 200
STATUS_NO_CONTENT = 204
STATUS_SEE_OTHER = 303
STATUS_BAD_REQUEST = 400
STATUS_NOT_FOUND = 404
STATUS_UNSUPPORTED = 415
MAX_AGE = 60
STATUS_CREATED = 201
STATUS_UNKNOWN = 499
COOKIE_COUNT = 2


def make_request(
    headers: list[tuple[str, str]] | None = None,
    body: bytes = b"",
    query: list[tuple[str, str]] | None = None,
) -> HttpRequest:
    """Build a request with the given headers and body."""
    return HttpRequest("GET", "/", QueryParams(query or []), Headers(headers or []), body)


class TestHeaders:
    """Tests for Headers."""

    def test_case_insensitive_get(self) -> None:
        """Look up names without regard to case."""
        headers = Headers([("Content-Type", "text/plain")])
        assert headers.get("content-type") == "text/plain"
        assert "CONTENT-TYPE" in headers
        assert headers.get("missing") is None
        assert headers.get("missing", "x") == "x"

    def test_order_and_multi(self) -> None:
        """Preserve order and repeated names."""
        headers = Headers([("A", "1"), ("b", "2")])
        headers.add("a", "3")
        assert headers.get_all("A") == ["1", "3"]
        assert headers.get("a") == "1"
        assert headers.items() == [("A", "1"), ("b", "2"), ("a", "3")]

    def test_set_replaces_all(self) -> None:
        """Replace every existing value on set."""
        headers = Headers([("A", "1"), ("a", "2")])
        headers.set("A", "9")
        assert headers.items() == [("A", "9")]

    def test_remove(self) -> None:
        """Remove all values of a name."""
        headers = Headers([("A", "1"), ("B", "2"), ("a", "3")])
        headers.remove("A")
        assert headers.items() == [("B", "2")]

    @pytest.mark.parametrize("bad", ["a\r\nX: y", "a\nb", "a\rb", "a\x00b"])
    def test_injection_rejected(self, bad: str) -> None:
        """Reject CR, LF and NUL in values and names."""
        headers = Headers()
        with pytest.raises(ValueError, match="header"):
            headers.set("X", bad)
        with pytest.raises(ValueError, match="header"):
            headers.add("X", bad)
        with pytest.raises(ValueError, match="header"):
            headers.add(bad, "v")
        with pytest.raises(ValueError, match="header"):
            Headers([("X", bad)])

    @pytest.mark.parametrize("name", ["", "a b", "a:b"])
    def test_bad_names_rejected(self, name: str) -> None:
        """Reject names that are not HTTP tokens."""
        with pytest.raises(ValueError, match="header"):
            Headers().set(name, "v")


class TestQueryParams:
    """Tests for QueryParams."""

    def test_lookup(self) -> None:
        """Get first, all, and membership."""
        query = QueryParams([("a", "1"), ("b", "2"), ("a", "3")])
        assert query.get("a") == "1"
        assert query.get_all("a") == ["1", "3"]
        assert query.get("z") is None
        assert query.get("z", "d") == "d"
        assert "b" in query
        assert "z" not in query
        assert query.items() == [("a", "1"), ("b", "2"), ("a", "3")]


class TestRequest:
    """Tests for HttpRequest."""

    def test_defaults_not_shared(self) -> None:
        """Give each request its own mutable dicts."""
        one, two = make_request(), make_request()
        one.state["k"] = 1
        one.path_params["p"] = 1
        assert two.state == {}
        assert two.path_params == {}
        assert one.user is None
        assert one.client is None

    def test_cookies(self) -> None:
        """Parse cookies and skip malformed pairs."""
        request = make_request([("Cookie", "a=1; b=2; bad; c=")])
        assert request.cookies == {"a": "1", "b": "2", "c": ""}

    def test_no_cookies(self) -> None:
        """Return an empty dict without a Cookie header."""
        assert make_request().cookies == {}

    def test_text_default_utf8(self) -> None:
        """Decode as UTF-8 by default."""
        assert make_request(body="é".encode()).text() == "é"

    def test_text_charset(self) -> None:
        """Honour the charset parameter."""
        request = make_request(
            [("Content-Type", "text/plain; charset=latin-1")], "é".encode("latin-1")
        )
        assert request.text() == "é"

    def test_text_bad_bytes(self) -> None:
        """Turn decode errors into 400."""
        with pytest.raises(HTTPError) as info:
            make_request(body=b"\xff\xfe").text()
        assert info.value.status == STATUS_BAD_REQUEST

    def test_text_unknown_charset(self) -> None:
        """Turn an unknown charset into 400."""
        request = make_request([("Content-Type", "text/plain; charset=nope")], b"x")
        with pytest.raises(HTTPError) as info:
            request.text()
        assert info.value.status == STATUS_BAD_REQUEST

    def test_json(self) -> None:
        """Parse a JSON body."""
        assert make_request(body=b'{"a": [1, 2]}').json() == {"a": [1, 2]}

    def test_json_bad(self) -> None:
        """Turn invalid JSON into 400."""
        with pytest.raises(HTTPError) as info:
            make_request(body=b"{nope").json()
        assert info.value.status == STATUS_BAD_REQUEST

    def test_form(self) -> None:
        """Parse an urlencoded form."""
        request = make_request(
            [("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")],
            b"a=1&a=2&b=x+y",
        )
        form = request.form()
        assert form.get_all("a") == ["1", "2"]
        assert form.get("b") == "x y"

    def test_form_wrong_type(self) -> None:
        """Answer 415 for non-form content types."""
        request = make_request([("Content-Type", "application/json")], b"{}")
        with pytest.raises(HTTPError) as info:
            request.form()
        assert info.value.status == STATUS_UNSUPPORTED

    def test_form_missing_type(self) -> None:
        """Answer 415 when Content-Type is absent."""
        with pytest.raises(HTTPError) as info:
            make_request(body=b"a=1").form()
        assert info.value.status == STATUS_UNSUPPORTED

    def test_last_event_id(self) -> None:
        """Read Last-Event-ID."""
        assert make_request([("Last-Event-ID", "42")]).last_event_id == "42"
        assert make_request().last_event_id is None

    def test_wants_html(self) -> None:
        """Detect text/html in Accept."""
        assert make_request([("Accept", "text/html,application/json")]).wants_html
        assert not make_request([("Accept", "application/json")]).wants_html
        assert not make_request().wants_html


class TestResponse:
    """Tests for HttpResponse and cookies."""

    def test_defaults_not_shared(self) -> None:
        """Give each response its own headers."""
        one, two = HttpResponse(), HttpResponse()
        one.headers.add("X", "1")
        assert two.headers.items() == []
        assert one.status == STATUS_OK
        assert one.body == b""

    def test_set_cookie_exact(self) -> None:
        """Format a Set-Cookie header exactly."""
        response = HttpResponse()
        response.set_cookie("session", "abc", max_age=MAX_AGE)
        assert response.headers.get("Set-Cookie") == (
            "session=abc; Path=/; Max-Age=60; HttpOnly; SameSite=Lax"
        )

    def test_set_cookie_options(self) -> None:
        """Apply the secure, http_only and same_site options."""
        response = HttpResponse()
        response.set_cookie("a", "b", secure=True, http_only=False, same_site="Strict", path="/x")
        assert response.headers.get("Set-Cookie") == "a=b; Path=/x; SameSite=Strict; Secure"

    def test_multiple_cookies(self) -> None:
        """Add one Set-Cookie header per cookie."""
        response = HttpResponse()
        response.set_cookie("a", "1")
        response.set_cookie("b", "2")
        assert len(response.headers.get_all("Set-Cookie")) == COOKIE_COUNT

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("a b", "v"),
            ("a;b", "v"),
            ("a,b", "v"),
            ("", "v"),
            ("a", "x;y"),
            ("a", "x y"),
            ("a", "x\x01"),
            ("a", "x,y"),
            ("a=b", "v"),
        ],
    )
    def test_bad_cookie_rejected(self, name: str, value: str) -> None:
        """Reject names and values with forbidden characters."""
        with pytest.raises(ValueError, match="cookie"):
            HttpResponse().set_cookie(name, value)

    def test_bad_attribute_rejected(self) -> None:
        """Reject path and same_site values with forbidden characters."""
        with pytest.raises(ValueError, match="cookie"):
            HttpResponse().set_cookie("a", "b", path="/x; Domain=evil")
        with pytest.raises(ValueError, match="cookie"):
            HttpResponse().set_cookie("a", "b", same_site="Lax; Secure")

    def test_delete_cookie(self) -> None:
        """Expire a cookie with Max-Age=0."""
        response = HttpResponse()
        response.delete_cookie("session")
        cookie = response.headers.get("Set-Cookie")
        assert cookie is not None
        assert cookie.startswith("session=; Path=/; Max-Age=0")


class TestHelpers:
    """Tests for response helper functions."""

    def test_json(self) -> None:
        """Build a JSON response."""
        response = json({"a": 1}, STATUS_CREATED)
        assert response.status == STATUS_CREATED
        assert response.headers.get("Content-Type") == "application/json"
        assert response.body == b'{"a": 1}'

    def test_html_and_text(self) -> None:
        """Build HTML and text responses."""
        page = html("<p>é</p>")
        assert page.headers.get("Content-Type") == "text/html; charset=utf-8"
        assert page.body == "<p>é</p>".encode()
        plain = text("hi", STATUS_NOT_FOUND)
        assert plain.headers.get("Content-Type") == "text/plain; charset=utf-8"
        assert plain.status == STATUS_NOT_FOUND

    def test_redirect(self) -> None:
        """Build a redirect."""
        response = redirect("/next")
        assert response.status == STATUS_SEE_OTHER
        assert response.headers.get("Location") == "/next"

    def test_redirect_injection(self) -> None:
        """Reject newlines in the location."""
        with pytest.raises(ValueError, match="header"):
            redirect("/a\r\nSet-Cookie: x=1")

    def test_empty(self) -> None:
        """Build an empty response."""
        response = empty()
        assert response.status == STATUS_NO_CONTENT
        assert response.body == b""


class TestHTTPError:
    """Tests for HTTPError."""

    def test_json_response(self) -> None:
        """Answer JSON by default."""
        response = HTTPError(STATUS_NOT_FOUND, "no such thing").to_response(make_request())
        assert response.status == STATUS_NOT_FOUND
        assert response.headers.get("Content-Type") == "application/json"
        assert b'"error": "Not Found"' in response.body
        assert b'"message": "no such thing"' in response.body

    def test_json_without_message(self) -> None:
        """Use null for a missing message."""
        response = HTTPError(STATUS_NOT_FOUND).to_response(make_request())
        assert b'"message": null' in response.body

    def test_html_escapes_message(self) -> None:
        """Escape the message in HTML pages."""
        request = make_request([("Accept", "text/html")])
        response = HTTPError(STATUS_NOT_FOUND, "<x>").to_response(request)
        assert response.headers.get("Content-Type") == "text/html; charset=utf-8"
        assert b"&lt;x&gt;" in response.body
        assert b"<x>" not in response.body

    def test_unknown_status_reason(self) -> None:
        """Fall back to a generic reason for unknown codes."""
        response = HTTPError(STATUS_UNKNOWN).to_response(make_request())
        assert b'"error"' in response.body
