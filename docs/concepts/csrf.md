# CSRF Protection

## The Forged Permission Slip

Your school sends home a permission slip: "Ada may go on the zoo trip."
A grown-up signs it and you hand it in. Now imagine a sneaky kid who
writes their *own* slip -- "Ada gives me her pocket money" -- and slips
it into Ada's bag. Ada hands in her bag as usual, and the teacher sees a
slip coming from Ada. Should the teacher believe it?

Not if the school is clever. The real slips carry a **secret sticker**
that only the school gives out, and only to Ada. The forged slip has no
sticker, so the teacher throws it away.

Websites have the same problem. Your browser sends your session cookie
with *every* request to a site, even one that another website tricked it
into making. That trick is called **Cross-Site Request Forgery (CSRF)**.
The fix is the secret sticker: a random **CSRF token** that the site gives
to its own pages. A page from another website cannot read it, so it
cannot put it on the forged request.

## Try It

```python
from pynet.loop import run

from pyweb.app import App
from pyweb.auth import InMemoryUsers, SessionAuth, authenticate, current_user
from pyweb.messages import json
from pyweb.security import LoginThrottle, SessionSigner
from pyweb.testing import TestClient

users = InMemoryUsers()
users.add("ada", "demo-password-not-real", rounds=1_000)
auth = SessionAuth(SessionSigner("demo-key-not-real-0123456789abcdef"), users)
throttle = LoginThrottle()
app = App()
app.middleware(auth)


@app.post("/login", blocking=True)
def login(request):
    form = request.form()
    user = authenticate(users, throttle, form.get("name") or "", form.get("password") or "")
    if user is None:
        return json({"error": "bad login"}, 401)
    response = json({"hello": user.name})
    auth.login(response, user)
    return response


@app.get("/form")
def form_page(request):
    return {"csrf_token": auth.csrf_token(request)}


@app.post("/pocket-money")
def pocket_money(request):
    return {"sent": 5, "by": current_user(request).name}


async def main():
    browser = TestClient(app)
    await browser.post("/login", form={"name": "ada", "password": "demo-password-not-real"})
    token = (await browser.get("/form")).json()["csrf_token"]

    forged = await browser.post("/pocket-money")
    print(forged.status, forged.json()["message"])
    wrong = await browser.post("/pocket-money", headers={"X-CSRF-Token": "guess"})
    print(wrong.status)
    print((await browser.post("/pocket-money", form={"csrf_token": token})).json())
    print((await browser.post("/pocket-money", headers={"X-CSRF-Token": token})).status)

    robot = TestClient(app)
    bearer = {"Authorization": f"Bearer {auth.api_token(users.get('ada'))}"}
    print((await robot.post("/pocket-money", headers=bearer)).status)


run(main())
```

Output:

```text
403 csrf
403
{'sent': 5, 'by': 'ada'}
200
200
```

The forged request (no sticker) and the guessed one are both refused
with `403`. The real form, carrying the token, goes through. The robot
using an API token (`Authorization: Bearer`) needs no sticker: another
website cannot make a browser add that header, so it cannot be forged.

The key and password above are fake. A real secret is at least 32
random characters kept out of your code, and nothing in `pyweb.auth`
ever logs a password, a token, a cookie value or a CSRF token.

## The Rules

- **Which requests?** Only visitors logged in by **cookie** are checked,
  and only for `POST`, `PUT`, `PATCH` and `DELETE`. Reading (`GET`,
  `HEAD`) must never change anything, so it needs no sticker.
- **Where does the token go?** In the `X-CSRF-Token` header, or in a
  form field called `csrf_token`. The form field only counts for normal
  HTML forms (`application/x-www-form-urlencoded`); a JSON body is never
  searched, so send the header there. If the header is present, it wins.
- **Where does the token come from?** `auth.login(response, user)` makes
  a fresh one for every login and returns it; `auth.csrf_token(request)`
  reads it back later, for example to put a hidden field in a form:
  `<input type="hidden" name="csrf_token" value="...">`.
- **Logged in already?** A visitor with a session cookie needs the
  token even to post the login form again, so put the hidden field there
  too.
- **Belt and braces.** The session cookie is `HttpOnly` (page scripts
  cannot read it) and `SameSite=Lax` (browsers do not send it with most
  requests from other sites). The token protects you even where a
  browser ignores `SameSite`. `SameSite` is not enough on its own:
  "same site" counts every port on the same machine, so a page on
  `localhost:3000` still gets your `localhost:8000` cookie.
- **WebSockets check the Origin instead.** See below.

## WebSockets: Which House Is Calling?

A WebSocket handshake is a `GET`, so it carries no CSRF sticker, yet the
browser still adds your cookie -- even when a page from *another* site
dials the number. Without a check, that page could open a phone line in
your name (this is called *cross-site WebSocket hijacking*).

So the phone line checks which house the call comes from. Browsers put
an `Origin` header (the page's `scheme://host:port`) on every WebSocket
handshake, and a page cannot fake it. For a visitor logged in by
**cookie**, `SessionAuth` only puts the call through when the `Origin`,
without its `http://` or `https://`, equals the `Host` the browser
dialled -- or is one of the exact `"http(s)://host:port"` strings you
list in `SessionAuth(..., allowed_origins=[...])`. A different or
missing `Origin` gets `403`. Bearer tokens and strangers are not
checked: another site cannot make a browser send your API token.

```python
from pynet.loop import run

from pyweb.app import App
from pyweb.auth import InMemoryUsers, SessionAuth
from pyweb.messages import empty
from pyweb.security import SessionSigner
from pyweb.testing import TestClient

users = InMemoryUsers()
ada = users.add("ada", "demo-password-not-real", rounds=1_000)
signer = SessionSigner("demo-key-not-real-0123456789abcdef")
auth = SessionAuth(signer, users, allowed_origins=["http://localhost:3000"])
app = App()
app.middleware(auth)


@app.websocket("/chat")
async def chat(ws, request):
    await ws.send_text("hello")


def wristband():
    response = empty()
    auth.login(response, ada)
    return response.headers.get("Set-Cookie").split(";")[0].split("=", 1)[1]


async def main():
    browser = TestClient(app, cookies={"session": wristband()})
    dial = {"Upgrade": "websocket", "Connection": "Upgrade"}
    for origin in ["http://testserver", "http://localhost:3000", "http://evil.example"]:
        answer = await browser.get("/chat", headers={**dial, "Origin": origin})
        print(origin, answer.status)
    print("no Origin", (await browser.get("/chat", headers=dial)).status)


run(main())
```

Output:

```text
http://testserver 101
http://localhost:3000 101
http://evil.example 403
no Origin 403
```

(The `TestClient` always sends `Host: testserver`, so that is "our own
house" here; `101` means "put through".)

## What We Test

- A cookie `POST` with no token, a wrong token, or a token only inside a
  JSON body is refused with `403`; the right header or form field is
  accepted.
- `PUT`, `PATCH` and `DELETE` are checked too; a session missing its
  token is refused.
- A wrong header is not rescued by a right form field.
- Bearer tokens are exempt; strangers can still post the login form.
- Over a real socket, a cookie WebSocket upgrade from our own origin or
  an allow-listed one gets `101`; another port, `Origin: null` or no
  `Origin` gets `403`; a bearer upgrade with no `Origin` gets `101`.
- Walking through every flow at `DEBUG` logging, no password, token,
  cookie value or CSRF token appears in any log record.

## Next Up

Some pages are for special people only. See [Roles](roles.md).
