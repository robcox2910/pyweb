"""Tests for session auth, roles and CSRF protection over the TestClient."""

from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

import pytest
from pynet.loop import run
from pynet.loop.testing import FakeClock

from pyweb.app import App
from pyweb.auth import (
    InMemoryUsers,
    SessionAuth,
    User,
    authenticate,
    current_user,
    require,
    safe_next,
)
from pyweb.messages import HTTPError, HttpRequest, HttpResponse, JsonValue, empty, json, redirect
from pyweb.security import LoginThrottle, SessionSigner
from pyweb.testing import TestClient, TestResponse

DEMO_KEY = "test-secret-not-real-0123456789abcdef"
ROUNDS = 1_000
ADA_WORD = "ada-pass-1234"
BOB_WORD = "bob-pass-5678"
START = 1_000_000.0
EIGHT_HOURS = 8 * 3600
LOCKOUT = 30
MAX_FAILURES = 5
OK = 200
NO_CONTENT = 204
SEE_OTHER = 303
UNAUTHORIZED = 401
FORBIDDEN = 403
TOO_MANY = 429
HTML = {"Accept": "text/html"}


class Clock:
    """A hand-wound clock shared by the signer and the throttle."""

    def __init__(self) -> None:
        """Start at a fixed moment."""
        self.now = START

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now


@dataclass
class Site:
    """An app wired with auth, plus the parts a test may poke."""

    app: App
    auth: SessionAuth
    users: InMemoryUsers
    clock: Clock
    signer: SessionSigner


