# PyWeb Async Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give PyWeb a concurrent async web framework on PyNet's own event loop — HTTP/1.1 keep-alive server with limits, an `App` with middleware and typed routes, SSE and WebSockets, sessions/auth/CSRF, escaping templates, cached static files and a `TestClient` — keeping the old synchronous API, with kid-friendly docs and a PyStack demo.

**Architecture:** A pure byte-level HTTP/1.1 layer (`http1`), message types (`messages`), a router and `App` that turn an `HttpRequest` into a response through middleware, and an `AsyncServer` that drives connections on `pynet.loop` streams and hands upgrades to `pynet.ws`. Security primitives come from pycrypt.

**Tech Stack:** Python 3.14, `pynet.loop` / `pynet.streams` / `pynet.ws`, `pycrypt.hmac` / `pycrypt.kdf` / `pycrypt.hashing`, uv, ruff, pyright strict, pytest + pytest-cov + pytest-timeout.

**Spec:** `docs/superpowers/specs/2026-10-17-pyweb-async-design.md`

## Global Constraints

- Python `>=3.14`; never `TYPE_CHECKING`, never `from __future__ import annotations`.
- Every public module/class/function/method and every test class/method has a Google-style docstring in imperative mood (repo CLAUDE.md).
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright src tests` (strict) clean; `uv run pytest` passes with coverage ≥ 80 %; named constants instead of magic numbers in tests (PLR2004) — never `# noqa: PLR2004`.
- Per-test timeout 10 s: `pytest-timeout>=2.3.1` in dev deps, `"--timeout=10"` in pytest `addopts`.
- Conventional commits; each ends with a `Co-Authored-By:` trailer naming the model that wrote it.
- Runs on `pynet.loop`, never asyncio. Time-based logic uses the loop clock; tests use `pynet.loop.testing.FakeClock(autojump=True)` via `pynet.loop.run(main(), clock=clock)` wherever no real socket is involved. Real-socket tests keep every wait well under 2 s.
- Dependencies (git tags): `pynet = { git = "https://github.com/robcox2910/pynet", tag = "v0.1.0" }`, `pycrypt = { git = "https://github.com/robcox2910/pycrypt", tag = "v0.1.0" }` in `[tool.uv.sources]`; CI installs with `uv sync --locked --all-extras`.
- Existing `pyweb.server`, `router`, `request`, `response`, `template`, `static`, `cli` modules and their tests keep their current behaviour (the CLI may gain a flag in Task 11 but `pyweb` with no flag still works).
- Files under `src/pyweb/` stay under ~400 lines.
- Every docs snippet is actually run before committing and its shown output matches.
- Kid-friendly docs: each concept page opens with an everyday analogy, then a runnable snippet, then "What We Test".
- Defaults (spec, verbatim): head ≤ 16 KiB, ≤ 100 headers, body ≤ 1 MiB, `header_timeout` 10 s, `body_timeout` 30 s, `keepalive_timeout` 5 s, `write_timeout` 30 s, `max_connections` 1 000, SSE keep-alive comment every 15 s, `EventHub` `max_pending` 100, PBKDF2 100 000 rounds with 16-byte salt, session `max_age` 8 h, secret ≥ 32 chars, `LoginThrottle` 5 failures / 5 min → 30 s lockout, static `max_age` 3600, graceful `grace` 5.0 s.
- Never log passwords, tokens or cookie values.

## Review Focus

1. **A browser closes the tab mid-SSE**: the server notices, closes the source iterator (`aclose`), and an `EventHub` subscriber is removed — Task 6 test `test_client_disconnect_closes_source_and_unsubscribes`.
2. **One slow handler on connection A** must not delay a fast request on connection B — Task 5 test `test_slow_handler_does_not_block_other_connections`.
3. **Conflicting framing** (two different `Content-Length` values, or `Content-Length` with `Transfer-Encoding`) → `400`, never a smuggled second request — Task 1 tests.
4. **Encoded traversal** (`/static/%2e%2e/secret.txt`, `/static/..%2fsecret.txt`, `/static/.env`) → `404` — Task 8 test `test_traversal_variants_are_404`.
5. **A tampered or expired session cookie** → treated as anonymous (protected route answers 401/303), never a 500 — Task 10 test `test_tampered_and_expired_cookies_are_anonymous`.

---

