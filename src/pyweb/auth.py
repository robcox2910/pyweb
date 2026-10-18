"""Auth: who is visiting, what they may do, and forged-form protection.

Think of a concert. At the gate you show your ticket once and get a
wristband (the *session cookie*). Some doors need a **backstage pass**
as well (a *role*). And when a note arrives saying "Ada says let my
friend in", the guard checks it carries Ada's secret stamp (the *CSRF
token*) -- otherwise anyone could write that note.

* ``SessionAuth`` is middleware: it reads the wristband (or an API token
  in ``Authorization: Bearer``) and sets ``request.user``. For cookie
  visitors it also checks the CSRF token on POST, PUT, PATCH and DELETE.
* ``require(...)`` guards one handler: strangers get ``401`` (or a trip
  to the login page in a browser) and users without a role get ``403``.
* ``authenticate`` checks a name and password, with a ``LoginThrottle``
  so guessers are slowed down.

Nothing here ever logs a password, a token, a cookie value or a CSRF token.
"""

import functools
import inspect
import logging
import weakref
from collections.abc import Awaitable, Callable, Iterable
from typing import cast
from urllib.parse import quote

from pyweb._handlers import is_registered
from pyweb._users import InMemoryUsers, User, UserStore, authenticate, current_user
from pyweb.app import CallNext, WebSocketUpgrade
from pyweb.messages import HTTPError, HttpRequest, HttpResponse, JsonValue, Outcome, redirect
from pyweb.security import SessionSigner, csrf_matches, new_csrf_token

__all__ = [
    "InMemoryUsers",
    "SessionAuth",
    "User",
    "UserStore",
    "authenticate",
    "current_user",
    "require",
    "safe_next",
]

logger = logging.getLogger("pyweb.auth")

_UNAUTHORIZED = 401
_FORBIDDEN = 403
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CSRF_HEADER = "X-CSRF-Token"
_CSRF_FIELD = "csrf_token"
_LOGIN_PATH_KEY = "pyweb.auth.login_path"
_DEFAULT_LOGIN_PATH = "/login"
_SESSION_KIND = "session"
_API_KIND = "api"
_MAX_UNWRAP = 100
_WEB_SCHEMES = frozenset({"http", "https"})


def _normal_origin(origin: str) -> str:
    """Return an origin trimmed and lower-cased, for comparing."""
    return origin.strip().lower().rstrip("/")


def safe_next(target: str | None) -> str:
    """Return ``target`` if it is a path on this site, else ``"/"``.

    Use it before redirecting to a ``?next=`` value, so a crafted link
    cannot bounce a visitor to someone else's site after login. Control
    characters (browsers quietly drop tabs and newlines, so ``/<tab>/evil``
    would become ``//evil``) and backslashes anywhere are refused too.
    """
    if not target or not target.startswith("/") or target[1:2] == "/":
        return "/"
    if any(ch < " " or ch in {"\x7f", "\\"} for ch in target):
        return "/"
    return target


# -- roles -----------------------------------------------------------------

_requirements: weakref.WeakKeyDictionary[Callable[..., object], frozenset[str]] = (
    weakref.WeakKeyDictionary()
)


def _required_roles(handler: object) -> frozenset[str] | None:
    """Return every role ``require`` put on a handler or anything it wraps.

    ``None`` means no ``require`` was found at all (anyone may call it).
    """
    found: frozenset[str] | None = None
    for _ in range(_MAX_UNWRAP):
        if not callable(handler):
            break
        try:
            roles = _requirements.get(handler)
        except TypeError:  # Some callables cannot be weakly referenced.
            roles = None
        if roles is not None:
            found = roles if found is None else found | roles
        handler = getattr(handler, "__wrapped__", None)
    return found


def _check(request: HttpRequest, roles: frozenset[str], *, allow_redirect: bool) -> Outcome | None:
    """Return None if the visitor may pass, a login redirect, or raise.

    Raises:
        HTTPError: ``401`` for a stranger, ``403`` for a missing role.

    """
    user = current_user(request)
    if user is None:
        if allow_redirect and request.wants_html:
            login = request.state.get(_LOGIN_PATH_KEY)
            login_path = login if isinstance(login, str) else _DEFAULT_LOGIN_PATH
            return redirect(f"{login_path}?next={quote(request.path, safe='')}")
        raise HTTPError(_UNAUTHORIZED)
    if not roles <= user.roles:
        raise HTTPError(_FORBIDDEN)
    return None


def _gate(args: tuple[object, ...], roles: frozenset[str]) -> Outcome | None:
    """Find the request among a handler's arguments and check it.

    The request comes first for an HTTP handler; a WebSocket handler is
    called as ``(ws, request)`` and must never be answered with a redirect.

    Raises:
        TypeError: If no argument is an ``HttpRequest``.

    """
    for index, arg in enumerate(args):
        if isinstance(arg, HttpRequest):
            return _check(arg, roles, allow_redirect=index == 0)
    msg = "require() needs a handler that is passed the HttpRequest"
    raise TypeError(msg)


