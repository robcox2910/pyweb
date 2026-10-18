"""Tests for session auth, roles and CSRF protection over the TestClient."""

import logging

import pytest

from pyweb.auth import (
    InMemoryUsers,
    User,
    authenticate,
    safe_next,
)
from pyweb.messages import HTTPError, HttpRequest, HttpResponse, redirect
from pyweb.security import LoginThrottle
from pyweb.testing import TestClient, TestResponse
from tests.aio._auth_helpers import (
    ADA_WORD,
    BOB_WORD,
    EIGHT_HOURS,
    FORBIDDEN,
    HTML,
    LOCKOUT,
    MAX_FAILURES,
    NO_CONTENT,
    OK,
    ROUNDS,
    SEE_OTHER,
    TOO_MANY,
    UNAUTHORIZED,
    Clock,
    build,
    go,
    log_in,
)


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


CI_STAMP = "tok"
BAD_STAMP = "nope"
LEAKY_STAMP = "sekret-token-text"


def _ci_verifier(token: str) -> User | None:
    """Accept only ``CI_STAMP``, as a viewer called ci."""
    return User("ci", "", frozenset({"viewer"})) if token == CI_STAMP else None


def _bearer(token: str) -> dict[str, str]:
    """Return an Authorization header carrying this token."""
    return {"Authorization": f"Bearer {token}"}


class TestBearerVerifier:
    """Test the pluggable bearer-token verifier."""

    def test_bearer_verifier_user(self) -> None:
        """Let a verified user through viewer doors and refuse admin ones."""
        client = TestClient(build(bearer_verifier=_ci_verifier).app)
        assert go(client.get("/viewer", headers=_bearer(CI_STAMP))).status == OK
        assert go(client.get("/admin", headers=_bearer(CI_STAMP))).status == FORBIDDEN

    def test_bearer_verifier_none_falls_back(self) -> None:
        """Still accept a signed API token when the verifier says None."""
        site = build(bearer_verifier=_ci_verifier)
        ada = site.users.get("ada")
        assert ada is not None
        response = go(TestClient(site.app).get("/admin", headers=_bearer(site.auth.api_token(ada))))
        assert response.status == OK

    def test_bearer_skips_csrf_and_cookie(self) -> None:
        """Skip CSRF for a bearer POST, and ignore a good cookie beside a bad bearer."""
        site = build(bearer_verifier=_ci_verifier)
        client = TestClient(site.app)
        assert go(client.post("/jobs", headers=_bearer(CI_STAMP))).status == OK
        go(log_in(client))
        assert go(client.get("/jobs")).status == OK
        assert go(client.get("/jobs", headers=_bearer(BAD_STAMP))).status == UNAUTHORIZED

    def test_verifier_exception_is_anonymous(self, caplog: pytest.LogCaptureFixture) -> None:
        """Log a raising verifier without the token and treat the visitor as anonymous."""

        def boom(_token: str) -> User | None:
            """Fail every time."""
            raise KeyError(LEAKY_STAMP)

        client = TestClient(build(bearer_verifier=boom).app)
        with caplog.at_level(logging.ERROR, logger="pyweb.auth"):
            response = go(client.get("/jobs", headers=_bearer(LEAKY_STAMP)))
        assert response.status == UNAUTHORIZED
        assert [r for r in caplog.records if r.name == "pyweb.auth"]
        assert "KeyError" in caplog.text
        assert LEAKY_STAMP not in caplog.text

    def test_empty_bearer_skips_verifier(self) -> None:
        """Treat an empty bearer as anonymous without asking the verifier."""
        seen: list[str] = []

        def spy(token: str) -> User | None:
            """Record every token asked about."""
            seen.append(token)
            return None

        client = TestClient(build(bearer_verifier=spy).app)
        assert go(client.get("/jobs", headers={"Authorization": "Bearer "})).status == UNAUTHORIZED
        assert seen == []

    def test_bearer_post_with_cookie_needs_no_csrf(self) -> None:
        """Let a verified bearer POST through even beside a valid cookie."""
        client = TestClient(build(bearer_verifier=_ci_verifier).app)
        go(log_in(client))
        assert go(client.post("/jobs", headers=_bearer(CI_STAMP))).status == OK

    def test_bad_bearer_with_cookie_post_is_401_not_csrf(self) -> None:
        """Answer 401 (not a CSRF 403) to a bad bearer POST with a valid cookie."""
        client = TestClient(build(bearer_verifier=_ci_verifier).app)
        go(log_in(client))
        assert go(client.post("/jobs", headers=_bearer(BAD_STAMP))).status == UNAUTHORIZED