## File Structure

```
src/pyweb/http1.py      Task 1   byte-level HTTP/1.1
src/pyweb/messages.py   Task 2   Headers, HttpRequest, HttpResponse, cookies, helpers, HTTPError
src/pyweb/routing.py    Task 3   typed routes
src/pyweb/app.py        Task 4   App, middleware, handler calling
src/pyweb/testing.py    Task 4   TestClient (stream() in Task 6)
src/pyweb/aserver.py    Task 5   AsyncServer (+ SSE writing Task 6, WS hand-off Task 7)
src/pyweb/sse.py        Task 6
src/pyweb/html.py       Task 8
src/pyweb/astatic.py    Task 8
src/pyweb/security.py   Task 9
src/pyweb/auth.py       Task 10
tests/aio/              one module per source module
```

---

### Task 1: Scaffolding and the HTTP/1.1 byte layer

**Files:** Modify `pyproject.toml`, `.github/workflows/ci.yml`; Create `src/pyweb/http1.py`, `tests/aio/__init__.py`, `tests/aio/test_http1.py`.

**Interfaces — Produces (`http1.py`):**
- `@dataclass(frozen=True) Limits(max_head_bytes: int = 16 * 1024, max_headers: int = 100, max_body_bytes: int = 1024 * 1024, header_timeout: float = 10.0, body_timeout: float = 30.0, keepalive_timeout: float = 5.0, write_timeout: float = 30.0, max_connections: int = 1_000)` — validated in `__post_init__` (`ValueError` for non-positive / non-finite).
- `class HttpParseError(Exception)` with attribute `status: int` (400, 411, 431, 501, 505).
- `@dataclass(frozen=True) RequestHead(method: str, target: str, version: str, headers: list[tuple[str, str]])`.
- `parse_head(raw: bytes, limits: Limits) -> RequestHead` — `raw` is everything up to and excluding the blank line. Rules: request line `METHOD SP target SP HTTP/1.x` (else 400; version not 1.0/1.1 → 505); method must be in `KNOWN_METHODS = {"GET","HEAD","POST","PUT","PATCH","DELETE","OPTIONS"}` (else 501); header lines `name: value` with a token name, no whitespace before the colon (400), obsolete line folding → 400; more than `max_headers` → 431; head must decode as latin-1.
- `body_length(head: RequestHead) -> int` — `Transfer-Encoding` present → `HttpParseError(411)`; multiple `Content-Length` values that differ, or a non-digit value → 400; with `Content-Length` and `Transfer-Encoding` both → 400 (checked before 411); absent → 0.
- `split_target(target: str) -> tuple[str, list[tuple[str, str]]]` — path percent-decoded (UTF-8, invalid → 400), `+` kept in path; query parsed with `urllib.parse.parse_qsl(keep_blank_values=True)`; target must start with `/` (else 400).
- `wants_keep_alive(head: RequestHead) -> bool` — HTTP/1.1 unless `Connection: close`; HTTP/1.0 only with `Connection: keep-alive` (token match, case-insensitive, comma lists).
- `encode_response_head(status: int, reason: str, headers: list[tuple[str, str]]) -> bytes`; `encode_chunk(data: bytes) -> bytes` (`b""` → terminating `0\r\n\r\n`); `STATUS_REASONS: dict[int, str]` covering every status the framework uses (200, 204, 301, 302, 303, 304, 400, 401, 403, 404, 405, 408, 411, 413, 415, 429, 431, 500, 501, 503, 505).

- [ ] **Step 1: Deps.** `[project.dependencies]` gains `"pynet"`, `"pycrypt"`; add the two git-tag `[tool.uv.sources]` entries from Global Constraints; dev extra gains `"pytest-timeout>=2.3.1"`; addopts gains `"--timeout=10"`. `uv lock && uv sync --all-extras`. CI: every install step becomes `uv sync --locked --all-extras` (if the security job runs `pip-audit`, keep it advisory).
- [ ] **Step 2: Failing tests** in `tests/aio/test_http1.py`: valid GET/POST heads; every rule above has a table row (incl. `"GET / HTTP/2.0"` → 505, `"BREW / HTTP/1.1"` → 501, `"Host : x"` → 400, folded header → 400, 101 headers → 431); `body_length` cases (`["5","5"]` → 5, `["5","6"]` → 400, `"-1"` → 400, TE+CL → 400, TE alone → 411); `split_target` (`"/a%20b?x=1&x=2&y="` → `("/a b", [("x","1"),("x","2"),("y","")])`, `"/%ff"` → 400, `"a"` → 400); keep-alive table; `encode_chunk(b"hi") == b"2\r\nhi\r\n"`, `encode_chunk(b"") == b"0\r\n\r\n"`; `Limits(max_body_bytes=0)` raises.
- [ ] **Step 3: Run** → FAIL. **Step 4: Implement.** **Step 5: Gates. Commit** `feat(aio): add HTTP/1.1 byte layer and dependencies`.