def require[F: Callable[..., object]](*roles: str) -> Callable[[F], F]:
    """Return a decorator that lets only logged-in users (with ``roles``) in.

    Put it *under* the route decorator::

        @app.get("/admin")
        @require("admin")
        def admin(request): ...

    A stranger gets ``401``, or a ``303`` to the login page with
    ``?next=`` when the client wants HTML. A user missing any listed role
    gets ``403``. ``require()`` with no roles lets in any logged-in user.

    The wrapper is ``async def`` exactly when the handler is, so a plain
    ``def`` handler can still use ``blocking=True``. On a WebSocket route
    the ``SessionAuth`` middleware does the check *before* the handshake;
    without that middleware the wrapped handler raises instead (and the
    WebSocket is closed with 1011), so it fails closed.

    Raises:
        TypeError: When the decorated function is already registered as a
            route -- ``@require`` was put *above* the route decorator, so
            the App holds the unguarded function and the page would be
            public.

    """
    needed = frozenset(roles)

    def decorate(fn: F) -> F:
        """Wrap ``fn`` with the check, keeping its name and signature."""
        if is_registered(fn):
            msg = "put @require below the route decorator (closer to the function)"
            raise TypeError(msg)
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_guard(*args: object, **kwargs: object) -> object:
                """Check the visitor, then await the handler."""
                denied = _gate(args, needed)
                if denied is not None:
                    return denied
                return await cast("Awaitable[object]", fn(*args, **kwargs))

            guard: Callable[..., object] = async_guard
        else:

            @functools.wraps(fn)
            def sync_guard(*args: object, **kwargs: object) -> object:
                """Check the visitor, then call the handler."""
                denied = _gate(args, needed)
                return denied if denied is not None else fn(*args, **kwargs)

            guard = sync_guard
        _requirements[guard] = needed
        return cast("F", guard)

    return decorate


# -- the middleware --------------------------------------------------------


def _bearer_token(request: HttpRequest) -> str | None:
    """Return the token from ``Authorization: Bearer``, or None if not sent."""
    header = request.headers.get("Authorization")
    if header is None:
        return None
    scheme, _, token = header.strip().partition(" ")
    return token.strip() if scheme.lower() == "bearer" else None


def _form_token(request: HttpRequest) -> str | None:
    """Return the ``csrf_token`` form field of an urlencoded body, or None."""
    try:
        form = request.form()  # Only parses urlencoded bodies; others raise 415.
    except HTTPError:
        return None
    return form.get(_CSRF_FIELD)


