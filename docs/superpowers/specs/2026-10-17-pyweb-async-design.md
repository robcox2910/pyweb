# PyWeb Async Web Framework — Design

**Date:** 2026-10-17
**Repo:** pyweb (library), pystack (integration)
**Depends on:** pynet `v0.1.0` (event loop, streams, `pynet.ws`), pycrypt `v0.1.0` (HMAC, PBKDF2)

## Why

PyWeb today is a teaching web server: a blocking socket loop that serves
**one visitor at a time**, a regex router, string request bodies, and a
`{{ name }}` template filler that does not escape HTML. It works for
"hello world" but cannot carry the job scheduler's web front end, which
needs:

- many browsers connected at once, without one slow visitor blocking
  the rest ("efficient concurrency is FUNDAMENTAL" — the user);
- **live updates**: the job board, dependency graph and timeline must
  change as jobs run, so the server pushes events (Server-Sent Events)
  and supports WebSockets;
- **logins**: operators can rerun or hold jobs, viewers can only look,
  scripts call a JSON API with a token;
- safe HTML (job names and log text must never become live script).

So PyWeb gains an **async framework on PyNet's own event loop** while the
existing synchronous `Server`, `Router`, `Request`, `Response`, `render`
and `serve_static` stay exactly as they are for learning ("keep them
alongside").

The user handed the design choices to Claude ("you decide", "move on");
everything below is Claude's call.

## Goals

1. `App` + `AsyncServer`: concurrent HTTP/1.1 on `pynet.loop`, keep-alive,
   strict limits and timeouts, graceful shutdown.
2. Handlers may be `async def` or plain `def`; blocking handlers can opt
   into running on a worker thread.
3. Middleware, typed path parameters, query/form/JSON/cookie parsing,
   structured errors, safe 500s.
4. Streaming: Server-Sent Events (with a broadcast hub) and WebSockets.
5. Auth: salted slow password hashing, signed session cookies, roles,
   bearer tokens, CSRF protection, login throttling.
6. HTML templates that escape by default.
7. Static files with ETag / 304 and no path traversal.
8. An in-process `TestClient` so apps are testable without sockets.
9. Kid-friendly docs for every concept, runnable examples, PyStack demo.

## Non-goals

- HTTP/2, TLS (the user: "plain for now, TLS later"), chunked *request*
  bodies (rejected with 411 Length Required), multipart uploads.
- A template language with loops/conditionals (the scheduler UI will be
  static HTML + JS reading the JSON API and SSE).
- Server-side session storage (sessions are signed, stateless cookies).
- Changing the existing synchronous API.

## Anti-goals

- One slow, silent or malicious client stalling the server or others.
- A crashing handler leaking a traceback to the browser.
- Any request path reading a file outside the static folder.
- Passwords stored or logged in any recoverable form; secrets compared
  with `==`.
- Tests that wait for real seconds where a fake clock would do.

## Constraints

- Python 3.14, uv, ruff (repo rules), pyright strict, pytest ≥ 80 %,
  per-test timeout 10 s (`pytest-timeout`), conventional commits, PR into
  protected `main`; no `TYPE_CHECKING`, no `from __future__ import
  annotations`; files under ~400 lines.
- Runs on `pynet.loop`, never asyncio.
- Dependencies pinned by git tag, per the series' sibling-pinning
  convention: `pynet = { git = "https://github.com/robcox2910/pynet", tag = "v0.1.0" }`,
  `pycrypt = { git = "https://github.com/robcox2910/pycrypt", tag = "v0.1.0" }`.
  If a sibling needs a change, fix it there, release a tag, bump here.
- Crypto comes from pycrypt (`pycrypt.hmac.hmac_sign/hmac_verify`,
  `pycrypt.kdf.derive_key`); randomness from `secrets`; comparisons with
  `hmac.compare_digest`.

## Architecture

```
pynet.streams ─> AsyncServer ─(HttpRequest)─> App: middleware → router → handler
                     │                                         │
                     └── writes HttpResponse | EventStream | WebSocket upgrade
```

New modules (Claude's call; all new code under `src/pyweb/`):

| Module | Responsibility |
|---|---|
| `http1.py` | Pure HTTP/1.1 parsing/serialising on bytes: request head, headers (case-insensitive, repeated), target → path + query (percent-decoding), status line, chunked encoding |
| `messages.py` | `HttpRequest`, `HttpResponse`, `Headers`, cookie parsing/`set_cookie`, response helpers (`json`, `html`, `text`, `redirect`, `HTTPError`) |
| `routing.py` | `Route` table with typed params `<name>`, `<int:name>`, `<path:name>`; 404/405 |
| `app.py` | `App`: route decorators (`get/post/put/patch/delete/websocket`), middleware chain, error handling, sync/async/blocking handlers, `mount_static`, lifecycle hooks |
| `aserver.py` | `AsyncServer`: connections, keep-alive, limits, timeouts, max connections, graceful shutdown, response writing, SSE and WebSocket hand-off |
| `sse.py` | `ServerEvent`, `EventStream` response, `EventHub` broadcast with bounded per-subscriber queues |
| `security.py` | password hashing, `SessionSigner`, CSRF tokens, `LoginThrottle` |
| `auth.py` | `User`, `UserStore`/`InMemoryUsers`, `SessionAuth` middleware, `require(role=…)`, bearer tokens, login/logout helpers |
| `html.py` | `escape`, `html_template` (escape-by-default `{{ x }}`, raw `{{ x|safe }}`) |
| `astatic.py` | async static files: ETag, `If-None-Match` → 304, `Cache-Control`, traversal-safe |
| `testing.py` | `TestClient` (in-process) |

## Behaviour

### HTTP/1.1 and the server

- Request head ≤ 16 KiB (`431`), header count ≤ 100 (`431`), body ≤ 1 MiB
  by default (`413`), all configurable on `AsyncServer(limits=Limits(...))`.
- Body framing: `Content-Length` only. `Transfer-Encoding` on a request →
  `411` (and the connection closes). Both headers present → `400`.
- Timeouts (`Limits`): `header_timeout` 10 s (whole head must arrive —
  defeats slowloris), `body_timeout` 30 s, `keepalive_timeout` 5 s idle
  between requests, `write_timeout` 30 s per response. On timeout the
  connection is closed (a `408` is sent if nothing has been sent yet).
- Keep-alive is the HTTP/1.1 default; `Connection: close` or HTTP/1.0
  without `keep-alive` closes after the response. Requests on one
  connection are handled in order (no pipelining concurrency).
- `max_connections` (default 1 000): beyond it new connections get a
  `503` and close.
- Malformed requests → `400` and close. Unknown method → `501`.
- `HEAD` is answered like `GET` with the body dropped. `OPTIONS *` is not
  special.
- Every response gets `Date`, `Content-Length` (or `Transfer-Encoding:
  chunked` for streams), and `Server: PyWeb`.
- Graceful shutdown: `await server.close(grace=5.0)` stops accepting,
  lets in-flight requests finish within `grace`, ends SSE streams and
  closes WebSockets with code 1001, then closes everything.
- Logging: one access-log line per request (`method path status bytes
  ms`) on logger `pyweb.access`; errors on `pyweb.server`.

### The App

```python
app = App()

@app.get("/jobs/<int:job_id>")
async def show(request: HttpRequest, job_id: int) -> HttpResponse:
    return json({"id": job_id})

@app.get("/report", blocking=True)      # runs on a worker thread
def report(request: HttpRequest) -> HttpResponse: ...

@app.middleware
async def timing(request, call_next):
    response = await call_next(request)
    response.headers.set("X-Took-Ms", "3")
    return response
```

- Path params are passed as keyword arguments, converted by type
  (`int` failing to convert → 404). Trailing slash is significant.
- Handlers return `HttpResponse`, `EventStream`, a `dict`/`list` (→ JSON),
  or `str` (→ HTML). Anything else → 500.
- `raise HTTPError(status, message)` → that status with a small JSON or
  HTML body (by `Accept`). Any other exception → logged with traceback,
  `500` with a generic body; never a traceback to the client.
- Middleware run outermost-first in registration order; each may
  short-circuit by returning a response.
- `blocking=True` handlers run via `pynet.loop.run_in_thread`; ordinary
  `def` handlers run inline and the docs warn they must be quick.
- `@app.on_startup` / `@app.on_shutdown` async hooks.
- `HttpRequest`: `method`, `path`, `query` (multi-dict; `.get`, `.get_all`),
  `headers` (case-insensitive multi-dict), `body: bytes`, `text()`,
  `json()` (→ `400` on bad JSON), `form()` (urlencoded; → `415` for other
  types), `cookies`, `client` (peer address), `state` (dict for
  middleware), `user` (set by auth, else `None`).
- `HttpResponse`: `status`, `headers`, `body: bytes`, `set_cookie(name,
  value, *, max_age, path="/", http_only=True, same_site="Lax",
  secure=False)`, `delete_cookie(name)`.

### Server-Sent Events

- `EventStream(source)` where `source` is an async iterator of
  `ServerEvent(data, event=None, id=None, retry=None)`; written as
  `text/event-stream`, chunked, `Cache-Control: no-store`. Multi-line
  data becomes multiple `data:` lines. A `: keep-alive` comment every 15 s
  when idle.
- When the client disconnects the source iterator is closed (`aclose`).
- The request exposes `last_event_id` (from `Last-Event-ID`).
- `EventHub`: `publish(event)`, `subscribe(max_pending=100) -> AsyncIterator[ServerEvent]`;
  each subscriber has a bounded queue, drop-oldest, with a `dropped`
  count, so a slow browser never slows the publisher or other browsers.
  `close()` ends every subscription.

### WebSockets

- `@app.websocket("/ws")` handler `async def handler(ws: WebSocket,
  request: HttpRequest) -> None`; the server validates the upgrade and
  hands the connection to `pynet.ws.accept_websocket`. Middleware
  (including auth) run before the upgrade; a middleware response
  rejects it. When the handler returns, the socket is closed (1000);
  if it raises, closed with 1011 and logged.

### Security and auth

- **Passwords:** `hash_password(pw) -> str` in the format
  `pbkdf2_sha256$<rounds>$<salt hex>$<key hex>` via `pycrypt.kdf.derive_key`
  (default 100 000 rounds, 16-byte salt from `secrets`);
  `verify_password(pw, stored) -> bool` with constant-time compare;
  malformed stored strings → `False`. Tests may pass fewer rounds.
- **Sessions:** `SessionSigner(secret, *, max_age=8 h)`; `sign(data: dict)
  -> str` produces `base64url(json(data + issued_at)).hmac`;
  `unsign(token) -> dict | None` returns `None` for bad signature, bad
  encoding or expiry. Secrets shorter than 32 chars are refused.
  Cookie `session`, HttpOnly, SameSite=Lax, Secure configurable.
- **Users and roles:** `User(name, password_hash, roles: frozenset[str])`;
  `InMemoryUsers` implements `UserStore.get(name) -> User | None`.
  Roles are plain strings; `require(role)` checks membership (the
  scheduler will use `viewer`, `operator`, `admin`).
- **SessionAuth middleware:** reads the session cookie or an
  `Authorization: Bearer <token>` header (tokens are signed the same way
  with a `kind: "api"` claim), sets `request.user`. `require(...)`
  decorates a handler: no user → `401` (HTML requests: `303` to the
  login path); wrong role → `403`.
- **CSRF:** cookie-authenticated `POST/PUT/PATCH/DELETE` must send header
  `X-CSRF-Token` (or form field `csrf_token`) equal to the session's
  `csrf` claim (compared in constant time), else `403`. Bearer-token
  requests are exempt (no ambient credential).
- **Login helpers:** `login(response, user)` sets the cookie (new csrf
  token each login); `logout(response)` clears it. `LoginThrottle`: after
  5 failures for a username within 5 minutes, further attempts fail with
  `429` for 30 s (per username, in memory, using the loop clock).
- Never log passwords, tokens or cookie values.

### Templates and static files

- `html_template(text, context)`: `{{ name }}` → HTML-escaped value,
  `{{ name|safe }}` → raw; missing names → `KeyError` naming the blank
  (fail loudly rather than show `{{ x }}` to users).
- `app.mount_static(prefix, directory, *, max_age=3600)`: GET/HEAD only;
  `ETag` = quoted SHA-256 of the content (via pycrypt `sha256_bytes`),
  `If-None-Match` match → `304`; `Cache-Control: public, max-age=N`;
  files read on a worker thread; anything resolving outside the folder,
  dotfiles and symlinks out of the folder → `404`.

### TestClient

`TestClient(app)` with `get/post/put/patch/delete(path, *, json=None,
form=None, headers=None, cookies=None)` returning a response with
`status`, `headers`, `body`, `text`, `json()`; keeps a cookie jar so
login flows can be tested; runs middleware and handlers exactly as the
server would; `stream(path)` collects SSE events from an `EventStream`
up to a count. Async (`await client.get(...)`) — used inside
`pynet.loop.run`.

## Docs (kid-friendly, every concept)

New `docs/concepts/` pages (analogy, runnable snippet that was run, "What
We Test"): `async-server` (one waiter, many tables), `keep-alive` (staying
on the phone), `limits-and-timeouts` (the slow-talker at the counter),
`handlers-and-threads`, `middleware` (the security line at the airport),
`server-sent-events` (the radio station), `websockets-in-pyweb` (a phone
call instead of letters), `cookies-and-sessions` (the theme-park wristband
with a tamper-proof stamp), `passwords` (never keep the key, keep a
fingerprint), `csrf` (the forged permission slip), `roles` (backstage
passes), `escaping` (why a name can't be `<script>`), `caching` (the ETag:
"I already have that one"), `testing-apps`. `docs/index.md` gets a
"Part 2: A Busy Restaurant" learning path; README a section and a
"Developing alongside a sibling" note.

Examples: `async_hello.py`, `sse_clock.py`, `ws_echo_page.py`,
`login_demo.py` — each with `main()`; tests run them against port 0.

## PyStack integration

PyStack command `webdemo`: starts an `AsyncServer` on a free localhost
port with a tiny app (public page, login, protected JSON, SSE ticker),
then, using `pynet.http`, shows: a public GET, a protected GET → 401, a
login POST → cookie, the protected GET → 200 with JSON, and the first
three SSE events. Deterministic output; README/docs updated. PyStack
pins pyweb's new tag.

## Testing approach

- `http1` parser: table tests incl. every limit, bad framing, odd
  whitespace, percent-decoding, repeated headers.
- App/router/middleware/auth/CSRF/templates via `TestClient` with
  `FakeClock(autojump=True)` for throttling and session expiry.
- Server over real localhost sockets: concurrency (a slow handler does
  not delay another connection), keep-alive reuse, slowloris header
  timeout (fake or tiny real timeouts), limits, `max_connections` 503,
  graceful shutdown with an open SSE stream and WebSocket, client
  disconnect closes the SSE source.
- Security: traversal attempts, tampered/expired cookies, wrong-role,
  missing/wrong CSRF, password hash format and constant-time verify.

## Left to the builder

Internal structure within each module, log wording, exact chunking, and
how the plan divides tasks.