---

### Task 2: Messages — headers, requests, responses, cookies

**Files:** Create `src/pyweb/messages.py`, `tests/aio/test_messages.py`.

**Interfaces — Consumes:** Task 1 `STATUS_REASONS`. **Produces:**
- `class Headers` — case-insensitive multi-dict preserving order: `Headers(items: Iterable[tuple[str, str]] = ())`, `get(name, default=None) -> str | None` (first), `get_all(name) -> list[str]`, `set(name, value)` (replace all), `add(name, value)`, `remove(name)`, `__contains__`, `items() -> list[tuple[str, str]]`. Setting a value containing `\r` or `\n` raises `ValueError` (header injection).
- `class QueryParams` — multi-dict over `list[tuple[str, str]]`: `get`, `get_all`, `__contains__`, `items()`.
- `@dataclass HttpRequest(method: str, path: str, query: QueryParams, headers: Headers, body: bytes = b"", client: tuple[str, int] | None = None, path_params: dict[str, object] = {}, state: dict[str, object] = {}, user: object | None = None)` (use `field(default_factory=...)`); methods `text() -> str` (charset from Content-Type, default UTF-8, errors → `HTTPError(400)`), `json() -> JsonValue` (→ `HTTPError(400)`), `form() -> QueryParams` (urlencoded only; other content type → `HTTPError(415)`), properties `cookies -> dict[str, str]` (parse `Cookie` header; malformed pairs skipped), `last_event_id -> str | None`, `wants_html -> bool` (`text/html` in `Accept`).
- `@dataclass HttpResponse(status: int = 200, headers: Headers = Headers(), body: bytes = b"")` with `set_cookie(name, value, *, max_age: int | None = None, path: str = "/", http_only: bool = True, same_site: str = "Lax", secure: bool = False) -> None` (adds a `Set-Cookie`; name/value validated — `;`, `,`, whitespace, control chars → `ValueError`), `delete_cookie(name, path="/")` (`Max-Age=0`).
- Helpers: `json(data: JsonValue, status: int = 200) -> HttpResponse` (`application/json`), `html(text: str, status: int = 200)`, `text(text: str, status: int = 200)`, `redirect(location: str, status: int = 303)`, `empty(status: int = 204)`.
- `class HTTPError(Exception)`: `HTTPError(status: int, message: str | None = None)`; `to_response(request: HttpRequest) -> HttpResponse` → JSON `{"error": reason, "message": message}` or small HTML page by `wants_html`; message HTML-escaped.
- `type JsonValue` re-exported from `pynet.messaging`.

- [ ] **Step 1: Failing tests:** header case-insensitivity and order; injection rejected; cookies parse (`"a=1; b=2; bad; c="` → `{"a":"1","b":"2","c":""}`); `set_cookie` output exactly `session=abc; Path=/; Max-Age=60; HttpOnly; SameSite=Lax` for `max_age=60`; secure adds `; Secure`; bad cookie name raises; `text()` with `charset=latin-1`; `json()` bad → 400; `form()` with JSON content type → 415; `HTTPError(404, "<x>").to_response(html_req)` body contains `&lt;x&gt;`.
- [ ] **Step 2–4: Run → FAIL, implement, gates. Commit** `feat(aio): add request, response and header types`.

---

### Task 3: Typed routing

**Files:** Create `src/pyweb/routing.py`, `tests/aio/test_routing.py`.

