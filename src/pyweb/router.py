"""URL router -- the mailroom that sorts requests.

The router matches a request's path and method to the right handler
function. It's like a mailroom: each letter (request) gets sorted
into the right bin (handler) based on its address (path).

If no bin matches the address, the letter is returned with a "not found"
stamp. If the address is right but the request type is wrong (like
trying to deliver a package through the letter slot), it gets a
"method not allowed" stamp.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from pyweb.request import Request
from pyweb.response import Response, method_not_allowed, not_found

# A handler is a function that takes a Request and returns a Response.
type Handler = Callable[[Request], Response]

# Pattern for path parameters like <id> or <name>.
_PARAM_PATTERN = re.compile(r"<(\w+)>")


@dataclass
class Route:
    """A single route mapping.

    Args:
        method: The HTTP method (GET, POST, etc.).
        path: The URL path pattern (may contain <params>).
        handler: The function to call.

    """

    method: str
    path: str
    handler: Handler
    pattern: re.Pattern[str] | None = None
    param_names: list[str] | None = None


def _compile_path(path: str) -> tuple[re.Pattern[str], list[str]]:
    """Compile a path pattern with <params> into a regex.

    Args:
        path: The path pattern (e.g., "/users/<id>").

    Returns:
        A tuple of (compiled regex, list of parameter names).

    """
    param_names: list[str] = []
    regex_parts: list[str] = []
    last_end = 0

    for match in _PARAM_PATTERN.finditer(path):
        regex_parts.append(re.escape(path[last_end : match.start()]))
        regex_parts.append(r"([^/]+)")
        param_names.append(match.group(1))
        last_end = match.end()

    regex_parts.append(re.escape(path[last_end:]))
    pattern = re.compile("^" + "".join(regex_parts) + "$")
    return pattern, param_names


class Router:
    """Match requests to handler functions.

    Register routes with ``add_route()`` or the ``get()``/``post()``
    decorators, then call ``dispatch()`` to find and run the right handler.

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
            path: The URL path (may contain <param> segments).
            handler: The handler function.

        """
        route = Route(method=method.upper(), path=path, handler=handler)
        if "<" in path:
            route.pattern, route.param_names = _compile_path(path)
        self._routes.append(route)

    def get(self, path: str) -> Callable[[Handler], Handler]:
        """Register a GET route via decorator."""

        def decorator(handler: Handler) -> Handler:
            self.add_route("GET", path, handler)
            return handler

        return decorator

    def post(self, path: str) -> Callable[[Handler], Handler]:
        """Register a POST route via decorator."""

        def decorator(handler: Handler) -> Handler:
            self.add_route("POST", path, handler)
            return handler

        return decorator

    def put(self, path: str) -> Callable[[Handler], Handler]:
        """Register a PUT route via decorator."""

        def decorator(handler: Handler) -> Handler:
            self.add_route("PUT", path, handler)
            return handler

        return decorator

    def delete(self, path: str) -> Callable[[Handler], Handler]:
        """Register a DELETE route via decorator."""

        def decorator(handler: Handler) -> Handler:
            self.add_route("DELETE", path, handler)
            return handler

        return decorator

    def dispatch(self, request: Request) -> Response:
        """Find and call the handler for a request.

        Args:
            request: The incoming HTTP request.

        Returns:
            The handler's response, 404 if no path matches, or
            405 if the path matches but the method doesn't.

        """
        path_matched = False
        allowed_methods: list[str] = []

        for route in self._routes:
            # Check for path parameter routes.
            if route.pattern is not None and route.param_names is not None:
                match = route.pattern.match(request.path)
                if match:
                    path_matched = True
                    if route.method == request.method:
                        params = dict(zip(route.param_names, match.groups(), strict=True))
                        # Create a new request with the matched params.
                        req_with_params = Request(
                            method=request.method,
                            path=request.path,
                            headers=request.headers,
                            body=request.body,
                            query_params=request.query_params,
                            params=params,
                        )
                        return route.handler(req_with_params)
                    allowed_methods.append(route.method)
                continue

            # Exact path match.
            if route.path == request.path:
                path_matched = True
                if route.method == request.method:
                    return route.handler(request)
                allowed_methods.append(route.method)

        if path_matched:
            return method_not_allowed(allowed_methods)

        return not_found(f"No route for {request.method} {request.path}")
