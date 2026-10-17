"""Tests for WebSocketSession's goodbye task when the session is cancelled."""

from typing import cast

import pytest
from pynet.loop import Event, run, sleep, spawn
from pynet.loop.testing import FakeClock
from pynet.streams import StreamReader, StreamWriter
from pynet.ws import CLOSE_GOING_AWAY

from pyweb import _upgrade
from pyweb._upgrade import WebSocketSession
from pyweb.app import WebSocketUpgrade
from pyweb.messages import Headers, HttpRequest, QueryParams

MAX_MESSAGE = 1024
SETTLE = 0.01


class StuckGoodbyeSocket:
    """A pretend WebSocket whose "going away" close never gets a reply."""

    def __init__(self) -> None:
        """Start with no goodbye in progress."""
        self.goodbye_ended = False

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Return at once for a normal close; hang for 1001 until cancelled."""
        del reason
        if code != CLOSE_GOING_AWAY:
            return
        try:
            await Event().wait()
        finally:
            self.goodbye_ended = True


class TestCancelledWhileSayingGoodbye:
    """Test that cancelling a session also cancels its goodbye task."""

    def test_cancel_during_goodbye_cancels_closer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cancel the 1001 close task too, instead of leaving it orphaned."""
        socket = StuckGoodbyeSocket()

        async def fake_accept(*_args: object, **_kwargs: object) -> StuckGoodbyeSocket:
            """Pretend the handshake worked."""
            return socket

        monkeypatch.setattr(_upgrade, "accept_websocket", fake_accept)

        async def main() -> bool:
            """Start the session, let the handler ask to stop, then cancel it."""
            request = HttpRequest("GET", "/live", QueryParams(), Headers())
            holder: list[WebSocketSession] = []

            def handler(_ws: object, _request: HttpRequest) -> None:
                """Ask the session to say goodbye, then finish at once."""
                holder[0].stop()

            session = WebSocketSession(
                WebSocketUpgrade(handler, {}, request),
                cast("StreamReader", None),
                cast("StreamWriter", None),
                max_message_size=MAX_MESSAGE,
            )
            holder.append(session)
            task = spawn(session.run())
            await sleep(SETTLE)  # the session is now waiting for its goodbye
            task.cancel()
            await sleep(SETTLE)
            return socket.goodbye_ended

        assert run(main(), clock=FakeClock(autojump=True))
