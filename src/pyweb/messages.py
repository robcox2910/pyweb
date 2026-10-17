"""Messages: headers, requests, responses, and cookies.

Think of an HTTP message as a parcel. The headers are the stickers on the
outside (who it is for, what is inside), and the body is the contents. This
module gives you tidy objects for both kinds of parcel: the one that arrives
(``HttpRequest``) and the one you send back (``HttpResponse``). It checks every
sticker so nobody can sneak a second parcel in by hiding a newline in a value.
"""

import html as html_lib
import json as json_lib
import string
from collections.abc import Iterable
from dataclasses import dataclass, field
from email.message import Message
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qsl

from pynet.messaging import JsonValue

from pyweb.http1 import STATUS_REASONS

__all__ = [
    "HTTPError",
    "Headers",
    "HttpRequest",
    "HttpResponse",
    "JsonValue",
    "Outcome",
    "QueryParams",
    "StreamingResponse",
    "empty",
    "html",
    "json",
    "redirect",
    "text",
]

_UNSUPPORTED_MEDIA_TYPE = 415
_BAD_REQUEST = 400
_SEE_OTHER = 303
_NO_CONTENT = 204
_FORM_TYPE = "application/x-www-form-urlencoded"
_TOKEN_CHARS = frozenset(string.ascii_letters + string.digits + "!#$%&'*+-.^_`|~")
_VALUE_BANNED = frozenset("\r\n\x00")
_COOKIE_VALUE_BANNED = frozenset(' ;,"\\')


def _check_header(name: str, value: str) -> None:
    """Reject names that are not tokens and values with CR, LF or NUL."""
    if not name or not all(ch in _TOKEN_CHARS for ch in name):
        msg = f"invalid header name {name!r}"
        raise ValueError(msg)
    if any(ch in _VALUE_BANNED for ch in value):
        msg = f"invalid header value for {name!r}: CR, LF and NUL are not allowed"
        raise ValueError(msg)


class Headers:
    """A case-insensitive, order-preserving multi-dict of header fields."""

    def __init__(self, items: Iterable[tuple[str, str]] = ()) -> None:
        """Create headers from (name, value) pairs."""
        self._items: list[tuple[str, str]] = []
        for name, value in items:
            self.add(name, value)

    def get(self, name: str, default: str | None = None) -> str | None:
        """Return the first value of a header, or the default."""
        lowered = name.lower()
        for key, value in self._items:
            if key.lower() == lowered:
                return value
        return default

    def get_all(self, name: str) -> list[str]:
        """Return every value of a header in order."""
        lowered = name.lower()
        return [v for k, v in self._items if k.lower() == lowered]

    def set(self, name: str, value: str) -> None:
        """Replace all values of a header with one value."""
        _check_header(name, value)
        self.remove(name)
        self._items.append((name, value))

    def add(self, name: str, value: str) -> None:
        """Append a header value, keeping existing ones."""
        _check_header(name, value)
        self._items.append((name, value))

    def remove(self, name: str) -> None:
        """Delete every value of a header."""
        lowered = name.lower()
        self._items = [(k, v) for k, v in self._items if k.lower() != lowered]

    def __contains__(self, name: object) -> bool:
        """Return whether a header is present."""
        return isinstance(name, str) and self.get(name) is not None

    def items(self) -> list[tuple[str, str]]:
        """Return a copy of all (name, value) pairs in order."""
        return list(self._items)


class QueryParams:
    """A multi-dict of query-string or form pairs."""

    def __init__(self, items: Iterable[tuple[str, str]] = ()) -> None:
        """Create from (name, value) pairs."""
        self._items = list(items)

    def get(self, name: str, default: str | None = None) -> str | None:
        """Return the first value for a name, or the default."""
        for key, value in self._items:
            if key == name:
                return value
        return default

    def get_all(self, name: str) -> list[str]:
        """Return every value for a name in order."""
        return [v for k, v in self._items if k == name]

    def __contains__(self, name: object) -> bool:
        """Return whether a name is present."""
        return any(k == name for k, _ in self._items)

    def items(self) -> list[tuple[str, str]]:
        """Return a copy of all pairs in order."""
        return list(self._items)


def _reason(status: int) -> str:
    """Return the reason phrase for a status code."""
    return STATUS_REASONS.get(status, "Error")


class HTTPError(Exception):
    """Raise from a handler to answer with a specific HTTP error."""

    def __init__(self, status: int, message: str | None = None) -> None:
        """Store the status and optional message."""
        super().__init__(message or _reason(status))
        self.status = status
        self.message = message

    def to_response(self, request: HttpRequest) -> HttpResponse:
        """Render as JSON, or as a small HTML page when the client wants HTML."""
        reason = _reason(self.status)
        if request.wants_html:
            detail = html_lib.escape(self.message or "")
            page = (
                f"<!doctype html><title>{self.status} {reason}</title>"
                f"<h1>{self.status} {reason}</h1><p>{detail}</p>"
            )
            return html(page, self.status)
        return json({"error": reason, "message": self.message}, self.status)


def _content_type_parts(headers: Headers) -> tuple[str, str | None]:
    """Return the media type and charset of Content-Type."""
    raw = headers.get("Content-Type")
    if raw is None:
        return "", None
    message = Message()
    message["Content-Type"] = raw
    charset = message.get_content_charset()
    return message.get_content_type(), charset