def go[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on a fresh loop with an auto-jumping fake clock."""
    return run(coro, clock=FakeClock(autojump=True))


def build(*, login_path: str = "/login", secure_cookies: bool = False) -> Site:
    """Build an app with a login form, protected routes and two users."""
    clock = Clock()
    users = InMemoryUsers()
    users.add("ada", ADA_WORD, roles=("admin",), rounds=ROUNDS)
    users.add("bob", BOB_WORD, rounds=ROUNDS)
    signer = SessionSigner(DEMO_KEY, clock=clock)
    auth = SessionAuth(signer, users, login_path=login_path, secure_cookies=secure_cookies)
    throttle = LoginThrottle(clock=clock)
    app = App()
    app.middleware(auth)

    @app.post("/login")
    def login(request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Check the form and hand out a session."""
        form = request.form()
        user = authenticate(users, throttle, form.get("name") or "", form.get("password") or "")
        if user is None:
            return json({"error": "bad login"}, UNAUTHORIZED)
        response = json({"user": user.name})
        auth.login(response, user)
        return response

    @app.post("/logout")
    def logout(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Forget the session."""
        response = empty()
        auth.logout(response)
        return response

    @app.get("/whoami")
    def whoami(request: HttpRequest) -> dict[str, JsonValue]:  # pyright: ignore[reportUnusedFunction]
        """Say who is visiting, for anyone."""
        user = current_user(request)
        return {"user": None if user is None else user.name, "csrf": auth.csrf_token(request)}

    @app.get("/jobs")
    @require()
    async def jobs(request: HttpRequest) -> dict[str, JsonValue]:  # pyright: ignore[reportUnusedFunction]
        """List jobs for any logged-in user."""
        user = current_user(request)
        return {"user": None if user is None else user.name}

    @app.post("/jobs")
    @require()
    async def add_job(_request: HttpRequest) -> dict[str, JsonValue]:  # pyright: ignore[reportUnusedFunction]
        """Pretend to add a job."""
        return {"added": True}

    @app.route("PUT", "/jobs/<int:job>", blocking=True)
    @require()
    def put_job(_request: HttpRequest, job: int) -> dict[str, JsonValue]:  # pyright: ignore[reportUnusedFunction]
        """Pretend to replace a job, on a helper thread."""
        return {"job": job}

    for method in ("PATCH", "DELETE"):
        app.route(method, "/jobs")(add_job)

    @app.get("/admin")
    @require("admin")
    def admin(_request: HttpRequest) -> dict[str, JsonValue]:  # pyright: ignore[reportUnusedFunction]
        """Only for admins."""
        return {"admin": True}

    @app.get("/token")
    @require()
    def token(request: HttpRequest) -> dict[str, JsonValue]:  # pyright: ignore[reportUnusedFunction]
        """Hand out an API token for the caller."""
        user = current_user(request)
        assert user is not None
        return {"token": auth.api_token(user)}

    return Site(app, auth, users, clock, signer)


async def log_in(client: TestClient, name: str = "ada", password: str = ADA_WORD) -> str:
    """Log in through the form and return the session's CSRF token."""
    response = await client.post("/login", form={"name": name, "password": password})
    assert response.status == OK
    csrf = (await client.get("/whoami")).json()
    assert isinstance(csrf, dict)
    token = csrf["csrf"]
    assert isinstance(token, str)
    return token


class TestLogin:
    """Test logging in, logging out and the session cookie."""

    def test_login_sets_http_only_cookie(self) -> None:
        """Set an HttpOnly, SameSite=Lax session cookie on a good login."""
        site = build()
        client = TestClient(site.app)
        response = go(client.post("/login", form={"name": "ada", "password": ADA_WORD}))
        cookie = response.headers.get("Set-Cookie") or ""
        assert cookie.startswith("session=")
        assert "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie
        assert "Secure" not in cookie
        assert "session" in client.cookies

    def test_secure_cookies_adds_secure(self) -> None:
        """Mark the cookie Secure when asked."""
        site = build(secure_cookies=True)
        response = go(
            TestClient(site.app).post("/login", form={"name": "ada", "password": ADA_WORD})
        )
        assert "Secure" in (response.headers.get("Set-Cookie") or "")

    def test_login_returns_the_session_csrf_token(self) -> None:
        """Return the same CSRF token that csrf_token later reads from the cookie."""
        site = build()
        user = site.users.get("ada")
        assert user is not None
        response = HttpResponse()
        csrf = site.auth.login(response, user)
        client = TestClient(site.app)
        client.cookies["session"] = (response.headers.get("Set-Cookie") or "").split(";")[0][8:]
        assert go(client.get("/whoami")).json() == {"user": "ada", "csrf": csrf}

    def test_wrong_password_is_refused(self) -> None:
        """Refuse a wrong password and an unknown name without a cookie."""
        client = TestClient(build().app)
        bad = go(client.post("/login", form={"name": "ada", "password": "nope"}))
        unknown = go(client.post("/login", form={"name": "zed", "password": "nope"}))
        assert (bad.status, unknown.status) == (UNAUTHORIZED, UNAUTHORIZED)
        assert client.cookies == {}

    def test_logout_clears(self) -> None:
        """Delete the cookie on logout so protected routes answer 401 again."""
        site = build()
        client = TestClient(site.app)

        async def flow() -> tuple[TestResponse, TestResponse]:
            """Log in, log out (with the CSRF token), then try a protected page."""
            csrf = await log_in(client)
            out = await client.post("/logout", headers={"X-CSRF-Token": csrf})
            return out, await client.get("/jobs")

        out, after = go(flow())
        assert out.status == NO_CONTENT
        assert "Max-Age=0" in (out.headers.get("Set-Cookie") or "")
        assert client.cookies == {}
        assert after.status == UNAUTHORIZED

    def test_anonymous_has_no_csrf_token(self) -> None:
        """Report no user and no CSRF token for a stranger."""
        assert go(TestClient(build().app).get("/whoami")).json() == {"user": None, "csrf": None}


class TestRequire:
    """Test the require decorator on protected routes."""

    def test_anonymous_json_is_401(self) -> None:
        """Answer 401 to an anonymous JSON client."""
        assert go(TestClient(build().app).get("/jobs")).status == UNAUTHORIZED

    def test_anonymous_html_is_redirected_to_login(self) -> None:
        """Redirect an anonymous browser to the login page with ?next=."""
        response = go(TestClient(build().app).get("/jobs", headers=HTML))
        assert response.status == SEE_OTHER
        assert response.headers.get("Location") == "/login?next=%2Fjobs"

    def test_custom_login_path(self) -> None:
        """Use the login_path given to SessionAuth."""
        response = go(TestClient(build(login_path="/sign-in").app).get("/admin", headers=HTML))
        assert response.headers.get("Location") == "/sign-in?next=%2Fadmin"

    def test_logged_in_user_gets_in(self) -> None:
        """Let a logged-in user through, with request.user set."""
        client = TestClient(build().app)

        async def flow() -> TestResponse:
            """Log in as Bob and list jobs."""
            await log_in(client, "bob", BOB_WORD)
            return await client.get("/jobs")

        assert go(flow()).json() == {"user": "bob"}

    def test_missing_role_is_403(self) -> None:
        """Answer 403 when the user lacks a listed role, even for HTML."""
        client = TestClient(build().app)

        async def flow() -> tuple[int, int]:
            """Try the admin page as Bob, then as Ada."""
            await log_in(client, "bob", BOB_WORD)
            bob = (await client.get("/admin", headers=HTML)).status
            client.cookies.clear()
            await log_in(client)
            return bob, (await client.get("/admin")).status

        assert go(flow()) == (FORBIDDEN, OK)

    def test_blocking_sync_handler_keeps_path_params(self) -> None:
        """Run a wrapped blocking handler with its path parameters."""
        client = TestClient(build().app)

        async def flow() -> TestResponse:
            """Log in and PUT a job."""
            csrf = await log_in(client)
            return await client.put("/jobs/7", headers={"X-CSRF-Token": csrf})

        assert go(flow()).json() == {"job": 7}


class TestBadCookies:
    """Test that broken cookies only ever make a visitor anonymous."""

    def test_tampered_and_expired_cookies_are_anonymous(self) -> None:
        """Answer 401/303 (never 500) for tampered, garbage, foreign and expired cookies."""
        site = build()
        client = TestClient(site.app)
        go(log_in(client))
        good = client.cookies["session"]
        payload, stamp = good.split(".")
        flipped = ("A" if payload[0] != "A" else "B") + payload[1:]
        stranger = site.signer.sign({"kind": "session", "user": "zed", "csrf": "x"})
        odd_name = site.signer.sign({"kind": "session", "user": 7, "csrf": "x"})
        for bad in (f"{flipped}.{stamp}", "garbage", "", "a.b.c", stranger, odd_name):
            client.cookies["session"] = bad
            assert go(client.get("/jobs")).status == UNAUTHORIZED
            assert go(client.get("/jobs", headers=HTML)).status == SEE_OTHER
        client.cookies["session"] = good
        assert go(client.get("/jobs")).status == OK
        site.clock.now += EIGHT_HOURS + 1
        assert go(client.get("/jobs")).status == UNAUTHORIZED
        assert go(client.get("/jobs", headers=HTML)).status == SEE_OTHER


class TestThrottle:
    """Test that repeated bad logins are locked out."""

    def test_throttle_429_after_five_bad_logins(self) -> None:
        """Answer 429 after five failures, even for the right password, until the lockout ends."""
        site = build()
        client = TestClient(site.app)
        bad = {"name": "ada", "password": "wrong"}
        statuses = [go(client.post("/login", form=bad)).status for _ in range(MAX_FAILURES)]
        good = {"name": "ada", "password": ADA_WORD}
        locked = go(client.post("/login", form=good)).status
        site.clock.now += LOCKOUT + 1
        after = go(client.post("/login", form=good)).status
        assert statuses == [UNAUTHORIZED] * MAX_FAILURES
        assert (locked, after) == (TOO_MANY, OK)

    def test_success_forgets_failures(self) -> None:
        """Forget earlier failures after a good login."""
        users = InMemoryUsers()
        users.add("kim", "kim-pass", rounds=ROUNDS)
        throttle = LoginThrottle(clock=Clock())
        for _ in range(MAX_FAILURES - 1):
            assert authenticate(users, throttle, "kim", "nope") is None
        assert authenticate(users, throttle, "kim", "kim-pass") is not None
        assert authenticate(users, throttle, "kim", "nope") is None
        assert not throttle.locked("kim")

    def test_locked_raises_http_error(self) -> None:
        """Raise HTTPError(429) while a name is locked."""
        throttle = LoginThrottle(clock=Clock())
        users = InMemoryUsers()
        for _ in range(MAX_FAILURES):
            authenticate(users, throttle, "zed", "nope")
        with pytest.raises(HTTPError) as caught:
            authenticate(users, throttle, "zed", "nope")
        assert caught.value.status == TOO_MANY


class TestUsers:
    """Test the user record and the in-memory store."""

    def test_add_and_get(self) -> None:
        """Store a hashed password and frozen roles; unknown names give None."""
        users = InMemoryUsers()
        added = users.add("ada", ADA_WORD, roles=["admin", "admin"], rounds=ROUNDS)
        assert users.get("ada") == added
        assert added.roles == frozenset({"admin"})
        assert ADA_WORD not in added.password_hash
        assert users.get("zed") is None

    def test_repr_hides_password_hash(self) -> None:
        """Leave the password hash out of the repr so logs cannot leak it."""
        user = User("ada", "pbkdf2_sha256$1$aa$bb")
        assert "pbkdf2" not in repr(user)

    def test_safe_next(self) -> None:
        """Allow only same-site paths as a ?next= target."""
        assert safe_next("/jobs?page=2") == "/jobs?page=2"
        for bad in (None, "", "jobs", "//evil.example", "/\\evil.example", "https://evil.example"):
            assert safe_next(bad) == "/"

    @pytest.mark.parametrize(
        "bad", ["/\t/evil.example", "/\n/evil.example", "/\r\n/x", "/a\\b", "/\x7fx", "/\x00x"]
    )
    def test_safe_next_refuses_control_chars_and_backslashes(self, bad: str) -> None:
        """Refuse targets a browser could rewrite into another site, or that break headers."""
        assert safe_next(bad) == "/"

    def test_login_redirect_with_tab_trick_goes_home(self) -> None:
        """Send a login with ?next=/%09/evil.example back to "/" instead of off-site."""
        site = build()

        @site.app.post("/form-login")
        def form_login(request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
            """Log in and go where ?next= says, if it is safe."""
            user = site.users.get(request.form().get("name") or "")
            assert user is not None
            response = redirect(safe_next(request.query.get("next")))
            site.auth.login(response, user)
            return response

        response = go(
            TestClient(site.app).post(
                "/form-login", query={"next": "/\t/evil.example"}, form={"name": "ada"}
            )
        )
        assert response.status == SEE_OTHER
        assert response.headers.get("Location") == "/"
