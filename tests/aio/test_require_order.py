"""Tests that ``@require`` in the wrong place is an error, not a public page."""

import pytest
from pynet.ws import WebSocket

from pyweb._handlers import is_registered
from pyweb.app import App
from pyweb.auth import InMemoryUsers, SessionAuth, require
from pyweb.messages import HttpRequest
from pyweb.security import SessionSigner
from pyweb.testing import TestClient
from tests.aio._auth_helpers import DEMO_KEY, UNAUTHORIZED, go

ORDER_HINT = "below the route decorator"


def guarded_app() -> App:
    """Build an app with the auth middleware and no routes yet."""
    app = App()
    app.middleware(SessionAuth(SessionSigner(DEMO_KEY), InMemoryUsers()))
    return app


class TestRequireOrder:
    """Test the order of ``@require`` and the route decorator."""

    def test_right_order_guards_the_route(self) -> None:
        """Answer 401 to a stranger when ``@require`` is under ``@app.get``."""
        app = guarded_app()

        @app.get("/secret")
        @require()
        def secret(_request: HttpRequest) -> str:  # pyright: ignore[reportUnusedFunction]
            """Show the secret."""
            return "treasure"

        assert go(TestClient(app).get("/secret")).status == UNAUTHORIZED

    def test_wrong_order_raises_at_decoration(self) -> None:
        """Refuse ``@require`` above ``@app.get`` instead of leaving it public."""
        app = guarded_app()
        with pytest.raises(TypeError, match=ORDER_HINT):

            @require()
            @app.get("/secret")
            def secret(_request: HttpRequest) -> str:  # pyright: ignore[reportUnusedFunction]
                """Show the secret."""
                return "treasure"

    def test_wrong_order_on_websocket_raises(self) -> None:
        """Refuse ``@require`` above ``@app.websocket`` too."""
        app = guarded_app()
        with pytest.raises(TypeError, match=ORDER_HINT):

            @require("admin")
            @app.websocket("/live")
            async def live(_ws: WebSocket, _request: HttpRequest) -> None:  # pyright: ignore[reportUnusedFunction]
                """Talk to admins."""

    def test_unweakrefable_handler_still_registers(self) -> None:
        """Register a handler that cannot be weakly referenced, and still serve it."""

        class Hello:
            """A handler object without a ``__weakref__`` slot."""

            __slots__ = ()

            def __call__(self, _request: HttpRequest) -> str:
                """Say hello."""
                return "hello"

        app = App()
        handler = Hello()
        app.get("/hello")(handler)
        assert not is_registered(handler)
        assert go(TestClient(app).get("/hello")).text == "hello"
