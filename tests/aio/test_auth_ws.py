"""Tests for require() on its own and for auth on WebSocket routes."""

import inspect
from typing import Any

import pytest
from pynet.ws import WebSocket, client_handshake_request

from pyweb.app import App, WebSocketUpgrade
from pyweb.aserver import AsyncServer
from pyweb.auth import InMemoryUsers, SessionAuth, _required_roles, current_user, require
from pyweb.messages import Headers, HTTPError, HttpRequest, HttpResponse, QueryParams
from pyweb.security import SessionSigner
from tests.aio._auth_helpers import ADA_WORD, BOB_WORD, DEMO_KEY, ROUNDS, go
from tests.aio._helpers import HOST, Reply, connect, exchange, read_reply, send, serve

SWITCHING = 101
UNAUTHORIZED = 401
FORBIDDEN = 403
KEY = "dGhlIHNhbXBsZSBub25jZQ=="
SAME_ORIGIN = f"Origin: http://{HOST}\r\n"


def plain_request(user: object | None = None, *, accept: str = "") -> HttpRequest:
    """Build a GET request for /secret carrying a user."""
    request = HttpRequest("GET", "/secret", QueryParams(), Headers([("Accept", accept)]))
    request.user = user
    return request


class TestRequireDecorator:
    """Test require() directly on sync and async functions."""

    def test_keeps_name_doc_and_async_ness(self) -> None:
        """Keep the wrapped name and docstring, and match async-ness."""

        @require()
        def plain(_request: HttpRequest) -> str:
            """Say plain."""
            return "plain"

        @require()
        async def fancy(_request: HttpRequest) -> str:
            """Say fancy."""
            return "fancy"

        assert (plain.__name__, plain.__doc__) == ("plain", "Say plain.")
        assert (fancy.__name__, fancy.__doc__) == ("fancy", "Say fancy.")
        assert not inspect.iscoroutinefunction(plain)
        assert inspect.iscoroutinefunction(fancy)

    def test_sync_checks_before_calling(self) -> None:
        """Raise 401 for anonymous, 403 for a missing role, else call through."""
        users = InMemoryUsers()
        ada = users.add("ada", ADA_WORD, roles=("admin",), rounds=ROUNDS)
        bob = users.add("bob", BOB_WORD, rounds=ROUNDS)
        called: list[object] = []

        @require("admin")
        def secret(request: HttpRequest, page: int) -> str:
            """Note the call."""
            called.append((current_user(request), page))
            return "ok"

        for user, status in ((None, UNAUTHORIZED), (bob, FORBIDDEN)):
            with pytest.raises(HTTPError) as caught:
                secret(plain_request(user), page=1)
            assert caught.value.status == status
        assert secret(plain_request(ada), page=2) == "ok"
        assert called == [(ada, 2)]

    def test_async_redirects_html_without_auth_middleware(self) -> None:
        """Redirect a browser to /login when no SessionAuth set a login path."""

        @require()
        async def secret(_request: HttpRequest) -> str:
            """Never reached."""
            return "no"

        response = go(secret(plain_request(accept="text/html")))
        assert isinstance(response, HttpResponse)
        assert response.headers.get("Location") == "/login?next=%2Fsecret"

    def test_needs_a_request_argument(self) -> None:
        """Refuse to guess when no HttpRequest was passed."""

        @require()
        def lonely(*_args: Any) -> str:
            """Never reached."""
            return "no"

        with pytest.raises(TypeError):
            lonely("not a request")

    def test_websocket_handler_without_middleware_fails_closed(self) -> None:
        """Raise (never redirect) for an anonymous WebSocket handler call."""

        @require()
        async def chat(_ws: object, _request: HttpRequest) -> None:
            """Never reached."""

        with pytest.raises(HTTPError) as caught:
            go(chat(object(), plain_request(accept="text/html")))
        assert caught.value.status == UNAUTHORIZED


