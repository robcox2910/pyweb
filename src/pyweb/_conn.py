"""One connection: read requests, ask the App, write answers, repeat.

Think of a shop counter with one customer standing at it. The shopkeeper
listens to the order (the request head), takes the money if there is any
(the body), fetches the goods (asks the ``App``), hands them over (writes
the response) and then asks "anything else?" (keep-alive). If the
customer just stands there saying nothing for too long, the shopkeeper
moves on to the next customer.

This module is private: ``pyweb.aserver.AsyncServer`` makes one
``Connection`` per socket and keeps count of them.
"""

import contextlib
import logging
from collections.abc import Awaitable
from email.utils import formatdate
from typing import cast

from pynet.errors import ConnectionClosedError
from pynet.loop import Cancelled, Task, current_task, get_running_loop, timeout
from pynet.streams import IncompleteReadError, LimitOverrunError, StreamReader, StreamWriter

from pyweb._streaming import EventPump
from pyweb.app import App, Handler
from pyweb.http1 import (
    STATUS_REASONS,
    HttpParseError,
    Limits,
    RequestHead,
    body_length,
    encode_response_head,
    parse_head,
    split_target,
    wants_keep_alive,
)
from pyweb.messages import (
    Headers,
    HTTPError,
    HttpRequest,
    HttpResponse,
    Outcome,
    QueryParams,
    StreamingResponse,
)
from pyweb.sse import EventStream

logger = logging.getLogger("pyweb.server")
access_logger = logging.getLogger("pyweb.access")

_HEAD_END = b"\r\n\r\n"
_REQUEST_TIMEOUT = 408
_TOO_LARGE = 413
_HEADERS_TOO_LARGE = 431
_SERVER_ERROR = 500
_NO_BODY_STATUSES = frozenset({204, 304})
_SERVER_OWNED = frozenset({"connection", "content-length", "date", "server", "transfer-encoding"})
_PEER_PARTS = 2


def _peer(writer: StreamWriter) -> tuple[str, int] | None:
    """Return the client's (host, port), if the socket knows it."""
    peer = writer.get_extra_info("peername")
    if isinstance(peer, tuple) and len(cast("tuple[object, ...]", peer)) >= _PEER_PARTS:
        host, port = cast("tuple[object, object]", peer[:_PEER_PARTS])
        return str(host), int(cast("int", port))
    return None  # pragma: no cover - TCP sockets always have a peer name


def _error_request(head: RequestHead | None) -> HttpRequest:
    """Build a stand-in request so an error page can pick JSON or HTML."""
    headers = Headers(head.headers if head is not None else [])
    return HttpRequest("GET", "/", QueryParams(), headers)


def _error(status: int, head: RequestHead | None, message: str | None = None) -> HttpResponse:
    """Render an HTTP error the same way the App would."""
    return HTTPError(status, message).to_response(_error_request(head))


def _log_path(head: RequestHead | None) -> str:
    """Return the raw path (no query, which may hold secrets) safe for a log line."""
    if head is None:
        return "-"
    path = head.target.partition("?")[0]
    return "".join(ch if ch.isprintable() else f"%{ord(ch):02X}" for ch in path)


