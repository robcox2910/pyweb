"""Logging in, wristbands and backstage passes -- sessions, throttling and roles.

Run it with: uv run python examples/login_demo.py
The passwords and key below are obviously fake demo values.
"""

from pynet.loop import run

from pyweb.app import App
from pyweb.auth import InMemoryUsers, SessionAuth, authenticate, current_user, require
from pyweb.messages import HttpRequest, HttpResponse, json
from pyweb.security import LoginThrottle, SessionSigner
from pyweb.testing import TestClient

ROUNDS = 1_000  # fast for a demo; the real default is 100 000
TRIES_BEFORE_LOCKOUT = 3

users = InMemoryUsers()
users.add("ada", "demo-password-not-real", roles=["crew"], rounds=ROUNDS)
users.add("bob", "another-demo-password", rounds=ROUNDS)
auth = SessionAuth(SessionSigner("demo-key-not-real-0123456789abcdef"), users)
throttle = LoginThrottle(max_failures=TRIES_BEFORE_LOCKOUT)
app = App()
app.middleware(auth)


@app.post("/login", blocking=True)
def login(request: HttpRequest) -> HttpResponse:
    """Check the name and password; hand out a session cookie when right."""
    form = request.form()
    user = authenticate(users, throttle, form.get("name") or "", form.get("password") or "")
    if user is None:
        return json({"error": "bad login"}, 401)
    response = json({"hello": user.name})
    auth.login(response, user)
    return response


@app.get("/stage")
@require()
def stage(request: HttpRequest) -> dict[str, str]:
    """Let any logged-in visitor in."""
    user = current_user(request)
    # require() has already turned strangers away, so user is set here.
    return {"welcome": user.name if user else "friend"}


@app.get("/backstage")
@require("crew")
def backstage(_request: HttpRequest) -> dict[str, str]:
    """Let only crew in."""
    return {"secret": "the confetti cannon"}


async def demo() -> None:
    """Walk through a stranger, a wrong password, a lockout and two roles."""
    stranger = TestClient(app)
    print("Stranger at the stage:", (await stranger.get("/stage")).status)

    for attempt in range(TRIES_BEFORE_LOCKOUT):
        reply = await stranger.post("/login", form={"name": "bob", "password": f"guess{attempt}"})
        print("Wrong password:", reply.status)
    locked = await stranger.post(
        "/login", form={"name": "bob", "password": "another-demo-password"}
    )
    print("Too many tries, locked out:", locked.status)

    ada = TestClient(app)
    reply = await ada.post("/login", form={"name": "ada", "password": "demo-password-not-real"})
    greeting = reply.json()
    assert isinstance(greeting, dict)  # noqa: S101 - narrows the JSON type for the demo
    print(f"Welcome back, {greeting['hello']}!")
    print("Ada at the stage:", (await ada.get("/stage")).status)
    print("Ada backstage:", (await ada.get("/backstage")).status)

    users.add("eve", "third-demo-password", rounds=ROUNDS)
    eve = TestClient(app)
    await eve.post("/login", form={"name": "eve", "password": "third-demo-password"})
    print("Only crew backstage -- eve gets:", (await eve.get("/backstage")).status)


def main() -> None:
    """Run the demo."""
    run(demo())


if __name__ == "__main__":
    main()
