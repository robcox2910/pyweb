"""The App: routes, middleware and lifecycle hooks on PyNet's event loop.

Think of a restaurant. The *handlers* are the cooks, each one making a
single dish (a page). The *route table* is the menu that says which cook
makes which dish. *Middleware* are the people a ticket passes on its way
to the kitchen and back -- the host who checks your booking, the waiter
who adds a mint to the plate. The ``App`` is the restaurant manager that
hires everyone and makes sure every order gets an answer, even when a
cook drops a plate (a ``500``).

Handlers come in three kinds:

* ``async def`` -- awaited on the loop; the friendliest kind.
* plain ``def`` -- called right there on the loop. While it runs, nobody
  else is served, so keep it quick.
* plain ``def`` with ``blocking=True`` -- sent to a helper thread with
  ``pynet.loop.run_in_thread`` so the loop keeps serving others.
"""

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, cast

from pynet.loop import run_in_thread

from pyweb.http1 import KNOWN_METHODS
from pyweb.messages import (
    HTTPError,
    HttpRequest,
    HttpResponse,
    JsonValue,
    Outcome,
    StreamingResponse,
    html,
    json,
)
from pyweb.routing import WEBSOCKET, RouteTable

__all__ = ["App", "CallNext", "Handler", "Hook", "Middleware"]

logger = logging.getLogger("pyweb.app")

type CallNext = Callable[[HttpRequest], Awaitable[Outcome]]
"""Call the rest of the chain (later middleware, then the handler)."""

type Middleware = Callable[[HttpRequest, CallNext], Awaitable[Outcome]]
"""Wrap every request: look at it, call ``call_next``, look at the answer."""

type Handler = Callable[..., object]
"""A route handler: called with the request plus path parameters."""

type Hook = Callable[[], object]
"""A startup or shutdown hook: a plain or async function with no arguments."""

_NOT_FOUND = 404
_METHOD_NOT_ALLOWED = 405
_SERVER_ERROR = 500
_ERROR_PAGE = (
    "<!doctype html><title>500 Internal Server Error</title><h1>500 Internal Server Error</h1>"
)


class _Decorator(Protocol):
    """A decorator that registers a function and gives it back unchanged."""

    def __call__[F: Handler](self, fn: F) -> F:
        """Register ``fn`` and return it."""
        ...


@dataclass(frozen=True)
class _Route:
    """A registered handler and how to call it."""

    fn: Handler
    is_async: bool
    blocking: bool


def _server_error(request: HttpRequest) -> HttpResponse:
    """Build the generic 500 answer, with no details for the client."""
    if request.wants_html:
        return html(_ERROR_PAGE, _SERVER_ERROR)
    return json({"error": "Internal Server Error"}, _SERVER_ERROR)


def _coerce(result: object, request: HttpRequest) -> Outcome:
    """Turn a handler's return value into a response."""
    if isinstance(result, HttpResponse | StreamingResponse):
        return result
    if isinstance(result, dict | list):
        return json(cast("JsonValue", result))
    if isinstance(result, str):
        return html(result)
    logger.error(
        "Handler for %s %s returned unsupported type %s",
        request.method,
        request.path,
        type(result).__name__,
    )
    return _server_error(request)


def _link(middleware: Middleware, call_next: CallNext) -> CallNext:
    """Wrap one middleware around the rest of the chain."""

    async def call(request: HttpRequest) -> Outcome:
        """Run this middleware with the rest of the chain as ``call_next``."""
        return await middleware(request, call_next)

    return call


async def _run_hook(hook: Hook) -> None:
    """Call a hook and await it if it is async."""
    result = hook()
    if inspect.isawaitable(result):
        await result


