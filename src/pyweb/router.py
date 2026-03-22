"""URL router -- the mailroom that sorts requests.

The router matches a request's path and method to the right handler
function. It's like a mailroom: each letter (request) gets sorted
into the right bin (handler) based on its address (path).
"""

from collections.abc import Callable
from dataclasses import dataclass

from pyweb.request import Request
from pyweb.response import Response, not_found

# A handler is a function that takes a Request and returns a Response.
type Handler = Callable[[Request], Response]


@dataclass
class Route:
    """A single route mapping.

    Args:
        method: The HTTP method (GET, POST, etc.).
        path: The URL path pattern.
        handler: The function to call.

    """

    method: str
    path: str
    handler: Handler


class Router:
    """Match requests to handler functions.

    Register routes with ``add_route()`` or the ``get()``/``post()``
    shortcuts, then call ``dispatch()`` to find and run the right handler.

    """

    def __init__(self) -> None:
        """Create an empty router."""
        self._routes: list[Route] = []

    @property
    def routes(self) -> list[Route]:
        """Return all registered routes."""
        return list(self._routes)

    def add_route(self, method: str, path: str, handler: Handler) -> None:
        """Register a route.

        Args:
            method: The HTTP method.
            path: The URL path.
            handler: The handler function.

        """
        self._routes.append(Route(method=method.upper(), path=path, handler=handler))

    def get(self, path: str) -> Callable[[Handler], Handler]:
        """Register a GET route.

        Usage::

            @router.get("/about")
            def about(request):
                return html_response("<h1>About</h1>")

        """

        def decorator(handler: Handler) -> Handler:
            self.add_route("GET", path, handler)
            return handler

        return decorator

    def post(self, path: str) -> Callable[[Handler], Handler]:
        """Register a POST route.

        Usage::

            @router.post("/api/data")
            def create(request):
                return json_response('{"ok": true}')

        """

        def decorator(handler: Handler) -> Handler:
            self.add_route("POST", path, handler)
            return handler

        return decorator

    def dispatch(self, request: Request) -> Response:
        """Find and call the handler for a request.

        Args:
            request: The incoming HTTP request.

        Returns:
            The handler's response, or a 404 if no route matches.

        """
        for route in self._routes:
            if route.method == request.method and route.path == request.path:
                return route.handler(request)

        return not_found(f"No route for {request.method} {request.path}")
