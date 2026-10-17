"""One WebSocket conversation: answer the phone, let the handler talk, hang up.

HTTP is like sending letters: one question, one answer, done. A
WebSocket is a phone call: once the line is open, either side can speak
whenever it likes. The ``Connection`` hands the socket over to a
``WebSocketSession`` once every middleware has said "yes, put them
through" (an ``App`` answer of ``WebSocketUpgrade``).

The session always hangs up politely with a close code:

* 1000 -- the handler finished normally;
* 1011 -- the handler crashed (the error is logged);
* 1001 -- the server is shutting down ("going away").

This module is private; ``pyweb._conn.Connection`` uses it.
"""

import inspect
import logging

from pynet.errors import HandshakeError
from pynet.loop import Cancelled, Task, spawn
from pynet.streams import StreamReader, StreamWriter
from pynet.ws import (
    CLOSE_GOING_AWAY,
    CLOSE_INTERNAL_ERROR,
    WebSocket,
    WebSocketClosed,
    accept_websocket,
)

from pyweb.app import WebSocketUpgrade

logger = logging.getLogger("pyweb.server")

SWITCHING_PROTOCOLS = 101
BAD_REQUEST = 400


class WebSocketSession:
    """Run one accepted WebSocket until the handler ends or the server leaves.

    Args:
        upgrade: The App's "go ahead": which handler, request and params.
        reader: The connection's reader, just after the request head.
        writer: The connection's writer.
        max_message_size: The largest message the WebSocket accepts.

    """

    def __init__(
        self,
        upgrade: WebSocketUpgrade,
        reader: StreamReader,
        writer: StreamWriter,
        *,
        max_message_size: int,
    ) -> None:
        """Remember everything needed to answer the call."""
        self._upgrade = upgrade
        self._request = upgrade.request
        self._reader = reader
        self._writer = writer
        self._max_message_size = max_message_size
        self._ws: WebSocket | None = None
        self._stopping = False
        self._closer: Task[None] | None = None

    def stop(self) -> None:
        """Say goodbye with 1001 (now, or as soon as the call is answered)."""
        self._stopping = True
        if self._ws is not None and self._closer is None:
            self._closer = spawn(self._ws.close(CLOSE_GOING_AWAY, "server shutting down"))

    async def run(self) -> int:
        """Do the handshake and run the handler; return the status for the log.

        Returns:
            101 once the conversation is over, or 400 if the handshake was
            bad (PyNet has already answered 400 and hung up).

        """
        headers = dict(self._request.headers.items())
        try:
            ws = await accept_websocket(
                self._reader,
                self._writer,
                method=self._request.method,
                headers=headers,
                max_message_size=self._max_message_size,
            )
        except HandshakeError as exc:
            logger.info("Bad WebSocket handshake for %s: %s", self._request.path, exc)
            return BAD_REQUEST
        self._ws = ws
        if self._stopping:
            self.stop()
        try:
            await self._talk(ws)
        except Cancelled:
            if self._closer is not None:
                self._closer.cancel()
            raise
        if self._closer is not None:
            await self._closer
        return SWITCHING_PROTOCOLS

    async def _talk(self, ws: WebSocket) -> None:
        """Call the handler, then close with 1000 (or 1011 if it crashed)."""
        try:
            result = self._upgrade.handler(ws, self._request, **self._upgrade.params)
            if inspect.isawaitable(result):
                await result
        except WebSocketClosed:
            return  # the other side (or a shutdown) already hung up
        except Exception:
            logger.exception("WebSocket handler for %s failed", self._request.path)
            await ws.close(CLOSE_INTERNAL_ERROR)
            return
        await ws.close()
