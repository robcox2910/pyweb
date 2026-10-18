"""Property-based tests -- rules that must hold for *every* input.

An ordinary test checks one example someone thought of: "the bytes
``GET /a HTTP/1.1`` parse into method ``GET`` and target ``/a``". A property
test states a rule instead, such as "whatever request head you write out,
reading it back gives exactly the same method, target, version and headers",
and lets Hypothesis invent hundreds of heads to try to break it. If one breaks
the rule, Hypothesis *shrinks* it down to the smallest input that still fails,
and shows you that.

Each test below says its rule in plain English in its docstring. The
strategies at the top are recipes for making random inputs; each one is
named for what it makes.

The step-by-step checks of the route table and of ``Headers`` against plain
lists live in ``tests/test_model.py``. See ``docs/property-testing.md`` for
the walk-through.
"""

import io
import socket
from http.client import HTTPResponse
from typing import cast
from urllib.parse import quote, urlencode

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pyweb.http1 import (
    KNOWN_METHODS,
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
from tests.strategies import header_names

# The thorough profile runs 2,000 examples of each test, and shrinking a
# failure takes a few seconds more, so these get longer than the 10 seconds
# pyproject.toml gives every other test.
pytestmark = pytest.mark.timeout(300)

LIMITS = Limits()

# A head with a handful of headers finds the same bugs as one with a hundred,
# and the limit (``Limits.max_headers``) is 100, so stay well under it.
MOST_HEADERS = 8

# The smallest and largest status a server answers with. ``http.client``
# quietly skips over ``100 Continue`` replies (it's waiting for the real one),
# so the response round-trip starts at 200.
FIRST_STATUS = 200
LAST_STATUS = 599
FIRST_ERROR_STATUS = 400

# Bytes that look blank but aren't a space or a tab: vertical tab, form
# feed, \x1c, "next line" (\x85) and the no-break space (\xa0).
BLANK_LOOKALIKES = "\x0b\x0c\x1c\x85\xa0"

# A damaged head gets at most this many random edits.
MOST_EDITS = 4

# --- Strategies: recipes for random inputs -----------------------------------
# header_names() lives in tests/strategies.py, shared with the model check.


def methods() -> st.SearchStrategy[str]:
    """Pick one of the methods PyWeb understands: GET, POST, HEAD and friends."""
    return st.sampled_from(sorted(KNOWN_METHODS))


def versions() -> st.SearchStrategy[str]:
    """Pick one of the HTTP versions PyWeb speaks: ``HTTP/1.0`` or ``HTTP/1.1``."""
    return st.sampled_from(["HTTP/1.0", "HTTP/1.1"])


def targets() -> st.SearchStrategy[str]:
    """Make any request target: one word of any bytes, with no space, CR, LF or NUL."""
    # The request line is three words split by single spaces, so a target
    # can't hold a space; CR, LF and NUL are refused anywhere in a head.
    # The head is read as latin-1, so every character is one byte (1-255).
    letters = st.characters(min_codepoint=1, max_codepoint=0xFF, exclude_characters=" \r\n")
    return st.text(alphabet=letters, min_size=1)


def trim_spaces(text: str) -> str:
    """Remove spaces and tabs from both ends, as HTTP does around a header value."""
    return text.strip(" \t")


def header_values() -> st.SearchStrategy[str]:
    """Make any header value a request may carry: any bytes but CR, LF and NUL."""
    # RFC 9110 section 5.5: spaces and tabs around a value are padding, not
    # part of it, so the value itself never starts or ends with one. Other
    # blank-looking bytes (form feed, \x85, a no-break space) *are* part of
    # it. Random text hardly ever starts or ends with one, so sometimes one
    # is stuck on each end on purpose.
    letters = st.characters(min_codepoint=1, max_codepoint=0xFF, exclude_characters="\r\n")
    ends = st.sampled_from(["", *BLANK_LOOKALIKES])
    return st.tuples(ends, st.text(alphabet=letters), ends).map("".join).map(trim_spaces)


def request_heads() -> st.SearchStrategy[RequestHead]:
    """Make any request head PyWeb accepts: method, target, version and a few headers."""
    headers = st.lists(st.tuples(header_names(), header_values()), max_size=MOST_HEADERS)
    return st.builds(RequestHead, methods(), targets(), versions(), headers)


def write_head(head: RequestHead) -> bytes:
    """Write a request head out the way a browser would, without the final blank line."""
    lines = [f"{head.method} {head.target} {head.version}"]
    lines += [f"{name}: {value}" for name, value in head.headers]
    return "\r\n".join(lines).encode("latin-1")


@st.composite
def damaged_heads(draw: st.DrawFn) -> bytes:
    """Make a good request head, then cut, swap or splice in a few random bytes."""
    raw = bytearray(write_head(draw(request_heads())))
    for _ in range(draw(st.integers(min_value=1, max_value=MOST_EDITS))):
        start = draw(st.integers(min_value=0, max_value=len(raw)))
        cut = draw(st.integers(min_value=0, max_value=3))
        raw[start : start + cut] = draw(st.binary(max_size=3))
    return bytes(raw)


def any_heads() -> st.SearchStrategy[bytes]:
    """Make any bytes at all that might arrive as a head, junk or nearly right."""
    # Pure random bytes almost never get past the first word, so half the
    # time start from a good head and damage it a little.
    return st.one_of(st.binary(), damaged_heads())


def reason_phrases() -> st.SearchStrategy[str]:
    """Make a status line's reason: visible ASCII words, like ``Not Found``."""
    # RFC 9110 section 15: the reason is human-readable text. Stick to
    # visible ASCII, single spaces inside, so http.client reads it as-is.
    word = st.text(alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E), min_size=1)
    return st.lists(word, max_size=3).map(" ".join)