class SessionAuth:
    """Middleware that works out ``request.user`` and checks CSRF tokens."""

    def __init__(
        self,
        signer: SessionSigner,
        users: UserStore,
        *,
        cookie_name: str = "session",
        login_path: str = _DEFAULT_LOGIN_PATH,
        secure_cookies: bool = False,
        allowed_origins: Iterable[str] = (),
        bearer_verifier: Callable[[str], User | None] | None = None,
    ) -> None:
        """Create the middleware.

        Args:
            signer: Signs and checks session cookies and API tokens.
            users: Where to look users up by name.
            cookie_name: The session cookie's name.
            login_path: Where ``require`` sends browsers that must log in.
            secure_cookies: Mark the cookie ``Secure`` (use it on HTTPS).
            allowed_origins: Other sites (exact ``"http(s)://host:port"``
                strings) whose pages may open a WebSocket with the
                visitor's cookie. The page's own site is always allowed.
            bearer_verifier: Your own check for ``Authorization: Bearer``
                tokens: given the token text, return the ``User`` it
                names, or None. It is tried first; on None the signed
                API token check runs. If it raises, the error is logged
                (never the token) and the visitor is anonymous.

        """
        self._bearer_verifier = bearer_verifier
        self._signer = signer
        self._users = users
        self._cookie_name = cookie_name
        self._login_path = login_path
        self._secure = secure_cookies
        self._allowed_origins = frozenset(_normal_origin(origin) for origin in allowed_origins)

    async def __call__(self, request: HttpRequest, call_next: CallNext) -> Outcome:
        """Set ``request.user``, check CSRF, and guard WebSocket upgrades.

        Raises:
            HTTPError: ``403`` ("csrf") for a cookie visitor's unsafe request
                without the right token; ``403`` ("origin") for a cookie
                visitor's upgrade from another site; ``401``/``403`` for an
                upgrade to a ``require``-guarded WebSocket route.

        """
        request.state[_LOGIN_PATH_KEY] = self._login_path
        user, claims = self._identify(request)
        request.user = user
        if claims is not None and request.method in _UNSAFE_METHODS:
            self._check_csrf(request, claims)
        outcome = await call_next(request)
        if isinstance(outcome, WebSocketUpgrade):
            # Upgrades must be refused before the handshake, so check here.
            # The CSRF token cannot ride on a WebSocket handshake, so a
            # cookie upgrade must come from a page on an allowed site.
            if claims is not None and not self._origin_allowed(request):
                logger.info("WebSocket upgrade refused: Origin not allowed for %s", request.path)
                raise HTTPError(_FORBIDDEN, "origin")
            roles = _required_roles(outcome.handler)
            if roles is not None:
                _check(outcome.request, roles, allow_redirect=False)
        return outcome

    def _origin_allowed(self, request: HttpRequest) -> bool:
        """Return whether the ``Origin`` is this site or an allowed one.

        "This site" means the Origin without its ``http://`` or
        ``https://`` equals the ``Host`` header. A missing Origin is
        refused: browsers always send one on a WebSocket handshake.
        """
        origin = request.headers.get("Origin")
        if origin is None:
            return False
        origin = _normal_origin(origin)
        if origin in self._allowed_origins:
            return True
        scheme, sep, host_port = origin.partition("://")
        host = (request.headers.get("Host") or "").strip().lower()
        return bool(sep) and scheme in _WEB_SCHEMES and bool(host) and host_port == host

    def _identify(self, request: HttpRequest) -> tuple[User | None, dict[str, JsonValue] | None]:
        """Return the user, plus the session claims if they came from the cookie.

        A ``Bearer`` header, when sent, is the only thing looked at: a bad
        one leaves the visitor anonymous rather than falling back to the
        cookie.
        """
        bearer = _bearer_token(request)
        if bearer is not None:
            try:
                user = self._verify_bearer(bearer)
            except Exception:
                logger.exception("Bearer verifier raised; treating the visitor as anonymous")
                return None, None
            if user is None:
                user, _ = self._user_from(bearer, _API_KIND)
            if user is None:
                logger.debug("Bearer token rejected")
            return user, None
        token = request.cookies.get(self._cookie_name)
        if token is None:
            return None, None
        user, claims = self._user_from(token, _SESSION_KIND)
        if user is None:
            logger.debug("Session cookie rejected")
            return None, None
        return user, claims

    def _verify_bearer(self, token: str) -> User | None:
        """Return the user the custom verifier names, or None if unset or unknown."""
        return None if self._bearer_verifier is None else self._bearer_verifier(token)

    def _user_from(self, token: str, kind: str) -> tuple[User | None, dict[str, JsonValue]]:
        """Return the user a token of this kind names (or None) and its claims."""
        claims = self._signer.unsign(token)
        if claims is None or claims.get("kind") != kind:
            return None, {}
        name = claims.get("user")
        if not isinstance(name, str):
            return None, {}
        return self._users.get(name), claims

    @staticmethod
    def _check_csrf(request: HttpRequest, claims: dict[str, JsonValue]) -> None:
        """Raise 403 unless the request carries the session's CSRF token.

        The ``X-CSRF-Token`` header wins when present; otherwise an
        urlencoded form's ``csrf_token`` field is used. Other bodies are
        never parsed for it.
        """
        expected = claims.get("csrf")
        given = request.headers.get(_CSRF_HEADER)
        if given is None:
            given = _form_token(request)
        if not isinstance(expected, str) or not csrf_matches(expected, given):
            logger.debug("CSRF check failed for %s %s", request.method, request.path)
            raise HTTPError(_FORBIDDEN, "csrf")

    def login(self, response: HttpResponse, user: User) -> str:
        """Put a fresh session cookie on the response and return its CSRF token.

        Put the returned token in your forms (field ``csrf_token``) or send
        it as the ``X-CSRF-Token`` header.
        """
        csrf = new_csrf_token()
        token = self._signer.sign({"kind": _SESSION_KIND, "user": user.name, "csrf": csrf})
        response.set_cookie(self._cookie_name, token, http_only=True, secure=self._secure)
        return csrf

    def logout(self, response: HttpResponse) -> None:
        """Ask the browser to forget the session cookie.

        The old token itself still verifies until it expires: sessions are
        stamped, not stored, so there is no list to cross it off.
        """
        response.set_cookie(self._cookie_name, "", max_age=0, secure=self._secure)

    def api_token(self, user: User) -> str:
        """Return a token for ``Authorization: Bearer`` (no CSRF needed)."""
        return self._signer.sign({"kind": _API_KIND, "user": user.name})

    def csrf_token(self, request: HttpRequest) -> str | None:
        """Return the CSRF token of the request's session cookie, or None."""
        token = request.cookies.get(self._cookie_name)
        if token is None:
            return None
        user, claims = self._user_from(token, _SESSION_KIND)
        csrf = claims.get("csrf")
        return csrf if user is not None and isinstance(csrf, str) else None
