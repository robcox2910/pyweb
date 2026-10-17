"""Server-sent events: a one-way radio station from the server to browsers.

Think of a radio station. Listeners tune in once (one HTTP request) and
then the station keeps talking: every bit of news goes out the moment it
happens, and the listener never has to ask "anything new?" again. When
there is no news for a while, the presenter says "still here!" (a
keep-alive comment) so nobody thinks the station has gone off air.

``ServerEvent`` is one bit of news, ``encode_event`` turns it into the
text browsers expect, ``EventStream`` is a response that keeps sending
events, and ``EventHub`` is the studio: publish once and every listener
hears it. A listener who falls behind gets the *newest* news, not a
growing pile of old news.

Example:
    >>> from pynet.loop import run
    >>> async def main():
    ...     hub = EventHub()
    ...     radio = hub.subscribe()
    ...     hub.publish(ServerEvent("hello"))
    ...     print(encode_event(await anext(radio)).decode().strip())
    >>> run(main())
    data: hello

"""

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Self, cast

from pynet.loop import Queue

from pyweb.messages import Headers

__all__ = [
    "DEFAULT_KEEPALIVE",
    "DEFAULT_MAX_PENDING",
    "KEEPALIVE_COMMENT",
    "EventHub",
    "EventStream",
    "ServerEvent",
    "Subscription",
    "encode_event",
]

DEFAULT_KEEPALIVE = 15.0
DEFAULT_MAX_PENDING = 100
KEEPALIVE_COMMENT = b": keep-alive\n\n"
_LINE_BREAK = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class ServerEvent:
    """One message for the browser.

    Attributes:
        data: The message text; line breaks are allowed.
        event: An event name, so the browser can listen for that kind only.
        id: An id the browser sends back as ``Last-Event-ID`` when it reconnects.
        retry: How many milliseconds the browser should wait before reconnecting.

    """

    data: str
    event: str | None = None
    id: str | None = None
    retry: int | None = None


def _field(name: str, value: str) -> str:
    """Return one ``name: value`` line, refusing line breaks in the value."""
    if "\r" in value or "\n" in value:
        msg = f"the {name} field must not contain a line break"
        raise ValueError(msg)
    return f"{name}: {value}"


def encode_event(event: ServerEvent) -> bytes:
    """Turn an event into the ``text/event-stream`` bytes a browser reads.

    Each line of ``data`` becomes its own ``data:`` line and the event
    ends with a blank line.

    Raises:
        ValueError: If ``event`` or ``id`` holds a line break (it would
            let someone forge extra fields), or ``retry`` is negative.

    """
    lines: list[str] = []
    if event.event is not None:
        lines.append(_field("event", event.event))
    if event.id is not None:
        lines.append(_field("id", event.id))
    if event.retry is not None:
        if event.retry < 0:
            msg = "retry must not be negative"
            raise ValueError(msg)
        lines.append(f"retry: {event.retry}")
    lines += [f"data: {line}" for line in _LINE_BREAK.split(event.data)]
    return ("\n".join(lines) + "\n\n").encode()


class EventStream:
    """A response that sends events for as long as its source has them.

    The server writes each event from ``source`` as it arrives, a
    keep-alive comment after ``keepalive`` quiet seconds, and closes the
    source (``aclose``) when the browser goes away or the server shuts down.

    Args:
        source: Where the events come from, such as an async generator or
            ``EventHub.subscribe()``.
        keepalive: Seconds of quiet before a keep-alive comment is sent.
        status: The HTTP status.
        headers: Extra headers; the SSE headers are added to them.

    """

    def __init__(
        self,
        source: AsyncIterator[ServerEvent],
        *,
        keepalive: float = DEFAULT_KEEPALIVE,
        status: int = 200,
        headers: Headers | None = None,
    ) -> None:
        """Wrap the source and set the SSE headers."""
        self.source = source
        self.keepalive = keepalive
        self.status = status
        self.headers = headers if headers is not None else Headers()
        self.headers.set("Content-Type", "text/event-stream")
        self.headers.set("Cache-Control", "no-store")
        self.headers.set("Transfer-Encoding", "chunked")
        self._closed = False

    async def aclose(self) -> None:
        """Close the source so its cleanup runs; closing twice is harmless."""
        if self._closed:
            return
        self._closed = True
        close = getattr(self.source, "aclose", None)
        if callable(close):
            await cast("Callable[[], Awaitable[object]]", close)()


