"""Tests for the URL router.

The router is the mailroom -- it sorts incoming requests to the right
handler based on the method and path.
"""

from pathlib import Path

from pyweb.request import Request
from pyweb.response import Response, text_response
from pyweb.router import Router
from pyweb.static import serve_static

STATUS_200 = 200
STATUS_404 = 404
STATUS_405 = 405


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
            """Serve about page."""
            return text_response("about")

        assert len(router.routes) == 1
        assert router.routes[0].method == "GET"

    def test_post_decorator(self) -> None:
        """The @post decorator should register a POST route."""
        router = Router()

        @router.post("/api")
        def create(_request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
            """Handle POST."""
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

    def test_dispatch_wrong_method_returns_405(self) -> None:
        """A matching path but wrong method should return 405."""
        router = Router()
        router.add_route("POST", "/api", lambda _r: text_response("ok"))
        resp = router.dispatch(_make_request("GET", "/api"))
        assert resp.status == STATUS_405

    def test_multiple_routes(self) -> None:
        """The router should match the correct route from many."""
        router = Router()
        router.add_route("GET", "/", lambda _r: text_response("home"))
        router.add_route("GET", "/about", lambda _r: text_response("about"))
        router.add_route("POST", "/api", lambda _r: text_response("api"))

        assert router.dispatch(_make_request("GET", "/about")).body == "about"
        assert router.dispatch(_make_request("POST", "/api")).body == "api"


class TestPathParameters:
    """Verify dynamic path segments."""

    def test_single_param(self) -> None:
        """A path with one parameter should match and extract it."""
        router = Router()
        router.add_route("GET", "/users/<id>", lambda r: text_response(r.params["id"]))
        resp = router.dispatch(_make_request("GET", "/users/42"))
        assert resp.body == "42"

    def test_multiple_params(self) -> None:
        """A path with multiple parameters should extract all."""
        router = Router()
        router.add_route(
            "GET",
            "/users/<user_id>/posts/<post_id>",
            lambda r: text_response(f"{r.params['user_id']}-{r.params['post_id']}"),
        )
        resp = router.dispatch(_make_request("GET", "/users/5/posts/10"))
        assert resp.body == "5-10"

    def test_param_no_match(self) -> None:
        """A parameterized route should not match the wrong structure."""
        router = Router()
        router.add_route("GET", "/users/<id>", lambda _r: text_response("ok"))
        resp = router.dispatch(_make_request("GET", "/posts/42"))
        assert resp.status == STATUS_404


class TestWildcardRoutes:
    """Verify trailing-``*`` wildcard routes and static mounting."""

    def test_wildcard_matches_subpath(self) -> None:
        """A trailing * should match sub-paths including slashes."""
        router = Router()
        router.add_route("GET", "/files/*", lambda r: text_response(r.params["wildcard"]))
        resp = router.dispatch(_make_request("GET", "/files/docs/readme.txt"))
        assert resp.status == STATUS_200
        assert resp.body == "docs/readme.txt"

    def test_serve_static_via_router(self, tmp_path: Path) -> None:
        """End-to-end: serve_static in a Router should serve a sub-path file."""
        (tmp_path / "style.css").write_text("body { color: red; }", encoding="utf-8")
        router = Router()
        router.add_route("GET", "/static/*", serve_static(str(tmp_path)))
        resp = router.dispatch(_make_request("GET", "/static/style.css"))
        assert resp.status == STATUS_200
        assert "body { color: red; }" in resp.body
        assert resp.headers["Content-Type"] == "text/css"

    def test_static_helper(self, tmp_path: Path) -> None:
        """Router.static should mount a folder and serve its files."""
        (tmp_path / "app.js").write_text("console.log('hi');", encoding="utf-8")
        router = Router()
        router.static("/assets", str(tmp_path))
        resp = router.dispatch(_make_request("GET", "/assets/app.js"))
        assert resp.status == STATUS_200
        assert "console.log('hi');" in resp.body
