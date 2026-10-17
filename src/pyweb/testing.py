"""TestClient: try an App without opening a real network socket.

Think of a flight simulator. A pilot can practise take-offs without a real
plane, and every dial behaves just like the real cockpit. The
``TestClient`` is a flight simulator for your web app: it builds the same
``HttpRequest`` the real server would build, hands it to ``App.handle``,
and gives you back the answer. It even keeps a cookie jar, like a browser,
so you can test logging in and out.
"""

import json as json_lib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypedDict, Unpack
from urllib.parse import quote, urlencode

from pyweb.app import App
from pyweb.http1 import split_target
from pyweb.messages import Headers, HttpRequest, HttpResponse, JsonValue, Outcome, QueryParams
from pyweb.sse import EventStream, ServerEvent

__all__ = ["RequestOptions", "TestClient", "TestResponse"]

_CLIENT = ("testclient", 0)
_HOST = "testserver"
_FORM_TYPE = "application/x-www-form-urlencoded"
# Characters left alone when quoting a path: everything already legal in a target.
_SAFE_PATH_CHARS = "/?&=%:@!$'()*+,;~"


class RequestOptions(TypedDict, total=False):
    """The optional keyword arguments every TestClient method accepts."""

    query: Mapping[str, str] | None
    json: JsonValue | None
    form: Mapping[str, str] | None
    body: bytes | None
    headers: Mapping[str, str] | None


@dataclass
class TestResponse:
    """What the app answered: status, headers and the whole body."""

    __test__ = False  # Tell pytest this is not a test class.

    status: int
    headers: Headers
    body: bytes = b""

    @property
    def text(self) -> str:
        """Return the body decoded as UTF-8."""
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> JsonValue:
        """Return the body parsed as JSON."""
        return json_lib.loads(self.body)


def _encode_body(
    json: JsonValue | None, form: Mapping[str, str] | None, body: bytes | None
) -> tuple[bytes, str | None]:
    """Return the body bytes and the Content-Type to send with them."""
    given = [part is not None for part in (json, form, body)]
    if sum(given) > 1:
        msg = "pass only one of json, form or body"
        raise ValueError(msg)
    if json is not None:
        return json_lib.dumps(json).encode(), "application/json"
    if form is not None:
        return urlencode(dict(form)).encode(), _FORM_TYPE
    return body or b"", None


@dataclass
class TestClient:
    """Send requests straight to an ``App`` and keep cookies like a browser."""

    __test__ = False  # Tell pytest this is not a test class.

    app: App
    cookies: dict[str, str] = field(default_factory=dict[str, str])

    async def _handle(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        json: JsonValue | None = None,
        form: Mapping[str, str] | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Outcome:
        """Build a request exactly as the server would and return the app's answer."""
        content, content_type = _encode_body(json, form, body)
        target = quote(path, safe=_SAFE_PATH_CHARS)
        if query:
            target += ("&" if "?" in target else "?") + urlencode(dict(query))
        decoded_path, pairs = split_target(target)
        request_headers = Headers([("Host", _HOST)])
        if content_type is not None:
            request_headers.set("Content-Type", content_type)
        if content:
            request_headers.set("Content-Length", str(len(content)))
        if self.cookies:
            jar = "; ".join(f"{name}={value}" for name, value in self.cookies.items())
            request_headers.set("Cookie", jar)
        for name, value in (headers or {}).items():
            request_headers.set(name, value)
        request = HttpRequest(
            method, decoded_path, QueryParams(pairs), request_headers, content, _CLIENT
        )
        outcome = await self.app.handle(request)
        self._store_cookies(outcome.headers)
        return outcome

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        json: JsonValue | None = None,
        form: Mapping[str, str] | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> TestResponse:
        """Build a request exactly as the server would, and send it to the app.

        Args:
            method: The HTTP method.
            path: The target, such as ``"/search?q=cats"``; spaces and
                accents are percent-encoded for you.
            query: Extra query pairs appended to the target.
            json: A value to send as a JSON body.
            form: Fields to send as an urlencoded form body.
            body: Raw bytes to send as the body.
            headers: Extra request headers.

        Raises:
            ValueError: If more than one of ``json``, ``form`` and ``body``
                is given.

        """
        outcome = await self._handle(
            method, path, query=query, json=json, form=form, body=body, headers=headers
        )
        # A streaming body is not read here (see ``stream``); only its status and headers.
        data = outcome.body if isinstance(outcome, HttpResponse) else b""
        return TestResponse(outcome.status, outcome.headers, data)

    async def stream(
        self, path: str, *, count: int, headers: Mapping[str, str] | None = None
    ) -> list[ServerEvent]:
        """GET an ``EventStream`` route and collect its first ``count`` events.

        The stream's source is closed afterwards, just as the server does
        when a browser leaves. Fewer events come back if the source ends early.

        Raises:
            TypeError: If the route does not answer with an ``EventStream``.

        """
        outcome = await self._handle("GET", path, headers=headers)
        if not isinstance(outcome, EventStream):
            msg = f"GET {path} answered {outcome.status}, not an EventStream"
            raise TypeError(msg)
        events: list[ServerEvent] = []
        try:
            async for event in outcome.source:
                events.append(event)
                if len(events) >= count:
                    break
        finally:
            await outcome.aclose()
        return events

    def _store_cookies(self, headers: Headers) -> None:
        """Update the jar from Set-Cookie headers, forgetting ``Max-Age=0`` ones."""
        for header in headers.get_all("Set-Cookie"):
            first, *attributes = (part.strip() for part in header.split(";"))
            name, sep, value = first.partition("=")
            if not sep or not name:
                continue
            expired = False
            for attribute in attributes:
                key, _, setting = attribute.partition("=")
                if key.lower() == "max-age":
                    expired = setting.lstrip("-").isdigit() and int(setting) <= 0
            if expired:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value

    async def get(self, path: str, **options: Unpack[RequestOptions]) -> TestResponse:
        """Send a GET request; see ``request`` for the options."""
        return await self.request("GET", path, **options)

    async def post(self, path: str, **options: Unpack[RequestOptions]) -> TestResponse:
        """Send a POST request; see ``request`` for the options."""
        return await self.request("POST", path, **options)

    async def put(self, path: str, **options: Unpack[RequestOptions]) -> TestResponse:
        """Send a PUT request; see ``request`` for the options."""
        return await self.request("PUT", path, **options)

    async def patch(self, path: str, **options: Unpack[RequestOptions]) -> TestResponse:
        """Send a PATCH request; see ``request`` for the options."""
        return await self.request("PATCH", path, **options)

    async def delete(self, path: str, **options: Unpack[RequestOptions]) -> TestResponse:
        """Send a DELETE request; see ``request`` for the options."""
        return await self.request("DELETE", path, **options)

    async def head(self, path: str, **options: Unpack[RequestOptions]) -> TestResponse:
        """Send a HEAD request; see ``request`` for the options."""
        return await self.request("HEAD", path, **options)