class _Mailbox:
    """The queue behind one subscription: the newest ``max_pending`` events."""

    def __init__(self, max_pending: int) -> None:
        """Make room for ``max_pending`` events."""
        self.max_pending = max_pending
        # One extra slot so the "the end" marker always fits.
        self.queue: Queue[ServerEvent | None] = Queue(max_pending + 1)
        self.ended = False
        self.dropped = 0

    def offer(self, event: ServerEvent) -> None:
        """Queue an event, throwing the oldest away if there is no room."""
        if self.queue.qsize() >= self.max_pending:
            self.queue.get_nowait()
            self.dropped += 1
        self.queue.put_nowait(event)

    def end(self) -> None:
        """Mark the end, after the events already queued."""
        if not self.ended:
            self.ended = True
            self.queue.put_nowait(None)


class Subscription:
    """One listener's place in an ``EventHub``: iterate it to hear the news.

    It keeps at most ``max_pending`` unheard events. When a new one comes
    and there is no room, the oldest is thrown away and ``dropped`` goes up.
    """

    def __init__(self, mailbox: _Mailbox, leave: Callable[[], None]) -> None:
        """Listen to ``mailbox``; call ``leave`` when done."""
        self._mailbox = mailbox
        self._leave = leave

    @property
    def dropped(self) -> int:
        """Return how many events were thrown away because this listener was slow."""
        return self._mailbox.dropped

    def __aiter__(self) -> Self:
        """Return itself; a subscription is its own iterator."""
        return self

    async def __anext__(self) -> ServerEvent:
        """Wait for the next event; stop once the hub or subscription closes."""
        mailbox = self._mailbox
        if mailbox.ended and mailbox.queue.empty():
            raise StopAsyncIteration
        event = await mailbox.queue.get()
        if event is None:
            raise StopAsyncIteration
        return event

    async def aclose(self) -> None:
        """Leave the hub; any further iteration stops."""
        self._leave()


class EventHub:
    """Broadcast events to every subscriber, like a radio studio.

    ``publish`` never waits: a slow listener only loses its own oldest
    news and never slows the publisher or other listeners down.
    """

    def __init__(self) -> None:
        """Start with no listeners."""
        self._mailboxes: set[_Mailbox] = set()
        self._closed = False

    @property
    def subscriber_count(self) -> int:
        """Return how many listeners are tuned in."""
        return len(self._mailboxes)

    def subscribe(self, *, max_pending: int = DEFAULT_MAX_PENDING) -> Subscription:
        """Tune in a new listener that keeps up to ``max_pending`` unheard events.

        After ``close`` the subscription it returns is already finished.

        Raises:
            ValueError: If ``max_pending`` is less than 1.

        """
        if max_pending < 1:
            msg = "max_pending must be at least 1"
            raise ValueError(msg)
        mailbox = _Mailbox(max_pending)

        def leave() -> None:
            """Stop this listener and forget it."""
            mailbox.end()
            self._mailboxes.discard(mailbox)

        if self._closed:
            mailbox.end()
        else:
            self._mailboxes.add(mailbox)
        return Subscription(mailbox, leave)

    def publish(self, event: ServerEvent) -> int:
        """Send an event to every listener; return how many it reached."""
        for mailbox in self._mailboxes:
            mailbox.offer(event)
        return len(self._mailboxes)

    def close(self) -> None:
        """Go off air: every subscription ends after its queued events."""
        self._closed = True
        for mailbox in self._mailboxes:
            mailbox.end()
        self._mailboxes.clear()