def response_header_values() -> st.SearchStrategy[str]:
    """Make a response header value: visible ASCII, with single spaces inside."""
    # http.client splits lines on more than CR and LF (form feed, \x85 and
    # others), so the response round-trip sticks to RFC 9110's plain
    # "visible characters and spaces" (section 5.5) to compare like with like.
    return reason_phrases()


def response_headers() -> st.SearchStrategy[list[tuple[str, str]]]:
    """Make a short list of response headers, repeats and all."""
    pair = st.tuples(header_names(), response_header_values())
    return st.lists(pair, max_size=MOST_HEADERS)


def plain_texts() -> st.SearchStrategy[str]:
    """Make any text that can be written as UTF-8: emoji, accents, ``?``, ``%``, ``+``..."""
    # Lone surrogates (category Cs) can't be written in UTF-8 at all, so no
    # URL could ever carry one.
    return st.text(alphabet=st.characters(exclude_categories=("Cs",)))


# --- Helpers -----------------------------------------------------------------


class FakeSocket:
    """Just enough of a socket for ``http.client`` to read bytes we already have."""

    def __init__(self, data: bytes) -> None:
        """Remember the bytes the 'server' sent."""
        self._data = data

    def makefile(self, mode: str) -> io.BytesIO:
        """Hand the bytes over as a file, which is all ``http.client`` needs."""
        assert mode == "rb"
        return io.BytesIO(self._data)


def read_or_refuse(raw: bytes) -> int | None:
    """Read a head the way the server does; return the refusal's status, or None if accepted.

    It catches only ``HttpParseError``. Any other exception escapes and fails
    the test, which is the point: the server only knows how to answer that one.
    """
    try:
        head = parse_head(raw, LIMITS)
        body_length(head)
        split_target(head.target)
        wants_keep_alive(head)
    except HttpParseError as refusal:
        return refusal.status
    return None


# --- Properties --------------------------------------------------------------


@given(head=request_heads())
def test_request_head_written_out_reads_back_the_same(head: RequestHead) -> None:
    """Whatever request head you write out, reading it back gives the very same head."""
    parsed = parse_head(write_head(head), LIMITS)
    assert parsed == head
    # Headers stay a list of pairs: repeats are kept, in the order they came,
    # and each name keeps its own capital letters. ``repr`` checks that too.
    assert repr(parsed) == repr(head)


@given(raw=any_heads())
def test_any_bytes_give_a_head_or_http_parse_error(raw: bytes) -> None:
    """Whatever bytes arrive, the server reads a head or raises HttpParseError, nothing else."""
    status = read_or_refuse(raw)
    # A refusal is sent back as the answer, so it must be a real error status.
    assert status is None or (status in STATUS_REASONS and status >= FIRST_ERROR_STATUS)


@given(
    status=st.integers(min_value=FIRST_STATUS, max_value=LAST_STATUS),
    reason=reason_phrases(),
    headers=response_headers(),
)
def test_response_head_reads_back_with_the_standard_library(
    status: int, reason: str, headers: list[tuple[str, str]]
) -> None:
    """Python's own http.client reads back every status, reason and header PyWeb writes."""
    raw = encode_response_head(status, reason, headers)
    reply = HTTPResponse(cast("socket.socket", FakeSocket(raw)))
    reply.begin()
    assert (reply.status, reply.reason) == (status, reason)
    assert reply.getheaders() == headers


@given(path=plain_texts(), query=st.lists(st.tuples(plain_texts(), plain_texts())))
def test_quoted_target_splits_back_into_path_and_query(
    path: str, query: list[tuple[str, str]]
) -> None:
    """A path and query escaped by Python's urllib split back into exactly what went in."""
    target = quote("/" + path) + "?" + urlencode(query)
    assert split_target(target) == ("/" + path, query)