def ws_app(allowed_origins: tuple[str, ...] = ()) -> tuple[App, SessionAuth, InMemoryUsers]:
    """Build an app whose WebSocket route needs the admin role."""
    users = InMemoryUsers()
    users.add("ada", ADA_WORD, roles=("admin",), rounds=ROUNDS)
    users.add("bob", BOB_WORD, rounds=ROUNDS)
    auth = SessionAuth(SessionSigner(DEMO_KEY), users, allowed_origins=allowed_origins)
    app = App()
    app.middleware(auth)

    @app.websocket("/live/<room>")
    @require("admin")
    async def live(ws: WebSocket, request: HttpRequest, room: str) -> None:  # pyright: ignore[reportUnusedFunction]
        """Greet the admin by name in the room."""
        user = current_user(request)
        await ws.send_text(f"hi {None if user is None else user.name} in {room}")

    return app, auth, users


def handshake(extra: str = "") -> bytes:
    """Build a raw upgrade request for /live/lobby with extra header lines."""
    raw = client_handshake_request(HOST, "/live/lobby", KEY)
    return raw[:-2] + extra.encode() + b"\r\n"


def cookie_for(auth: SessionAuth, users: InMemoryUsers, name: str) -> str:
    """Return a Cookie header line holding a fresh session for the user."""
    user = users.get(name)
    assert user is not None
    response = HttpResponse()
    auth.login(response, user)
    value = (response.headers.get("Set-Cookie") or "").split(";")[0]
    return f"Cookie: {value}\r\n"


async def greeted(server: AsyncServer, raw: bytes) -> tuple[Reply, bytes]:
    """Send an upgrade, read the 101 reply, then read up to the greeting."""
    reader, writer = await connect(server)
    await send(writer, raw)
    reply = await read_reply(reader)
    greeting = await reader.read_until(b"lobby")
    writer.close()
    return reply, greeting


async def status_of(server: AsyncServer, raw: bytes) -> int:
    """Send an upgrade request and return just the reply's status."""
    reader, writer = await connect(server)
    await send(writer, raw)
    reply = await read_reply(reader)
    writer.close()
    return reply.status


class TestWebSocketAuth:
    """Test that the auth middleware guards WebSocket upgrades."""

    def test_anonymous_upgrade_is_401(self) -> None:
        """Turn away an anonymous upgrade (even from a browser) before the handshake."""
        app, _, _ = ws_app()
        reply, rest = serve(app, lambda s: exchange(s, handshake("Accept: text/html\r\n")))
        assert reply.status == UNAUTHORIZED
        assert rest == b""

    def test_missing_role_upgrade_is_403(self) -> None:
        """Turn away a logged-in user without the role."""
        app, auth, users = ws_app()
        reply, _ = serve(app, lambda s: exchange(s, handshake(cookie_for(auth, users, "bob"))))
        assert reply.status == FORBIDDEN

    def test_authenticated_upgrade_succeeds(self) -> None:
        """Upgrade a cookie-authenticated admin and run the handler."""
        app, auth, users = ws_app()
        line = cookie_for(auth, users, "ada") + SAME_ORIGIN
        reply, rest = serve(app, lambda s: greeted(s, handshake(line)))
        assert reply.status == SWITCHING
        assert b"hi ada in lobby" in rest

    def test_bearer_upgrade_succeeds(self) -> None:
        """Upgrade with an API token too."""
        app, auth, users = ws_app()
        ada = users.get("ada")
        assert ada is not None
        line = f"Authorization: Bearer {auth.api_token(ada)}\r\n"
        reply, rest = serve(app, lambda s: greeted(s, handshake(line)))
        assert reply.status == SWITCHING
        assert b"hi ada in lobby" in rest