class App:
    """A web application: routes, middleware and lifecycle hooks."""

    def __init__(self) -> None:
        """Create an app with no routes, middleware or hooks."""
        self._routes: RouteTable[_Route] = RouteTable()
        self._middleware: list[Middleware] = []
        self._startup: list[Hook] = []
        self._shutdown: list[Hook] = []

    # -- registering -------------------------------------------------------

    def route(self, method: str, pattern: str, *, blocking: bool = False) -> _Decorator:
        """Return a decorator that registers a handler for a method and pattern.

        Args:
            method: An HTTP method such as ``"GET"``.
            pattern: A path pattern such as ``"/users/<int:id>"``.
            blocking: Run a plain ``def`` handler on a helper thread.

        Raises:
            ValueError: If the method is unknown, or ``blocking=True`` is
                used with an ``async def`` handler (it would gain nothing).

        """
        if method not in KNOWN_METHODS:
            msg = f"unknown HTTP method {method!r}"
            raise ValueError(msg)

        def register[F: Handler](fn: F) -> F:
            """Store the handler in the route table."""
            is_async = inspect.iscoroutinefunction(fn)
            if blocking and is_async:
                msg = f"blocking=True needs a plain def handler, not async def {fn!r}"
                raise ValueError(msg)
            self._routes.add(method, pattern, _Route(fn, is_async, blocking))
            return fn

        return register

    def get(self, pattern: str, *, blocking: bool = False) -> _Decorator:
        """Register a GET handler (HEAD is answered by it too)."""
        return self.route("GET", pattern, blocking=blocking)

    def post(self, pattern: str, *, blocking: bool = False) -> _Decorator:
        """Register a POST handler."""
        return self.route("POST", pattern, blocking=blocking)

    def put(self, pattern: str, *, blocking: bool = False) -> _Decorator:
        """Register a PUT handler."""
        return self.route("PUT", pattern, blocking=blocking)

    def patch(self, pattern: str, *, blocking: bool = False) -> _Decorator:
        """Register a PATCH handler."""
        return self.route("PATCH", pattern, blocking=blocking)

    def delete(self, pattern: str, *, blocking: bool = False) -> _Decorator:
        """Register a DELETE handler."""
        return self.route("DELETE", pattern, blocking=blocking)

    def websocket(self, pattern: str) -> _Decorator:
        """Register a WebSocket handler for a pattern."""

        def register[F: Handler](fn: F) -> F:
            """Store the handler under the WEBSOCKET pseudo-method."""
            route = _Route(fn, inspect.iscoroutinefunction(fn), blocking=False)
            self._routes.add(WEBSOCKET, pattern, route)
            return fn

        return register

    def middleware(self, fn: Middleware) -> Middleware:
        """Register middleware; the first registered runs outermost."""
        self._middleware.append(fn)
        return fn

    def on_startup[H: Hook](self, fn: H) -> H:
        """Register a hook to run, in order, when the server starts."""
        self._startup.append(fn)
        return fn

    def on_shutdown[H: Hook](self, fn: H) -> H:
        """Register a hook to run, in reverse order, when the server stops."""
        self._shutdown.append(fn)
        return fn

    # -- lifecycle ---------------------------------------------------------

    async def startup(self) -> None:
        """Run every startup hook in registration order."""
        for hook in self._startup:
            await _run_hook(hook)

    async def shutdown(self) -> None:
        """Run every shutdown hook in reverse order, logging any that fail."""
        for hook in reversed(self._shutdown):
            try:
                await _run_hook(hook)
            except Exception:
                logger.exception("Shutdown hook %r failed", hook)

    # -- handling ----------------------------------------------------------

    def websocket_route(self, path: str) -> tuple[Handler, dict[str, object]] | None:
        """Return the WebSocket handler and params for a path, or ``None``."""
        found = self._routes.match(WEBSOCKET, path)
        if found.value is None:
            return None
        return found.value.fn, found.params

    async def handle(self, request: HttpRequest) -> Outcome:
        """Answer one request: middleware, routing, the handler, then coercion.

        Never raises for an ordinary ``Exception``: an ``HTTPError`` becomes
        its own response and anything else becomes a logged, generic 500.
        """
        call_next: CallNext = self._dispatch
        for middleware in reversed(self._middleware):
            call_next = _link(middleware, call_next)
        try:
            outcome = await call_next(request)
        except HTTPError as exc:
            outcome = exc.to_response(request)
        except Exception:
            logger.exception("Unhandled error in %s %s", request.method, request.path)
            outcome = _server_error(request)
        if request.method == "HEAD" and isinstance(outcome, HttpResponse):
            outcome.body = b""
        return outcome

    async def _dispatch(self, request: HttpRequest) -> Outcome:
        """Find the route and call its handler, turning errors into responses."""
        try:
            route, params = self._find(request)
            request.path_params = params
            return _coerce(await self._call(route, request, params), request)
        except HTTPError as exc:
            return exc.to_response(request)
        except Exception:
            logger.exception("Unhandled error in %s %s", request.method, request.path)
            return _server_error(request)

    def _find(self, request: HttpRequest) -> tuple[_Route, dict[str, object]]:
        """Match the request to a route, or raise 404/405."""
        if request.method == WEBSOCKET:
            raise HTTPError(_NOT_FOUND)
        found = self._routes.match(request.method, request.path)
        if found.value is not None:
            return found.value, found.params
        allowed = sorted(found.allowed - {WEBSOCKET})
        if not allowed:
            raise HTTPError(_NOT_FOUND)
        raise _MethodNotAllowedError(allowed)

    @staticmethod
    async def _call(route: _Route, request: HttpRequest, params: dict[str, object]) -> object:
        """Call a handler the right way for its kind."""
        if route.is_async:
            return await cast("Awaitable[object]", route.fn(request, **params))
        if route.blocking:
            return await run_in_thread(route.fn, request, **params)
        return route.fn(request, **params)


class _MethodNotAllowedError(HTTPError):
    """A 405 that remembers which methods would have worked."""

    def __init__(self, allowed: list[str]) -> None:
        """Store the allowed methods."""
        super().__init__(_METHOD_NOT_ALLOWED)
        self.allowed = allowed

    def to_response(self, request: HttpRequest) -> HttpResponse:
        """Render the 405 with an ``Allow`` header."""
        response = super().to_response(request)
        response.headers.set("Allow", ", ".join(self.allowed))
        return response
