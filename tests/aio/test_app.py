"""Tests for the App: handlers, coercion, errors, middleware and hooks."""

import json as json_lib
import logging
import threading
from collections.abc import Coroutine
from typing import Any

import pytest
from pynet.loop import run, sleep, spawn
from pynet.loop.testing import FakeClock

from pyweb.app import App, CallNext
from pyweb.messages import (
    Headers,
    HTTPError,
    HttpRequest,
    HttpResponse,
    Outcome,
    QueryParams,
    StreamingResponse,
    json,
    text,
)

STATUS_OK = 200
STATUS_NOT_FOUND = 404
STATUS_METHOD_NOT_ALLOWED = 405
STATUS_CONFLICT = 409
STATUS_ERROR = 500
USER_ID = 42
WAIT_SECONDS = 2.0
POLL_SECONDS = 0.005


def make_request(
    method: str = "GET", path: str = "/", headers: list[tuple[str, str]] | None = None
) -> HttpRequest:
    """Build a bare request for the App."""
    return HttpRequest(method, path, QueryParams(), Headers(headers or []))


def go[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on a fresh loop with an auto-jumping fake clock."""
    return run(coro, clock=FakeClock(autojump=True))


def as_response(outcome: Outcome) -> HttpResponse:
    """Narrow an outcome to a plain response."""
    assert isinstance(outcome, HttpResponse)
    return outcome


async def fetch(app: App, method: str = "GET", path: str = "/") -> HttpResponse:
    """Send one request through the app and return the plain response."""
    return as_response(await app.handle(make_request(method, path)))


class TestHandlers:
    """Test the three kinds of handler."""

    def test_async_handler(self) -> None:
        """Await an async def handler."""
        app = App()

        @app.get("/")
        async def home(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Answer hello."""
            await sleep(0)
            return text("hi")

        response = go(fetch(app))
        assert response.status == STATUS_OK
        assert response.body == b"hi"

    def test_sync_handler_runs_on_loop_thread(self) -> None:
        """Call a plain def handler inline on the loop thread."""
        app = App()
        seen: list[threading.Thread] = []

        @app.get("/")
        def home(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Record the thread."""
            seen.append(threading.current_thread())
            return text("sync")

        assert go(fetch(app)).body == b"sync"
        assert seen == [threading.current_thread()]

    def test_blocking_handler_runs_off_loop_and_others_continue(self) -> None:
        """Run a blocking handler on a helper thread while others are served."""
        app = App()
        release = threading.Event()
        seen: list[threading.Thread] = []

        @app.get("/slow", blocking=True)
        def slow(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Wait for the test to release us."""
            seen.append(threading.current_thread())
            release.wait(WAIT_SECONDS)
            return text("slow")

        @app.get("/fast")
        async def fast(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Answer at once."""
            return text("fast")

        async def main() -> tuple[bytes, bytes]:
            """Start the slow request, serve the fast one, then release."""
            slow_task = spawn(fetch(app, path="/slow"))
            while not seen:
                await sleep(POLL_SECONDS)
            fast_response = await fetch(app, path="/fast")
            finished_early = slow_task.done()
            release.set()
            slow_response = await slow_task
            assert not finished_early
            return fast_response.body, slow_response.body

        assert run(main()) == (b"fast", b"slow")
        assert seen
        assert seen[0] is not threading.current_thread()

    def test_blocking_async_handler_is_rejected(self) -> None:
        """Refuse blocking=True on an async def handler."""
        app = App()

        async def handler(_request: HttpRequest) -> HttpResponse:
            """Do nothing."""
            return text("")

        with pytest.raises(ValueError, match="blocking"):
            app.get("/", blocking=True)(handler)

    def test_unknown_method_is_rejected(self) -> None:
        """Refuse a method name that HTTP does not know."""
        app = App()
        with pytest.raises(ValueError, match="method"):
            app.route("FETCH", "/")

    def test_int_param_passed_as_kwarg(self) -> None:
        """Pass converted path parameters as keyword arguments."""
        app = App()

        @app.get("/users/<int:user_id>")
        async def user(request: HttpRequest, user_id: int) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
            """Echo the id and its type."""
            assert request.path_params == {"user_id": user_id}
            return {"id": user_id, "type": type(user_id).__name__}

        response = go(fetch(app, path=f"/users/{USER_ID}"))
        assert json_lib.loads(response.body) == {"id": USER_ID, "type": "int"}

    def test_shorthands_register_their_methods(self) -> None:
        """Register post, put, patch and delete with their method."""
        app = App()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            register = getattr(app, method.lower())

            def handler(_request: HttpRequest, name: str = method) -> str:
                """Answer with the method name."""
                return name

            register("/thing")(handler)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert go(fetch(app, method, "/thing")).body == method.encode()


class TestCoercion:
    """Test turning handler results into responses."""

    def test_dict_and_list_become_json(self) -> None:
        """Encode dicts and lists as JSON."""
        app = App()

        @app.get("/d")
        def as_dict(_request: HttpRequest) -> dict[str, int]:  # pyright: ignore[reportUnusedFunction]
            """Return a dict."""
            return {"a": 1}

        @app.get("/l")
        def as_list(_request: HttpRequest) -> list[int]:  # pyright: ignore[reportUnusedFunction]
            """Return a list."""
            return [1, 2]

        d = go(fetch(app, path="/d"))
        assert d.headers.get("Content-Type") == "application/json"
        assert json_lib.loads(d.body) == {"a": 1}
        assert json_lib.loads(go(fetch(app, path="/l")).body) == [1, 2]

    def test_str_becomes_html(self) -> None:
        """Send a str as HTML."""
        app = App()

        @app.get("/")
        def page(_request: HttpRequest) -> str:  # pyright: ignore[reportUnusedFunction]
            """Return markup."""
            return "<h1>Hi</h1>"

        response = go(fetch(app))
        assert response.headers.get("Content-Type") == "text/html; charset=utf-8"
        assert response.body == b"<h1>Hi</h1>"

    def test_bad_return_type_is_logged_500(self, caplog: pytest.LogCaptureFixture) -> None:
        """Answer 500 and log when a handler returns something odd."""
        app = App()

        @app.get("/")
        def odd(_request: HttpRequest) -> int:  # pyright: ignore[reportUnusedFunction]
            """Return a number, which is not allowed."""
            return USER_ID

        with caplog.at_level(logging.ERROR, logger="pyweb.app"):
            response = go(fetch(app))
        assert response.status == STATUS_ERROR
        assert any("int" in r.getMessage() for r in caplog.records)

    def test_streaming_response_passes_through(self) -> None:
        """Return a streaming response unchanged."""
        app = App()

        class Stream:
            """A minimal streaming response."""

            def __init__(self) -> None:
                """Set status and headers."""
                self.status = STATUS_OK
                self.headers = Headers([("Content-Type", "text/event-stream")])

        stream = Stream()

        @app.get("/events")
        def events(_request: HttpRequest) -> Stream:  # pyright: ignore[reportUnusedFunction]
            """Return the stream."""
            return stream

        outcome = go(app.handle(make_request(path="/events")))
        assert isinstance(outcome, StreamingResponse)
        assert outcome is stream


class TestErrors:
    """Test turning errors into responses."""

    def test_http_error_sets_status(self) -> None:
        """Answer an HTTPError with its status."""
        app = App()

        @app.post("/things")
        def create(_request: HttpRequest) -> str:  # pyright: ignore[reportUnusedFunction]
            """Refuse a duplicate."""
            raise HTTPError(STATUS_CONFLICT, "already exists")

        response = go(fetch(app, "POST", "/things"))
        assert response.status == STATUS_CONFLICT
        assert json_lib.loads(response.body)["message"] == "already exists"

    def test_unhandled_exception_is_generic_500(self, caplog: pytest.LogCaptureFixture) -> None:
        """Hide the traceback from the client but log it."""
        app = App()

        @app.get("/")
        def broken(_request: HttpRequest) -> str:  # pyright: ignore[reportUnusedFunction]
            """Fail badly."""
            msg = "secret detail"
            raise RuntimeError(msg)

        with caplog.at_level(logging.ERROR, logger="pyweb.app"):
            response = go(fetch(app))
        assert response.status == STATUS_ERROR
        assert json_lib.loads(response.body) == {"error": "Internal Server Error"}
        assert b"secret" not in response.body
        assert b"Traceback" not in response.body
        records = [r for r in caplog.records if r.name == "pyweb.app"]
        assert records
        assert records[0].exc_info is not None

    def test_unhandled_exception_html_when_wanted(self) -> None:
        """Answer a browser with a small HTML 500 page."""
        app = App()

        @app.get("/")
        def broken(_request: HttpRequest) -> str:  # pyright: ignore[reportUnusedFunction]
            """Fail badly."""
            raise RuntimeError

        request = make_request(headers=[("Accept", "text/html")])
        response = as_response(go(app.handle(request)))
        assert response.status == STATUS_ERROR
        assert response.headers.get("Content-Type") == "text/html; charset=utf-8"
        assert b"500" in response.body

    def test_not_found(self) -> None:
        """Answer 404 when no route matches."""
        assert go(fetch(App(), path="/nope")).status == STATUS_NOT_FOUND

    def test_method_not_allowed_has_allow_header(self) -> None:
        """Answer 405 with an Allow header listing the methods."""
        app = App()

        @app.get("/only-get")
        def page(_request: HttpRequest) -> str:  # pyright: ignore[reportUnusedFunction]
            """Answer."""
            return "ok"

        response = go(fetch(app, "POST", "/only-get"))
        assert response.status == STATUS_METHOD_NOT_ALLOWED
        assert response.headers.get("Allow") == "GET, HEAD"

    def test_websocket_is_never_in_allow(self) -> None:
        """Leave the WEBSOCKET pseudo-method out of the Allow header."""
        app = App()

        @app.get("/chat")
        def page(_request: HttpRequest) -> str:  # pyright: ignore[reportUnusedFunction]
            """Answer."""
            return "ok"

        @app.websocket("/chat")
        async def chat(socket: object) -> None:  # pyright: ignore[reportUnusedFunction]
            """Do nothing."""

        response = go(fetch(app, "DELETE", "/chat"))
        assert response.status == STATUS_METHOD_NOT_ALLOWED
        assert response.headers.get("Allow") == "GET, HEAD"

    def test_websocket_only_path_is_404_for_plain_requests(self) -> None:
        """Answer 404 when only a WebSocket route matches a plain request."""
        app = App()

        @app.websocket("/chat")
        async def chat(socket: object) -> None:  # pyright: ignore[reportUnusedFunction]
            """Do nothing."""

        assert go(fetch(app, "GET", "/chat")).status == STATUS_NOT_FOUND
        assert go(fetch(app, "WEBSOCKET", "/chat")).status == STATUS_NOT_FOUND

    def test_websocket_route_lookup(self) -> None:
        """Find a WebSocket handler and its params by path."""
        app = App()

        @app.websocket("/rooms/<int:room>")
        async def room(socket: object, room: int) -> None:
            """Do nothing."""

        found = app.websocket_route(f"/rooms/{USER_ID}")
        assert found is not None
        assert found[0] is room
        assert found[1] == {"room": USER_ID}
        assert app.websocket_route("/elsewhere") is None


class TestHead:
    """Test HEAD requests."""

    def test_head_has_get_headers_and_no_body(self) -> None:
        """Answer HEAD with the GET headers but an empty body."""
        app = App()

        @app.get("/")
        def page(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Answer with a custom header."""
            response = text("hello")
            response.headers.set("X-Kind", "greeting")
            return response

        get = go(fetch(app, "GET"))
        head = go(fetch(app, "HEAD"))
        assert head.status == STATUS_OK
        assert head.body == b""
        assert head.headers.items() == get.headers.items()


class TestMiddleware:
    """Test the middleware chain."""

    def test_order_first_registered_is_outermost(self) -> None:
        """Run middleware in registration order, unwinding in reverse."""
        app = App()
        calls: list[str] = []

        @app.middleware
        async def outer(request: HttpRequest, call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Record entry and exit."""
            calls.append("outer in")
            outcome = await call_next(request)
            calls.append("outer out")
            return outcome

        @app.middleware
        async def inner(request: HttpRequest, call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Record entry and exit."""
            calls.append("inner in")
            outcome = await call_next(request)
            calls.append("inner out")
            return outcome

        @app.get("/")
        def page(_request: HttpRequest) -> str:  # pyright: ignore[reportUnusedFunction]
            """Record the handler call."""
            calls.append("handler")
            return "ok"

        go(fetch(app))
        assert calls == ["outer in", "inner in", "handler", "inner out", "outer out"]

    def test_short_circuit_skips_handler(self) -> None:
        """Let middleware answer without calling the rest of the chain."""
        app = App()
        calls: list[str] = []

        @app.middleware
        async def guard(_request: HttpRequest, _call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Refuse everything."""
            return text("stopped", STATUS_CONFLICT)

        @app.get("/")
        def page(_request: HttpRequest) -> str:  # pyright: ignore[reportUnusedFunction]
            """Record the call."""
            calls.append("handler")
            return "ok"

        response = go(fetch(app))
        assert response.status == STATUS_CONFLICT
        assert calls == []

    def test_middleware_sees_error_responses(self) -> None:
        """Hand middleware the 404 response rather than an exception."""
        app = App()
        statuses: list[int] = []

        @app.middleware
        async def log_status(request: HttpRequest, call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Record the status."""
            outcome = await call_next(request)
            statuses.append(outcome.status)
            return outcome

        go(fetch(app, path="/missing"))
        assert statuses == [STATUS_NOT_FOUND]

    def test_middleware_errors_become_responses(self) -> None:
        """Turn exceptions raised by middleware into responses."""
        app = App()

        @app.middleware
        async def deny(_request: HttpRequest, _call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Raise an HTTP error."""
            raise HTTPError(STATUS_CONFLICT)

        @app.middleware
        async def crash(_request: HttpRequest, _call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Never reached."""
            raise RuntimeError

        assert go(fetch(app)).status == STATUS_CONFLICT

    def test_middleware_crash_is_logged_500(self, caplog: pytest.LogCaptureFixture) -> None:
        """Turn an unexpected middleware exception into a logged, generic 500."""
        app = App()

        @app.middleware
        async def crash(_request: HttpRequest, _call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Fail badly."""
            raise RuntimeError

        with caplog.at_level(logging.ERROR, logger="pyweb.app"):
            response = go(fetch(app))
        assert response.status == STATUS_ERROR
        assert json_lib.loads(response.body) == {"error": "Internal Server Error"}
        assert any(r.exc_info for r in caplog.records if r.name == "pyweb.app")

    def test_middleware_can_add_headers(self) -> None:
        """Let middleware change the response on the way out."""
        app = App()

        @app.middleware
        async def stamp(request: HttpRequest, call_next: CallNext) -> Outcome:  # pyright: ignore[reportUnusedFunction]
            """Add a header."""
            outcome = await call_next(request)
            outcome.headers.set("X-Stamp", "yes")
            return outcome

        @app.get("/")
        def page(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Answer."""
            return json({"ok": True})

        assert go(fetch(app)).headers.get("X-Stamp") == "yes"


class TestLifecycle:
    """Test startup and shutdown hooks."""

    def test_hook_order(self) -> None:
        """Run startup hooks in order and shutdown hooks in reverse."""
        app = App()
        calls: list[str] = []

        @app.on_startup
        async def open_db() -> None:  # pyright: ignore[reportUnusedFunction]
            """Record."""
            calls.append("start db")

        @app.on_startup
        def warm_cache() -> None:  # pyright: ignore[reportUnusedFunction]
            """Record."""
            calls.append("start cache")

        @app.on_shutdown
        def close_db() -> None:  # pyright: ignore[reportUnusedFunction]
            """Record."""
            calls.append("stop db")

        @app.on_shutdown
        async def flush_cache() -> None:  # pyright: ignore[reportUnusedFunction]
            """Record."""
            calls.append("stop cache")

        async def main() -> None:
            """Start then stop."""
            await app.startup()
            await app.shutdown()

        go(main())
        assert calls == ["start db", "start cache", "stop cache", "stop db"]

    def test_failing_shutdown_hook_does_not_stop_others(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log a failing shutdown hook and still run the rest."""
        app = App()
        calls: list[str] = []

        @app.on_shutdown
        def first() -> None:  # pyright: ignore[reportUnusedFunction]
            """Record."""
            calls.append("first")

        @app.on_shutdown
        def second() -> None:  # pyright: ignore[reportUnusedFunction]
            """Fail."""
            raise RuntimeError

        with caplog.at_level(logging.ERROR, logger="pyweb.app"):
            go(app.shutdown())
        assert calls == ["first"]
        assert any(r.name == "pyweb.app" for r in caplog.records)