**Interfaces — Produces:**
- `class RouteTable[T]` — generic over the stored value `T` (the App stores its handler records).
- `RouteTable[T].add(method: str, pattern: str, value: T) -> None` — pattern segments: literal, `<name>` (one segment, str), `<int:name>` (one segment, digits only, converted to `int`, leading `-` allowed), `<path:name>` (rest of the path, may contain `/`, must be last). Duplicate (method, pattern) → `ValueError`; bad pattern syntax → `ValueError`.
- `RouteTable[T].match(method: str, path: str) -> RouteMatch[T]`, where `@dataclass(frozen=True) RouteMatch[T](value: T | None, params: dict[str, object], allowed: frozenset[str])`: `value` set when found; if the path matches some route but not the method, `value is None` and `allowed` lists methods (→ 405); nothing matches → `value is None, allowed == frozenset()` (→ 404). `HEAD` matches a `GET` route when no explicit `HEAD` route exists. Literal routes win over parameter routes for the same path; otherwise first-added wins.
- `WEBSOCKET = "WEBSOCKET"` pseudo-method constant for upgrade routes.

- [ ] **Step 1: Failing tests:** literal vs param priority; `<int:id>` with `"42"`, `"-3"` (ints) and `"4x"` (no match → 404); `<path:rest>` matches `"a/b/c"`; 405 allowed set; HEAD→GET fallback; duplicates and `"/<path:x>/more"` raise.
- [ ] **Step 2–4. Commit** `feat(aio): add typed route table`.

---

### Task 4: The App, middleware and the TestClient

**Files:** Create `src/pyweb/app.py`, `src/pyweb/testing.py`, `docs/concepts/handlers-and-threads.md`, `docs/concepts/middleware.md`, `docs/concepts/testing-apps.md`; Tests `tests/aio/test_app.py`, `tests/aio/test_testing.py`.

**Interfaces — Consumes:** Tasks 2–3. **Produces:**
- In `messages.py` (add it there in this task): `class StreamingResponse(Protocol)` with `status: int` and `headers: Headers` — Task 6's `EventStream` implements it; `type Outcome = HttpResponse | StreamingResponse`.
- In `app.py`: `type CallNext = Callable[[HttpRequest], Awaitable[Outcome]]`, `type Middleware = Callable[[HttpRequest, CallNext], Awaitable[Outcome]]`.
- `class App`:
  - `route(method: str, pattern: str, *, blocking: bool = False)` decorator; shorthands `get/post/put/patch/delete(pattern, *, blocking=False)`; `websocket(pattern)` (Task 7 consumes it; here it only registers under `WEBSOCKET`).
  - `middleware(fn: Middleware) -> Middleware` (decorator; order = registration order, first is outermost).
  - `on_startup(fn)`, `on_shutdown(fn)`; `async startup()`, `async shutdown()` run them in order (shutdown in reverse).
  - `async handle(request: HttpRequest) -> Outcome` — the whole pipeline: middleware → route match (404/405 via `HTTPError`; 405 response gets an `Allow` header) → call handler with `request` plus path params as kwargs → coerce result (`HttpResponse`/`StreamingResponse` as-is; `dict`/`list` → `json`; `str` → `html`; anything else → logged, 500). `HTTPError` anywhere → `to_response`; any other `Exception` → logged on `pyweb.app` with traceback, generic 500 (`{"error": "Internal Server Error"}` / HTML). HEAD: body emptied after handling (headers kept).
  - Handler kinds: `async def` awaited; plain `def` called inline; `blocking=True` (plain `def` only, else `ValueError` at registration) runs via `pynet.loop.run_in_thread`.
  - `websocket_route(path) -> tuple[handler, params] | None` for Task 7.
- `testing.py`: `class TestClient(app: App)` with `async get/post/put/patch/delete/head(path: str, *, query: Mapping[str,str] | None = None, json: JsonValue | None = None, form: Mapping[str,str] | None = None, body: bytes | None = None, headers: Mapping[str,str] | None = None) -> TestResponse`; `TestResponse(status, headers: Headers, body: bytes)` with `text`, `json()`; a cookie jar updated from `Set-Cookie` (respecting `Max-Age=0` deletion) and sent on later requests; `client=("testclient", 0)`. It builds `HttpRequest` exactly as the server will (path/query split via `http1.split_target`).