@dataclass
class HttpRequest:
    """One parsed HTTP request."""

    method: str
    path: str
    query: QueryParams
    headers: Headers
    body: bytes = b""
    client: tuple[str, int] | None = None
    path_params: dict[str, object] = field(default_factory=dict[str, object])
    state: dict[str, object] = field(default_factory=dict[str, object])
    user: object | None = None

    def text(self) -> str:
        """Decode the body using the Content-Type charset (default UTF-8)."""
        _, charset = _content_type_parts(self.headers)
        try:
            return self.body.decode(charset or "utf-8")
        except (UnicodeDecodeError, LookupError) as exc:
            raise HTTPError(_BAD_REQUEST, "body is not valid text") from exc

    def json(self) -> JsonValue:
        """Parse the body as JSON."""
        try:
            return json_lib.loads(self.text())
        except (ValueError, RecursionError) as exc:
            # RecursionError: absurdly deep nesting such as "[[[[...]]]]".
            raise HTTPError(_BAD_REQUEST, "body is not valid JSON") from exc

    def form(self) -> QueryParams:
        """Parse an urlencoded form body."""
        media_type, _ = _content_type_parts(self.headers)
        if media_type != _FORM_TYPE:
            raise HTTPError(_UNSUPPORTED_MEDIA_TYPE, f"expected {_FORM_TYPE}")
        return QueryParams(parse_qsl(self.text(), keep_blank_values=True))

    @property
    def cookies(self) -> dict[str, str]:
        """Return cookies from the Cookie header, skipping malformed pairs."""
        result: dict[str, str] = {}
        for header in self.headers.get_all("Cookie"):
            for pair in header.split(";"):
                name, sep, value = pair.strip().partition("=")
                if sep and name:
                    result[name] = value
        return result

    @property
    def last_event_id(self) -> str | None:
        """Return the Last-Event-ID header used by SSE reconnects."""
        return self.headers.get("Last-Event-ID")

    @property
    def wants_html(self) -> bool:
        """Return whether the client accepts text/html."""
        return "text/html" in (self.headers.get("Accept") or "").lower()


def _check_cookie_token(kind: str, value: str, *, allow_empty: bool) -> None:
    """Reject cookie names, values and attributes with unsafe characters."""
    if not value and not allow_empty:
        msg = f"invalid cookie {kind}: empty"
        raise ValueError(msg)
    bad = any(
        ch in _COOKIE_VALUE_BANNED or not ch.isprintable() or not ch.isascii() for ch in value
    )
    if bad or (kind == "name" and "=" in value):
        msg = f"invalid cookie {kind} {value!r}"
        raise ValueError(msg)


@dataclass
class HttpResponse:
    """One HTTP response to send."""

    status: int = 200
    headers: Headers = field(default_factory=Headers)
    body: bytes = b""

    def set_cookie(
        self,
        name: str,
        value: str,
        *,
        max_age: int | None = None,
        path: str = "/",
        http_only: bool = True,
        same_site: str = "Lax",
        secure: bool = False,
    ) -> None:
        """Add a Set-Cookie header after validating every part."""
        _check_cookie_token("name", name, allow_empty=False)
        _check_cookie_token("value", value, allow_empty=True)
        _check_cookie_token("path", path, allow_empty=False)
        _check_cookie_token("same_site", same_site, allow_empty=False)
        parts = [f"{name}={value}", f"Path={path}"]
        if max_age is not None:
            parts.append(f"Max-Age={int(max_age)}")
        if http_only:
            parts.append("HttpOnly")
        parts.append(f"SameSite={same_site}")
        if secure:
            parts.append("Secure")
        self.headers.add("Set-Cookie", "; ".join(parts))

    def delete_cookie(self, name: str, path: str = "/") -> None:
        """Ask the browser to forget a cookie."""
        self.set_cookie(name, "", max_age=0, path=path)


@runtime_checkable
class StreamingResponse(Protocol):
    """A response whose body is sent a piece at a time (such as server-sent events).

    It has a status and headers like any response; the server sends the
    body as it is produced instead of all at once.
    """

    status: int
    headers: Headers


type Outcome = HttpResponse | StreamingResponse
"""Anything a handler or middleware may answer with."""


def json(data: JsonValue, status: int = 200) -> HttpResponse:
    """Build a JSON response."""
    response = HttpResponse(status, body=json_lib.dumps(data).encode())
    response.headers.set("Content-Type", "application/json")
    return response


def html(text: str, status: int = 200) -> HttpResponse:
    """Build an HTML response."""
    response = HttpResponse(status, body=text.encode())
    response.headers.set("Content-Type", "text/html; charset=utf-8")
    return response


def text(text: str, status: int = 200) -> HttpResponse:
    """Build a plain-text response."""
    response = HttpResponse(status, body=text.encode())
    response.headers.set("Content-Type", "text/plain; charset=utf-8")
    return response


def redirect(location: str, status: int = _SEE_OTHER) -> HttpResponse:
    """Build a redirect to a location."""
    response = HttpResponse(status)
    response.headers.set("Location", location)
    return response


def empty(status: int = _NO_CONTENT) -> HttpResponse:
    """Build a response with no body."""
    return HttpResponse(status)