class TestWebSocketOrigin:
    """Test that a cookie upgrade must come from our own site (no hijacking).

    The phone line checks which house the call comes from: a page on
    another site (even another port on the same machine) cannot use the
    visitor's cookie to open a WebSocket in their name.
    """

    def upgrade(self, extra: str, allowed: tuple[str, ...] = ()) -> int:
        """Send Ada's cookie upgrade with extra header lines; return the status."""
        app, auth, users = ws_app(allowed)
        raw = handshake(cookie_for(auth, users, "ada") + extra)
        return serve(app, lambda s: status_of(s, raw))

    def test_same_origin_is_101(self) -> None:
        """Put through a cookie upgrade whose Origin matches the Host."""
        assert self.upgrade(SAME_ORIGIN) == SWITCHING

    def test_cross_origin_is_403(self) -> None:
        """Refuse a cookie upgrade from another port on the same machine."""
        assert self.upgrade(f"Origin: http://{HOST}:9999\r\n") == FORBIDDEN

    def test_null_origin_is_403(self) -> None:
        """Refuse a cookie upgrade from a sandboxed page (Origin: null)."""
        assert self.upgrade("Origin: null\r\n") == FORBIDDEN

    def test_missing_origin_is_403(self) -> None:
        """Refuse a cookie upgrade with no Origin (browsers always send one)."""
        assert self.upgrade("") == FORBIDDEN

    def test_allow_listed_origin_is_101(self) -> None:
        """Put through an Origin named in ``allowed_origins``."""
        app, auth, users = ws_app(("http://localhost:3000",))
        raw = handshake(cookie_for(auth, users, "ada") + "Origin: http://localhost:3000\r\n")
        reply, rest = serve(app, lambda s: greeted(s, raw))
        assert reply.status == SWITCHING
        assert b"hi ada in lobby" in rest

    def test_bearer_without_origin_is_101(self) -> None:
        """Leave bearer-token upgrades alone: a script sends no Origin."""
        app, auth, users = ws_app()
        ada = users.get("ada")
        assert ada is not None
        line = f"Authorization: Bearer {auth.api_token(ada)}\r\n"
        reply, _ = serve(app, lambda s: greeted(s, handshake(line)))
        assert reply.status == SWITCHING

    def test_cookie_on_open_route_is_checked_too(self) -> None:
        """Refuse a cross-site cookie upgrade even to a route with no require."""
        app = App()
        users = InMemoryUsers()
        ada = users.add("ada", ADA_WORD, rounds=ROUNDS)
        auth = SessionAuth(SessionSigner(DEMO_KEY), users)
        app.middleware(auth)

        @app.websocket("/live/<room>")
        async def live(ws: WebSocket, _request: HttpRequest, room: str) -> None:  # pyright: ignore[reportUnusedFunction]
            """Greet anyone."""
            await ws.send_text(f"hi in {room}")

        anonymous, _ = serve(app, lambda s: greeted(s, handshake()))
        response = HttpResponse()
        auth.login(response, ada)
        cookie = (response.headers.get("Set-Cookie") or "").split(";")[0]
        raw = handshake(f"Cookie: {cookie}\r\nOrigin: http://evil.example\r\n")
        assert anonymous.status == SWITCHING
        assert serve(app, lambda s: status_of(s, raw)) == FORBIDDEN


class SlotCallable:
    """A callable that cannot be weakly referenced."""

    __slots__ = ()

    def __call__(self) -> None:
        """Do nothing."""


class TestRequiredRoles:
    """Test how the middleware finds the roles a handler needs."""

    def test_stacked_requires_add_up(self) -> None:
        """Collect roles from every require in a stack of wrappers."""

        @require("a")
        @require("b")
        def both(_request: HttpRequest) -> None:
            """Need both."""

        assert _required_roles(both) == frozenset({"a", "b"})

    def test_unguarded_and_odd_handlers_need_nothing(self) -> None:
        """Give None for plain, unweakrefable and self-wrapping callables."""

        def plain() -> None:
            """Need nothing."""

        def loop() -> None:
            """Wrap myself."""

        loop.__wrapped__ = loop  # pyright: ignore[reportFunctionMemberAccess]
        assert _required_roles(plain) is None
        assert _required_roles(SlotCallable()) is None
        assert _required_roles(loop) is None

    def test_unguarded_upgrade_passes_through(self) -> None:
        """Hand back the upgrade marker for a WebSocket route with no require."""
        app = App()
        app.middleware(SessionAuth(SessionSigner(DEMO_KEY), InMemoryUsers()))

        @app.websocket("/open")
        async def open_room(_ws: WebSocket, _request: HttpRequest) -> None:  # pyright: ignore[reportUnusedFunction]
            """Let anyone in."""

        headers = Headers([("Upgrade", "websocket"), ("Connection", "Upgrade")])
        outcome = go(app.handle(HttpRequest("GET", "/open", QueryParams(), headers)))
        assert isinstance(outcome, WebSocketUpgrade)
