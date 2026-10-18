"""Shared auth test helpers: a demo site with users, a fake clock and log-in."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

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
)
from pyweb.messages import HttpRequest, HttpResponse, JsonValue, empty, json
from pyweb.security import LoginThrottle, SessionSigner
from pyweb.testing import TestClient

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


def build(
    *,
    login_path: str = "/login",
    secure_cookies: bool = False,
    bearer_verifier: Callable[[str], User | None] | None = None,
) -> Site:
    """Build an app with a login form, protected routes and two users."""
    clock = Clock()
    users = InMemoryUsers()
    users.add("ada", ADA_WORD, roles=("admin",), rounds=ROUNDS)
    users.add("bob", BOB_WORD, rounds=ROUNDS)
    signer = SessionSigner(DEMO_KEY, clock=clock)
    auth = SessionAuth(
        signer,
        users,
        login_path=login_path,
        secure_cookies=secure_cookies,
        bearer_verifier=bearer_verifier,
    )
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

    @app.get("/viewer")
    @require("viewer")
    def viewer(_request: HttpRequest) -> dict[str, JsonValue]:  # pyright: ignore[reportUnusedFunction]
        """Only for viewers."""
        return {"viewer": True}

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
