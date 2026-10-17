"""Tests that NaN and Infinity never sneak into or out of JSON.

``NaN`` and ``Infinity`` are not part of JSON, so other programs (and
browsers' ``JSON.parse``) choke on them. PyWeb refuses to write them and
refuses to read them.
"""

import logging
import math

import pytest

from pyweb.app import App, CallNext
from pyweb.messages import Headers, HTTPError, HttpRequest, JsonValue, Outcome, QueryParams, json
from pyweb.testing import TestClient
from tests.aio._auth_helpers import go

BAD_REQUEST = 400
SERVER_ERROR = 500
NOT_NUMBERS = ["NaN", "[Infinity]", '{"x": -Infinity}']


def body_request(body: bytes) -> HttpRequest:
    """Build a POST request carrying a raw body."""
    return HttpRequest("POST", "/", QueryParams(), Headers(), body)


class TestWritingJson:
    """Test the ``json`` helper and handlers that return non-numbers."""

    @pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
    def test_helper_refuses_non_numbers(self, value: float) -> None:
        """Raise ValueError instead of writing invalid JSON."""
        with pytest.raises(ValueError, match="JSON"):
            json({"x": value})

    def test_handler_returning_nan_is_logged_500(self, caplog: pytest.LogCaptureFixture) -> None:
        """Answer a logged 500 when a handler's dict holds NaN."""
        app = App()

        @app.get("/nan")
        def nan(_request: HttpRequest) -> dict[str, JsonValue]:  # pyright: ignore[reportUnusedFunction]
            """Return a dict holding NaN."""
            return {"x": math.nan}

        with caplog.at_level(logging.ERROR, logger="pyweb.app"):
            response = go(TestClient(app).get("/nan"))
        assert response.status == SERVER_ERROR
        assert b"NaN" not in response.body
        assert caplog.records

    def test_middleware_returning_nan_is_logged_500(self) -> None:
        """Answer a 500 when a middleware's dict holds Infinity."""
        app = App()

        @app.middleware
        async def broken(_request: HttpRequest, _call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Answer with a dict holding Infinity."""
            return {"x": math.inf}  # pyright: ignore[reportReturnType]

        assert go(TestClient(app).get("/")).status == SERVER_ERROR


class TestReadingJson:
    """Test that a request body with NaN or Infinity is a 400."""

    @pytest.mark.parametrize("body", NOT_NUMBERS)
    def test_non_numbers_are_400(self, body: str) -> None:
        """Raise HTTPError(400) for the NaN/Infinity constants."""
        with pytest.raises(HTTPError) as caught:
            body_request(body.encode()).json()
        assert caught.value.status == BAD_REQUEST

    def test_ordinary_numbers_still_parse(self) -> None:
        """Parse normal numbers as usual."""
        assert body_request(b'{"x": 1.5, "y": -2}').json() == {"x": 1.5, "y": -2}
