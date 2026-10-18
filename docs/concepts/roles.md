# Roles

## Backstage Passes

At a concert, your **wristband** gets you through the gate and into the
crowd. But the door behind the stage has its own guard, and the
wristband is not enough there: you need a **backstage pass**. The crew
has passes; fans do not.

PyWeb works the same way. Logging in gives a visitor a session cookie,
the wristband. `SessionAuth`, a middleware, reads it on every request
and puts the visitor in `request.user`. A **role** is a backstage pass:
a name such as `"crew"` or `"admin"` stored with the user. The
`require(...)` decorator is the guard at a door:

- `require()` -- any logged-in visitor may come in.
- `require("crew")` -- only visitors holding the `"crew"` pass.
- A stranger gets `401 Unauthorized`. A browser (one that asks for HTML)
  is sent to the login page instead, with `?next=` saying where it was
  going.
- A logged-in visitor without the pass gets `403 Forbidden`.

## Try It

```python
from pynet.loop import run

from pyweb.app import App
from pyweb.auth import InMemoryUsers, SessionAuth, current_user, require
from pyweb.messages import empty
from pyweb.security import SessionSigner
from pyweb.testing import TestClient

users = InMemoryUsers()
ada = users.add("ada", "demo-password-not-real", roles=["crew"], rounds=1_000)
bob = users.add("bob", "another-demo-password", rounds=1_000)
auth = SessionAuth(SessionSigner("demo-key-not-real-0123456789abcdef"), users)
app = App()
app.middleware(auth)


@app.get("/stage")
@require()
def stage(request):
    return {"welcome": current_user(request).name}


@app.get("/backstage")
@require("crew")
async def backstage(request):
    return {"crew": current_user(request).name}


def wristband_for(user):
    response = empty()
    auth.login(response, user)
    return response.headers.get("Set-Cookie").split(";")[0].split("=", 1)[1]


async def main():
    stranger = TestClient(app)
    print((await stranger.get("/stage")).status)
    page = await stranger.get("/stage", headers={"Accept": "text/html"})
    print(page.status, page.headers.get("Location"))

    fan = TestClient(app, cookies={"session": wristband_for(bob)})
    print((await fan.get("/stage")).json())
    print((await fan.get("/backstage")).status)

    roadie = TestClient(app, cookies={"session": wristband_for(ada)})
    print((await roadie.get("/backstage")).json())


run(main())
```

Output:

```text
401
303 /login?next=%2Fstage
{'welcome': 'bob'}
403
{'crew': 'ada'}
```

The demo hands out wristbands directly; a real site does it in a login
handler with `authenticate(users, throttle, name, password)` and then
`auth.login(response, user)`. The passwords and key are fake.

## The Rules

- **Put `require` under the route decorator**, so the App registers the
  guarded version. Put it on top by mistake and the App would already
  hold the *unguarded* handler -- a door with the guard standing in the
  wrong corridor -- so PyWeb stops you straight away with
  `TypeError: put @require below the route decorator (closer to the
  function)` instead of quietly leaving the page open to everyone. It works on `async def` and plain `def` handlers, keeps
  the handler's name and path parameters, and stays a plain `def` for a
  plain handler, so `blocking=True` still works.
- **WebSocket doors too.** `@app.websocket(...)` routes can use
  `require`. `SessionAuth` checks them *before* the handshake, so a
  stranger gets a plain `401` (never a redirect) and the WebSocket is
  never opened. A cookie visitor must also be calling from your own site
  (the `Origin` check in [CSRF Protection](csrf.md)).
- **API tokens.** `auth.api_token(user)` makes a token for scripts to send
  as `Authorization: Bearer <token>`. If that header is sent, it is the
  only thing looked at: a bad token means "stranger", even with a good
  cookie.
- **Broken wristbands.** A tampered, expired or unknown cookie never
  causes an error page. The visitor is just a stranger.
- **Logging out** (`auth.logout(response)`) asks the browser to forget
  the cookie. The old token itself keeps working until it expires
  (8 hours), because it is stamped, not stored.
- **Too many guesses.** `authenticate` uses a `LoginThrottle`: after 5
  wrong passwords in 5 minutes the name is locked for 30 seconds and
  login answers `429`.
- **Going back.** Before redirecting to a `?next=` value after login, pass
  it through `safe_next(...)`, which only allows paths on your own site.

## Your Own Tokens

Some visitors are not people at a gate but robots, such as a build
server. Think of a **spare key** you cut for the robot: it is not a
wristband, but the guard still checks it against the key list.

Give `SessionAuth` a `bearer_verifier`: a small function that gets the
token text and returns the `User` it belongs to, or `None` if it does not
know the token. It is asked first; if it says `None`, PyWeb still tries
its own signed API tokens. Bearer requests never need a CSRF token, and
a bad one makes the visitor a stranger even with a good cookie. If your
function breaks, PyWeb logs it (without the token) and treats the visitor
as a stranger instead of showing a 500.

```python
from pynet.loop import run

from pyweb.app import App
from pyweb.auth import InMemoryUsers, SessionAuth, User, require
from pyweb.security import SessionSigner
from pyweb.testing import TestClient

SPARE_KEYS = {"robot-key-not-real": User("robot", "", frozenset({"viewer"}))}

auth = SessionAuth(
    SessionSigner("demo-key-not-real-0123456789abcdef"),
    InMemoryUsers(),
    bearer_verifier=SPARE_KEYS.get,
)
app = App()
app.middleware(auth)


@app.get("/reports")
@require("viewer")
def reports(request):
    return {"ok": True}


async def main():
    client = TestClient(app)
    good = await client.get("/reports", headers={"Authorization": "Bearer robot-key-not-real"})
    bad = await client.get("/reports", headers={"Authorization": "Bearer guess"})
    print(good.status, bad.status)


run(main())
```

## What We Test

- Login sets an `HttpOnly`, `SameSite=Lax` cookie; logout clears it.
- Strangers get `401` (JSON) or `303` to `/login?next=%2Fjobs` (HTML).
- A user without the role gets `403`; one with it gets in.
- A tampered, garbage, foreign or expired cookie is anonymous, never a 500.
- A custom `bearer_verifier` is tried first, falls back to API tokens, and a
  raising one is logged and anonymous.
- Bearer tokens work; session and API tokens cannot stand in for each other.
- Five bad logins lock the name with `429` until the lockout ends.
- WebSocket upgrades: strangers `401`, wrong role `403`, crew `101`.
- `@require` above `@app.get` or `@app.websocket` raises `TypeError` when
  the module is loaded; the right order guards the route.

## Next Up

Forms that change things need one more guard. See
[CSRF Protection](csrf.md).
