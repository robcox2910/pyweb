"""Tests for the in-memory TestClient."""

from collections.abc import Coroutine
from typing import Any

import pytest
from pynet.loop import run
from pynet.loop.testing import FakeClock

from pyweb.app import App
from pyweb.messages import HttpRequest, HttpResponse, JsonValue, text
from pyweb.testing import TestClient, TestResponse

STATUS_OK = 200
STATUS_CREATED = 201
STATUS_NOT_FOUND = 404
STATUS_METHOD_NOT_ALLOWED = 405


def go[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on a fresh loop with an auto-jumping fake clock."""
    return run(coro, clock=FakeClock(autojump=True))


def echo_app() -> App:
    """Build an app whose routes report what they received."""
    app = App()

    @app.get("/echo")
    def echo(request: HttpRequest) -> dict[str, JsonValue]:  # pyright: ignore[reportUnusedFunction]
        """Report the request parts."""
        return {
            "method": request.method,
            "path": request.path,
            "query": [list(pair) for pair in request.query.items()],
            "client": list(request.client or ()),
            "cookies": dict(request.cookies),
            "host": request.headers.get("Host"),
            "x": request.headers.get("X-Test"),
        }

    @app.get("/files/<path:name>")
    def files(_request: HttpRequest, name: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Echo the decoded name."""
        return name

    @app.post("/json")
    def take_json(request: HttpRequest) -> dict[str, JsonValue]:  # pyright: ignore[reportUnusedFunction]
        """Echo the JSON body."""
        return {"got": request.json(), "length": request.headers.get("Content-Length")}

    @app.post("/form")
    def take_form(request: HttpRequest) -> dict[str, JsonValue]:  # pyright: ignore[reportUnusedFunction]
        """Echo a form field."""
        return {"name": request.form().get("name")}

    @app.put("/raw")
    def take_raw(request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Echo the raw body."""
        return text(request.body.decode(), STATUS_CREATED)

    @app.post("/login")
    def login(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Set two cookies."""
        response = text("in")
        response.set_cookie("session", "abc123")
        response.set_cookie("theme", "dark", max_age=60)
        return response

    @app.delete("/login")
    def logout(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Delete the session cookie."""
        response = text("out")
        response.delete_cookie("session")
        response.headers.add("Set-Cookie", "junk-without-equals")
        return response

    return app


class TestRequests:
    """Test how the TestClient builds requests."""

    def test_get_with_query_headers_and_client(self) -> None:
        """Send query pairs and headers, and name the client."""
        client = TestClient(echo_app())
        response = go(
            client.get("/echo?a=1", query={"b": "two words"}, headers={"X-Test": "yes"}),
        )
        assert response.status == STATUS_OK
        data = response.json()
        assert data == {
            "method": "GET",
            "path": "/echo",
            "query": [["a", "1"], ["b", "two words"]],
            "client": ["testclient", 0],
            "cookies": {},
            "host": "testserver",
            "x": "yes",
        }

    def test_path_is_percent_decoded(self) -> None:
        """Decode the path the same way the server does."""
        client = TestClient(echo_app())
        assert go(client.get("/files/a%20b/c%C3%A9.txt")).text == "a b/cé.txt"

    def test_non_ascii_path_is_encoded_for_you(self) -> None:
        """Accept a path with spaces and accents."""
        client = TestClient(echo_app())
        assert go(client.get("/files/my café.txt")).text == "my café.txt"

    def test_json_body(self) -> None:
        """Encode JSON and set Content-Type and Content-Length."""
        client = TestClient(echo_app())
        response = go(client.post("/json", json={"score": 3}))
        assert response.json() == {"got": {"score": 3}, "length": "12"}

    def test_form_body(self) -> None:
        """Encode a form with the right Content-Type."""
        client = TestClient(echo_app())
        assert go(client.post("/form", form={"name": "Zoë"})).json() == {"name": "Zoë"}

    def test_raw_body(self) -> None:
        """Send raw bytes as the body."""
        client = TestClient(echo_app())
        response = go(client.put("/raw", body=b"hello"))
        assert response.status == STATUS_CREATED
        assert response.text == "hello"

    def test_two_bodies_are_rejected(self) -> None:
        """Refuse more than one of json, form and body."""
        client = TestClient(echo_app())
        with pytest.raises(ValueError, match="one of"):
            go(client.post("/json", json={}, body=b"x"))

    def test_head_and_patch(self) -> None:
        """Send HEAD and PATCH requests."""
        client = TestClient(echo_app())
        head = go(client.head("/echo"))
        assert head.status == STATUS_OK
        assert head.body == b""
        assert go(client.patch("/echo")).status == STATUS_METHOD_NOT_ALLOWED

    def test_unknown_path(self) -> None:
        """Pass a 404 straight back."""
        assert go(TestClient(echo_app()).get("/nope")).status == STATUS_NOT_FOUND


class TestCookies:
    """Test the cookie jar."""

    def test_round_trip_and_delete(self) -> None:
        """Store Set-Cookie values, send them back, and forget deleted ones.

        A malformed Set-Cookie header is ignored.
        """
        client = TestClient(echo_app())

        async def main() -> list[JsonValue]:
            """Log in, look, log out, look again."""
            await client.post("/login")
            during = (await client.get("/echo")).json()
            await client.delete("/login")
            after = (await client.get("/echo")).json()
            return [during, after]

        during, after = go(main())
        assert isinstance(during, dict)
        assert isinstance(after, dict)
        assert during["cookies"] == {"session": "abc123", "theme": "dark"}
        assert after["cookies"] == {"theme": "dark"}
        assert client.cookies == {"theme": "dark"}


class TestResponseHelpers:
    """Test TestResponse helpers."""

    def test_text_and_json(self) -> None:
        """Decode the body as text and as JSON."""
        response = TestResponse(STATUS_OK, HttpResponse().headers, b'{"a": 1}')
        assert response.text == '{"a": 1}'
        assert response.json() == {"a": 1}
