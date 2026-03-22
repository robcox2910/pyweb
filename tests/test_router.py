"""Tests for the URL router.

The router is the mailroom -- it sorts incoming requests to the right
handler based on the method and path.
"""

from pyweb.request import Request
from pyweb.response import Response, text_response
from pyweb.router import Router

STATUS_200 = 200
STATUS_404 = 404


def _make_request(method: str = "GET", path: str = "/") -> Request:
    """Create a test request."""
    return Request(method=method, path=path)


class TestRouterRegistration:
    """Verify route registration."""

    def test_add_route(self) -> None:
        """Adding a route should register it."""
        router = Router()
        router.add_route("GET", "/", lambda _r: text_response("ok"))
        assert len(router.routes) == 1

    def test_get_decorator(self) -> None:
        """The @get decorator should register a GET route."""
        router = Router()

        @router.get("/about")
        def about(_request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
            """Serve the about page."""
            return text_response("about")

        assert len(router.routes) == 1
        assert router.routes[0].method == "GET"
        assert router.routes[0].path == "/about"

    def test_post_decorator(self) -> None:
        """The @post decorator should register a POST route."""
        router = Router()

        @router.post("/api")
        def create(_request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
            """Handle a POST."""
            return text_response("created")

        assert router.routes[0].method == "POST"


class TestRouterDispatch:
    """Verify request dispatching."""

    def test_dispatch_matching_route(self) -> None:
        """A matching route should return its handler's response."""
        router = Router()
        router.add_route("GET", "/", lambda _r: text_response("home"))
        resp = router.dispatch(_make_request("GET", "/"))
        assert resp.body == "home"
        assert resp.status == STATUS_200

    def test_dispatch_no_match(self) -> None:
        """A non-matching request should return 404."""
        router = Router()
        resp = router.dispatch(_make_request("GET", "/missing"))
        assert resp.status == STATUS_404

    def test_dispatch_wrong_method(self) -> None:
        """A matching path but wrong method should return 404."""
        router = Router()
        router.add_route("POST", "/api", lambda _r: text_response("ok"))
        resp = router.dispatch(_make_request("GET", "/api"))
        assert resp.status == STATUS_404

    def test_multiple_routes(self) -> None:
        """The router should match the correct route from many."""
        router = Router()
        router.add_route("GET", "/", lambda _r: text_response("home"))
        router.add_route("GET", "/about", lambda _r: text_response("about"))
        router.add_route("POST", "/api", lambda _r: text_response("api"))

        assert router.dispatch(_make_request("GET", "/about")).body == "about"
        assert router.dispatch(_make_request("POST", "/api")).body == "api"
