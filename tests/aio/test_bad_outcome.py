"""Tests for middleware that answers with something that is not a response."""

import logging

import pytest

from pyweb.app import App, CallNext
from pyweb.aserver import AsyncServer
from pyweb.messages import HttpRequest, HttpResponse, Outcome, text
from pyweb.testing import TestClient
from tests.aio._auth_helpers import go
from tests.aio._helpers import OK, SERVER_ERROR, Reply, connect, read_reply, send, serve

GET_OOPS = b"GET /oops HTTP/1.1\r\nHost: x\r\n\r\n"
GET_HELLO = b"GET /hello HTTP/1.1\r\nHost: x\r\n\r\n"


def forgetful_app() -> App:
    """Build an app whose middleware forgets ``return`` on some paths."""
    app = App()

    @app.middleware
    async def forgetful(request: HttpRequest, call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
        """Forget to return on /oops, answer a dict on /dict and 42 on /number."""
        outcome = await call_next(request)
        if request.path == "/oops":
            return None  # pyright: ignore[reportReturnType]
        if request.path == "/dict":
            return {"made": "by middleware"}  # pyright: ignore[reportReturnType]
        if request.path == "/number":
            return 42  # pyright: ignore[reportReturnType]
        return outcome

    def hello(_request: HttpRequest) -> HttpResponse:
        """Say hello."""
        return text("hello")

    for path in ("/hello", "/oops", "/dict", "/number"):
        app.get(path)(hello)
    return app


class TestBadOutcomeOverTheWire:
    """Test that a broken middleware answer is a clean 500 on a real socket."""

    def test_forgotten_return_is_500_and_connection_keeps_working(self) -> None:
        """Answer 500 for the forgotten return, then serve the next request."""

        async def client(server: AsyncServer) -> tuple[Reply, Reply]:
            reader, writer = await connect(server)
            await send(writer, GET_OOPS)
            first = await read_reply(reader)
            await send(writer, GET_HELLO)
            second = await read_reply(reader)
            writer.close()
            return first, second

        first, second = serve(forgetful_app(), client, access_log=False)
        assert first.status == SERVER_ERROR
        assert second.status == OK
        assert second.body == b"hello"


class TestBadOutcomeInTestClient:
    """Test that the TestClient sees the same 500 and helpful log."""

    def test_forgotten_return_is_500(self, caplog: pytest.LogCaptureFixture) -> None:
        """Answer 500 and log which kind of value came back."""
        with caplog.at_level(logging.ERROR, logger="pyweb.app"):
            response = go(TestClient(forgetful_app()).get("/oops"))
        assert response.status == SERVER_ERROR
        assert any("NoneType" in record.getMessage() for record in caplog.records)

    def test_other_values_are_500(self, caplog: pytest.LogCaptureFixture) -> None:
        """Answer 500 for a value that is no kind of response."""
        with caplog.at_level(logging.ERROR, logger="pyweb.app"):
            response = go(TestClient(forgetful_app()).get("/number"))
        assert response.status == SERVER_ERROR
        assert any("int" in record.getMessage() for record in caplog.records)

    def test_dict_is_coerced_to_json(self) -> None:
        """Turn a dict from middleware into JSON, like a handler's dict."""
        response = go(TestClient(forgetful_app()).get("/dict"))
        assert response.status == OK
        assert response.json() == {"made": "by middleware"}