class Connection:
    """Serve HTTP/1.1 requests on one socket until either side stops.

    Attributes:
        idle: True while waiting for the first byte of the next request.
        closing: Set by the server during shutdown: finish the current
            request, then close.

    """

    def __init__(
        self,
        app: App,
        limits: Limits,
        reader: StreamReader,
        writer: StreamWriter,
        *,
        access_log: bool,
    ) -> None:
        """Remember the app, limits and streams for this connection."""
        self._app = app
        self._limits = limits
        self._reader = reader
        self._writer = writer
        self._access_log = access_log
        self._loop = get_running_loop()
        self._client = _peer(writer)
        self._task: Task[object] | None = current_task()
        self.idle = False
        self.closing = False
        self._pump: EventPump | None = None

    def begin_close(self) -> None:
        """Finish the current request (ending any open stream), then close."""
        self.closing = True
        if self._pump is not None:
            self._pump.stop()

    def cancel(self) -> None:
        """Cut the connection off (used when the grace period runs out)."""
        if self._task is not None:
            self._task.cancel()

    async def serve(self) -> None:
        """Answer requests until the client or the server wants to stop."""
        try:
            first = True
            while not self.closing and await self._one_request(first=first):
                first = False
        except Cancelled:
            self._writer.abort()
            raise
        except ConnectionClosedError, IncompleteReadError, OSError:
            pass  # the client went away; nobody is left to answer
        await self._finish()

    async def reject(self, status: int) -> None:
        """Answer with an error status (such as 503) and close."""
        with contextlib.suppress(ConnectionClosedError, OSError):
            await self._send(_error(status, None), None, self._loop.time(), keep=False)
        await self._finish()

    async def _finish(self) -> None:
        """Close the socket, giving buffered bytes up to ``write_timeout`` to drain."""
        self._writer.close()
        try:
            async with timeout(self._limits.write_timeout):
                await self._writer.wait_closed()
        except TimeoutError:
            self._writer.abort()

    # -- reading ------------------------------------------------------------

    async def _one_request(self, *, first: bool) -> bool:
        """Serve one request; return whether to keep the connection open."""
        first_byte = await self._wait_for_request(first=first)
        if not first_byte:
            return False
        started = self._loop.time()
        head: RequestHead | None = None
        try:
            head = parse_head(await self._read_head(first_byte), self._limits)
            request = await self._read_request(head)
        except HttpParseError as exc:
            return await self._send(_error(exc.status, head, str(exc)), head, started, keep=False)
        websocket = self._websocket_route(request)
        if websocket is not None:
            return await self._fallback(self._upgrade(request, *websocket), head, started)
        try:
            outcome: Outcome = await self._app.handle(request)
        except Exception:
            logger.exception("App failed on %s %s", head.method, _log_path(head))
            outcome = _error(_SERVER_ERROR, head)
        # Decided after the handler: the server may have begun closing meanwhile.
        keep = wants_keep_alive(head) and not self.closing and not _asks_to_close(outcome)
        if isinstance(outcome, HttpResponse):
            return await self._send(outcome, head, started, keep=keep)
        if head.method == "HEAD":
            try:
                return await self._send(outcome, head, started, keep=keep)
            finally:
                await _close_stream(outcome)
        return await self._fallback(self._write_stream(outcome, head, started), head, started)

    async def _wait_for_request(self, *, first: bool) -> bytes:
        """Wait for the first byte of a request; ``b""`` means close quietly.

        A brand-new connection gets ``header_timeout``; a keep-alive one
        waiting for its next request gets ``keepalive_timeout``.
        """
        limits = self._limits
        self.idle = True
        try:
            async with timeout(limits.header_timeout if first else limits.keepalive_timeout):
                return await self._reader.read(1)
        except TimeoutError:
            return b""
        finally:
            self.idle = False

    async def _read_head(self, first_byte: bytes) -> bytes:
        """Read the rest of the head within ``header_timeout`` and ``max_head_bytes``."""
        limit = self._limits.max_head_bytes + len(_HEAD_END) - len(first_byte)
        try:
            async with timeout(self._limits.header_timeout):
                rest = await self._reader.read_until(_HEAD_END, limit=limit)
        except TimeoutError as exc:
            raise HttpParseError(_REQUEST_TIMEOUT, "the request head took too long") from exc
        except LimitOverrunError as exc:
            raise HttpParseError(_HEADERS_TOO_LARGE, "the request head is too large") from exc
        return (first_byte + rest)[: -len(_HEAD_END)]

    async def _read_request(self, head: RequestHead) -> HttpRequest:
        """Check the framing, read the body and build the request."""
        length = body_length(head)
        if length > self._limits.max_body_bytes:
            raise HttpParseError(_TOO_LARGE, "the request body is too large")
        path, query = split_target(head.target)
        body = b""
        if length:
            try:
                async with timeout(self._limits.body_timeout):
                    body = await self._reader.read_exactly(length)
            except TimeoutError as exc:
                raise HttpParseError(_REQUEST_TIMEOUT, "the request body took too long") from exc
        headers = Headers(head.headers)
        return HttpRequest(head.method, path, QueryParams(query), headers, body, self._client)

    def _websocket_route(self, request: HttpRequest) -> tuple[Handler, dict[str, object]] | None:
        """Return the WebSocket route if this is an upgrade request for one."""
        upgrade = (request.headers.get("Upgrade") or "").lower()
        if request.method != "GET" or upgrade != "websocket":
            return None
        return self._app.websocket_route(request.path)

    # -- writing ------------------------------------------------------------

    def _head_bytes(
        self,
        outcome: Outcome,
        head: RequestHead | None,
        *,
        keep: bool,
        length: str | None,
        chunked: bool = False,
    ) -> bytes:
        """Encode the status line and headers, adding the server's own headers."""
        items = [(k, v) for k, v in outcome.headers.items() if k.lower() not in _SERVER_OWNED]
        items += [("Date", _http_date()), ("Server", "PyWeb")]
        if length is not None and outcome.status not in _NO_BODY_STATUSES:
            items.append(("Content-Length", length))
        if chunked:
            items.append(("Transfer-Encoding", "chunked"))
        if not keep:
            items.append(("Connection", "close"))
        elif head is not None and head.version == "HTTP/1.0":
            items.append(("Connection", "keep-alive"))
        reason = STATUS_REASONS.get(outcome.status, "Unknown")
        return encode_response_head(outcome.status, reason, items)

    async def _send(
        self, outcome: Outcome, head: RequestHead | None, started: float, *, keep: bool
    ) -> bool:
        """Write a buffered response (or just the head of a stream); return ``keep``.

        A buffered response gets the Content-Length its body has -- for HEAD,
        the length the GET body would have had. HEAD on a stream sends the
        head alone and never starts the stream.
        """
        is_head = head is not None and head.method == "HEAD"
        body, length = b"", None
        if isinstance(outcome, HttpResponse):
            if not is_head and outcome.status not in _NO_BODY_STATUSES:
                body = outcome.body
            length = (outcome.headers.get("Content-Length") if is_head else None) or str(len(body))
        try:
            data = self._head_bytes(outcome, head, keep=keep, length=length) + body
        except ValueError:
            logger.exception("Response for %s could not be sent; answering 500", _log_path(head))
            return await self._send(_error(_SERVER_ERROR, head), head, started, keep=False)
        return await self._write(data, outcome.status, len(body), head, started, keep=keep)

    async def _write(
        self,
        data: bytes,
        status: int,
        body_bytes: int,
        head: RequestHead | None,
        started: float,
        *,
        keep: bool,
    ) -> bool:
        """Write bytes within ``write_timeout`` and log the request; return ``keep``."""
        try:
            async with timeout(self._limits.write_timeout):
                self._writer.write(data)
                await self._writer.drain()
        except TimeoutError:
            self._writer.abort()  # the client stopped reading
            return False
        self._log(head, status, body_bytes, started)
        return keep

    def _log(self, head: RequestHead | None, status: int, body_bytes: int, started: float) -> None:
        """Write one access-log line, if access logging is on."""
        if self._access_log:
            access_logger.info(
                "%s %s %d %d %.1fms",
                head.method if head is not None else "-",
                _log_path(head),
                status,
                body_bytes,
                (self._loop.time() - started) * 1000,
            )

    async def _fallback(self, hook: Awaitable[bool], head: RequestHead, started: float) -> bool:
        """Await a stream/upgrade hook; answer 500 and close if it is not built yet."""
        try:
            return await hook
        except NotImplementedError:
            logger.exception("%s %s needs a feature not built yet", head.method, _log_path(head))
            return await self._send(_error(_SERVER_ERROR, head), head, started, keep=False)

    # -- hooks filled in by later tasks ------------------------------------

    async def _write_stream(
        self, outcome: StreamingResponse, head: RequestHead, started: float
    ) -> bool:
        """Send an ``EventStream`` until it ends; the connection is never reused after.

        Raises:
            NotImplementedError: For a kind of stream the server cannot write.

        """
        if not isinstance(outcome, EventStream):
            msg = f"cannot stream a {type(outcome).__name__}"
            raise NotImplementedError(msg)
        try:
            data = self._head_bytes(outcome, head, keep=False, length=None, chunked=True)
        except ValueError:
            await outcome.aclose()
            logger.exception("Stream for %s could not be sent; answering 500", _log_path(head))
            return await self._send(_error(_SERVER_ERROR, head), head, started, keep=False)
        pump = EventPump(outcome, self._reader, self._writer, self._limits.write_timeout)
        self._pump = pump
        if self.closing:
            pump.stop()
        try:
            await pump.run(data)
        finally:
            self._pump = None
        self._log(head, outcome.status, pump.body_bytes, started)
        return False

    async def _upgrade(
        self, request: HttpRequest, handler: Handler, params: dict[str, object]
    ) -> bool:
        """Hand the socket to a WebSocket handler; return whether to keep it (never)."""
        del request, handler, params  # used once WebSockets are built
        msg = "WebSocket upgrades are not supported yet"
        raise NotImplementedError(msg)


def _asks_to_close(outcome: Outcome) -> bool:
    """Return whether the handler set ``Connection: close`` on its answer."""
    tokens = (outcome.headers.get("Connection") or "").lower().split(",")
    return "close" in (token.strip() for token in tokens)


async def _close_stream(outcome: StreamingResponse) -> None:
    """Close a stream that will never be sent, so its source can clean up."""
    if isinstance(outcome, EventStream):
        await outcome.aclose()


def _http_date() -> str:
    """Return the current time in the format HTTP's Date header uses."""
    return formatdate(usegmt=True)