- [ ] **Step 1: Failing tests** (inside `pynet.loop.run`): async and sync handlers; `blocking=True` handler runs off the loop thread (assert `threading.current_thread()` differs) and others keep being served meanwhile; `<int:id>` kwarg type; return coercion for dict/str/bad type (500 logged); `HTTPError(418? no — use 409)` → status; unhandled exception → 500 with no traceback text in body and a log record; middleware order (two middlewares append to a list) and short-circuit; 405 has `Allow: GET, HEAD`; HEAD returns empty body with GET's headers; startup/shutdown hook order; TestClient cookie jar round trip.
- [ ] **Step 2–3: Run → FAIL, implement.** **Step 4: Docs** (three pages; `handlers-and-threads` must warn that plain `def` handlers block the whole loop while they run). **Step 5: Gates. Commit** `feat(aio): add App, middleware and TestClient`.

---

### Task 5: AsyncServer — connections, keep-alive, limits, shutdown

**Files:** Create `src/pyweb/aserver.py`, `docs/concepts/async-server.md`, `docs/concepts/keep-alive.md`, `docs/concepts/limits-and-timeouts.md`; Test `tests/aio/test_aserver.py`.

**Interfaces — Consumes:** Tasks 1, 2, 4; `pynet.streams.start_server(handler, host, port) -> Server` (handler gets `(reader, writer)`), `StreamReader.readuntil`/`readexactly` (check pynet's names), `pynet.loop.timeout`, `Semaphore`. **Produces:**
- `class AsyncServer(app: App, *, limits: Limits = Limits(), access_log: bool = True)`: `async start(host: str = "127.0.0.1", port: int = 0) -> None` (runs `app.startup()`), `port` property, `connection_count`, `async close(grace: float = 5.0) -> None` (stop accepting; wait up to `grace` for in-flight requests; then cancel the rest, close streams, run `app.shutdown()`); `async with` support; `async serve_forever()` (until cancelled).
- Per connection: loop { read head within `header_timeout` (timeout on an *idle* keep-alive connection uses `keepalive_timeout` and closes silently; mid-head → 408); `parse_head`; body via `body_length` within `body_timeout` (too large → 413 *before* reading it); build `HttpRequest`; `await app.handle()`; write response within `write_timeout`; access log `"{method} {path} {status} {bytes} {ms:.1f}ms"`; close unless keep-alive }.
- Errors: `HttpParseError` → its status (JSON/HTML via `HTTPError`) then close; connections beyond `max_connections` get `503` and close; `Date`, `Server: PyWeb`, `Content-Length` added to every buffered response; `Connection: close` header sent when closing.
- Streaming outcomes (Task 6) and upgrades (Task 7) go through two private hooks `_write_stream(...)` / `_upgrade(...)` that this task leaves raising `NotImplementedError` → 500.

- [ ] **Step 1: Failing tests** (real sockets via `pynet.streams.open_connection`, raw bytes):
  - round trip GET and POST with body; two requests on one keep-alive connection; `Connection: close` honoured; HTTP/1.0 closes by default.
  - `test_slow_handler_does_not_block_other_connections` (Review Focus 2): handler A awaits an `Event`; B's request on another connection completes first.
  - header timeout (Limits(header_timeout=0.2)): send `GET / HTTP/1.1\r\n` and stall → `408` and close within ~0.5 s; idle keep-alive timeout closes without a response.
  - 413 for a too-big Content-Length (sent without the body), 431, 400 conflicting framing, 411 for TE, 501, 505.
  - `max_connections=2`: third connection gets 503.
  - graceful `close(grace=0.5)` lets an in-flight slow request finish; one longer than grace is cut off; startup/shutdown hooks ran.
  - access log record format (caplog).
- [ ] **Step 2–3. Step 4: Docs** (three pages). **Step 5: Gates (loop the socket tests 5×). Commit** `feat(aio): add async HTTP/1.1 server`.

---

### Task 6: Server-Sent Events

**Files:** Create `src/pyweb/sse.py`, `docs/concepts/server-sent-events.md`; Modify `src/pyweb/aserver.py` (`_write_stream`), `src/pyweb/testing.py` (`stream`); Test `tests/aio/test_sse.py`.

**Interfaces — Produces:**
- `@dataclass(frozen=True) ServerEvent(data: str, event: str | None = None, id: str | None = None, retry: int | None = None)`; `encode_event(e) -> bytes` (each data line `data: …`, `\r`/`\n` in `event`/`id` → `ValueError`, ends with blank line).
- `class EventStream(source: AsyncIterator[ServerEvent], *, keepalive: float = 15.0, status: int = 200, headers: Headers | None = None)` implementing Task 4's `StreamingResponse`; headers `Content-Type: text/event-stream`, `Cache-Control: no-store`, `Transfer-Encoding: chunked`.
- `class EventHub`: `publish(event: ServerEvent) -> int` (subscribers reached), `subscribe(*, max_pending: int = 100) -> Subscription` (an async iterator with `.dropped`), `subscriber_count`, `close()` (ends every subscription's iteration).
- Server `_write_stream`: writes the head, then each event as a chunk; a `: keep-alive\n\n` comment chunk after `keepalive` seconds idle; detects client disconnect (write error or EOF on the reader — watch the reader concurrently) and then `aclose()`s the source; on graceful shutdown ends the stream with the terminating chunk; the connection is not reused after a stream.
- `TestClient.stream(path, *, count: int, headers=None) -> list[ServerEvent]` — runs the handler, takes `count` events, then closes the source.

- [ ] **Step 1: Failing tests:** encoding (multi-line data, retry, id); hub fan-out, drop-oldest with `dropped` counted while a subscriber is slow, `close()` ends iteration; over a real socket: chunked framing parsed by a tiny test reader, keep-alive comment with `keepalive=0.1`, `test_client_disconnect_closes_source_and_unsubscribes` (Review Focus 1: hub subscriber count returns to 0), graceful close ends an open stream; `last_event_id` reaches the handler; TestClient.stream.
- [ ] **Step 2–3. Step 4: Docs** (the radio station; hub; why slow listeners get the newest news). **Step 5: Gates. Commit** `feat(aio): add server-sent events and event hub`.

---

### Task 7: WebSockets

**Files:** Modify `src/pyweb/app.py` (websocket registration complete), `src/pyweb/aserver.py` (`_upgrade`); Create `docs/concepts/websockets-in-pyweb.md`; Test `tests/aio/test_websocket.py`.

**Interfaces — Consumes:** `pynet.ws.accept_websocket(reader, writer, *, method, headers, max_message_size=...) -> WebSocket`, `WebSocket.send_text/send_bytes/recv/close`, `pynet.ws.connect_websocket` for tests (read signatures in pynet). **Produces:** `@app.websocket(pattern)` handlers `async def h(ws: WebSocket, request: HttpRequest, **params) -> None`.

Behaviour: a request with `Upgrade: websocket` on a WEBSOCKET route runs the middleware chain with a request whose method is `GET`; if middleware returns a response, that response is sent and the connection closes; otherwise `accept_websocket` is called (it answers 400 itself for a bad handshake) and the handler runs; return → `close(1000)`; exception → logged, `close(1011)`; graceful server close → `close(1001)`. A normal GET to a WEBSOCKET-only path → 426 `Upgrade Required` (add 426 to `STATUS_REASONS`).

- [ ] **Step 1: Failing tests:** echo round trip (text and bytes) with path param; middleware rejection (401) before upgrade; handler exception → client sees close code 1011; server close → 1001; plain GET → 426.
- [ ] **Step 2–3. Step 4: Docs. Step 5: Gates. Commit** `feat(aio): add WebSocket routes`.

---

### Task 8: Escaping templates and cached static files

**Files:** Create `src/pyweb/html.py`, `src/pyweb/astatic.py`, `docs/concepts/escaping.md`, `docs/concepts/caching.md`; Modify `src/pyweb/app.py` (`mount_static`); Tests `tests/aio/test_html.py`, `tests/aio/test_astatic.py`.

**Interfaces — Produces:**
- `escape(value: object) -> str` (`& < > " '` → entities); `html_template(text: str, context: Mapping[str, object]) -> str` — `{{ name }}` escaped, `{{ name|safe }}` raw, whitespace inside braces optional, unknown filter → `ValueError`, missing name → `KeyError(name)`; `load_template(path: str | Path) -> str`.
- `App.mount_static(prefix: str, directory: str | Path, *, max_age: int = 3600) -> None` registers GET (and HEAD via fallback) `prefix + "/<path:file>"`.
- `astatic.static_handler(directory, *, max_age) -> handler`: resolves the decoded path under the real directory (`Path.resolve()` + `is_relative_to`), rejects any segment starting with `.`, non-files → 404; reads bytes on a worker thread (`run_in_thread`); `ETag: "<sha256 hex>"` using `pycrypt.hashing.sha256_bytes`; `If-None-Match` (incl. `*` and lists) → 304 with no body; `Cache-Control: public, max-age=N`; content type from the existing `pyweb.static.MIME_TYPES`/`guess_mime_type` (reuse, don't copy).

- [ ] **Step 1: Failing tests:** escaping table incl. `<script>` and quotes; `|safe`; missing name; static 200 with ETag + Cache-Control; 304 on matching If-None-Match; `test_traversal_variants_are_404` (Review Focus 4: `/static/%2e%2e/secret.txt`, `/static/..%2fsecret.txt`, `/static/.env`, `/static/sub/../../secret.txt`, a symlink pointing outside); binary file bytes intact; HEAD has headers, no body.
- [ ] **Step 2–3. Step 4: Docs** (two pages). **Step 5: Gates. Commit** `feat(aio): add escaping templates and cached static files`.

---

### Task 9: Security primitives

**Files:** Create `src/pyweb/security.py`, `docs/concepts/passwords.md`, `docs/concepts/cookies-and-sessions.md`; Test `tests/aio/test_security.py`.

**Interfaces — Produces:**
- `hash_password(password: str, *, rounds: int = 100_000) -> str` → `"pbkdf2_sha256$<rounds>$<salt hex>$<key hex>"` using `pycrypt.kdf.derive_key(password, salt=secrets.token_bytes(16), rounds=rounds)`; `verify_password(password: str, stored: str) -> bool` (re-derive with stored rounds+salt, `hmac.compare_digest`; any malformed input → `False`, never raises).
- `class SessionSigner(secret: str, *, max_age: float = 8 * 3600, clock: Callable[[], float] = time.time)`: secret < 32 chars → `ValueError`; `sign(data: Mapping[str, JsonValue]) -> str` → `base64url(json({**data, "iat": now}))` + `"."` + `pycrypt.hmac.hmac_sign(payload, secret)`; `unsign(token: str) -> dict[str, JsonValue] | None` — `None` for bad shape, bad signature (via `pycrypt.hmac.hmac_verify`), bad base64/JSON, non-object, missing/non-number `iat`, or `now - iat > max_age` (also `iat` more than 60 s in the future). The constructor takes `clock: Callable[[], float] = time.time` — wall-clock by default because cookies outlive processes; tests inject a fake.
- `new_csrf_token() -> str` (`secrets.token_urlsafe(32)`); `csrf_matches(expected: str, given: str | None) -> bool` (constant-time; `None`/empty → False).
- `class LoginThrottle(*, max_failures: int = 5, window: float = 300.0, lockout: float = 30.0, clock: Callable[[], float] = time.monotonic)`: `locked(username) -> bool`, `record_failure(username)`, `record_success(username)` (clears).

- [ ] **Step 1: Failing tests:** hash format and round trip (use `rounds=1_000` in tests); wrong password False; malformed stored strings (`""`, `"md5$x"`, `"pbkdf2_sha256$x$y$z"`) False; two hashes of the same password differ (salt); signer round trip; one changed character in payload or signature → None; expiry with an injected clock; short secret raises; csrf compare; throttle lockout after 5 failures within window, unlock after 30 s, failures outside window don't count, success clears.
- [ ] **Step 2–3. Step 4: Docs** (passwords: fingerprints and salt, why slow; cookies-and-sessions: theme-park wristband with a tamper-proof stamp, expiry, HttpOnly). **Step 5: Gates. Commit** `feat(aio): add password hashing, signed sessions and throttling`.

---

### Task 10: Auth middleware, roles and CSRF

**Files:** Create `src/pyweb/auth.py`, `docs/concepts/csrf.md`, `docs/concepts/roles.md`; Test `tests/aio/test_auth.py`.

**Interfaces — Consumes:** Tasks 4, 9. **Produces:**
- `@dataclass(frozen=True) User(name: str, password_hash: str, roles: frozenset[str] = frozenset())`; `class UserStore(Protocol)`: `get(name: str) -> User | None`; `class InMemoryUsers(UserStore)` with `add(name, password, roles=(), *, rounds=100_000) -> User`.
- `class SessionAuth(signer: SessionSigner, users: UserStore, *, cookie_name: str = "session", login_path: str = "/login", secure_cookies: bool = False)` — usable as middleware (`app.middleware(auth)` — implement `__call__(request, call_next)`): sets `request.user` from the cookie (claim `kind == "session"`) or `Authorization: Bearer` (claim `kind == "api"`); unknown user or bad token → anonymous; CSRF: if authenticated by cookie and method in `{POST, PUT, PATCH, DELETE}`, require `csrf_matches(claims["csrf"], header X-CSRF-Token or form field csrf_token)` else `HTTPError(403, "csrf")`. Methods: `login(response: HttpResponse, user: User) -> str` (sets cookie with fresh csrf; returns the csrf token), `logout(response)`, `api_token(user: User) -> str`, `csrf_token(request) -> str | None`.
- `require(*roles: str)` decorator for handlers (sync or async): anonymous → `HTTPError(401)` or, when `request.wants_html`, `redirect(login_path + "?next=" + quoted path)`; missing any listed role → `HTTPError(403)`. `require()` with no roles = any logged-in user.
- `authenticate(users: UserStore, throttle: LoginThrottle, name: str, password: str) -> User | None` — locked → `HTTPError(429)`; records failure/success.

- [ ] **Step 1: Failing tests** via `TestClient`: login sets HttpOnly cookie; protected JSON 401 when anonymous, 303 to `/login?next=%2Fjobs` for HTML; role 403; bearer token works and is CSRF-exempt; cookie POST without/with wrong/with right CSRF → 403/403/200 (header and form field); `test_tampered_and_expired_cookies_are_anonymous` (Review Focus 5, injected clock); throttle 429 after 5 bad logins; logout clears; no password or token appears in any log record (caplog over the whole test).
- [ ] **Step 2–3. Step 4: Docs** (forged permission slip; backstage passes). **Step 5: Gates. Commit** `feat(aio): add session auth, roles and CSRF protection`.

---

### Task 11: CLI, examples, learning path, README

**Files:** Modify `src/pyweb/cli.py` (add `--async` flag: serves a demo `App` on `AsyncServer` with a page, an SSE clock and a WS echo; no flag = old behaviour), `docs/index.md`, `README.md`; Create `examples/async_hello.py`, `examples/sse_clock.py`, `examples/ws_echo_page.py`, `examples/login_demo.py`, `tests/test_examples.py` (or extend the existing one).

- Each example exposes `main()` that starts on port 0, exercises itself with a client (raw `pynet.streams` / `pynet.ws.connect_websocket` / `TestClient`), prints what it shows, and finishes in < 3 s; tests run each `main()` and assert a key line.
- `docs/index.md`: keep the existing text; add "Part 2: A Busy Restaurant" linking, in order: async-server, keep-alive, limits-and-timeouts, handlers-and-threads, middleware, testing-apps, server-sent-events, websockets-in-pyweb, escaping, caching, passwords, cookies-and-sessions, csrf, roles.
- `README.md`: an "Async PyWeb" section with a short `App` + `AsyncServer` snippet that runs; how to run `pyweb --async`; a "Developing alongside a sibling" note (`uv pip install -e ../pynet` then `uv run --no-sync …`). Keep the existing Releasing section if present (don't duplicate).
- [ ] Steps: failing example tests → implement → run every snippet/example → gates → **Commit** `feat(aio): add async CLI demo, examples and learning path`.

---

### Task 12: PyStack `webdemo`

**Files (in a PyStack worktree on a new branch — never the user's checkout):** Modify `src/pystack/plugins/web_plugin.py`, its tests, `README.md`, `docs/index.md`. Develop against the new pyweb with `uv pip install -e ../pyweb` + `uv run --no-sync`; the controller bumps PyStack's pyweb tag after release.

**Produces:** shell command `webdemo` — starts an `AsyncServer` on a free localhost port with: `GET /` public page, `POST /login` (form), `GET /api/me` protected JSON, `GET /ticks` SSE (three numbered events); then with a small raw `pynet.streams` HTTP client prints, in order: public GET status, `/api/me` → 401, login → `Set-Cookie: session=…` (value elided), `/api/me` with the cookie → 200 + JSON user, and the three SSE events. Deterministic, traceback-free, < 3 s.

- [ ] Steps: failing test asserting each key line → implement → 5 identical runs → gates → **Commit** `feat(web): add webdemo for PyWeb's async server`.
