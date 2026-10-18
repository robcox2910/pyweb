"""Model checks -- the route table and Headers must behave like plain lists.

A property test checks one rule for many inputs. A *model check* goes
further: Hypothesis invents a random *story* -- add this route, look up that
path, add the same route again -- and plays it on two things at once:

- the real ``RouteTable``;
- a **model**: a plain list of ``(method, pattern, value)`` rules, which we
  read the slow, obvious way: sort the rules by how specific they are, then
  take the first one whose pattern fits the path.

After every look-up the two must agree on the answer: which route, which
parameters, and -- when nothing fits -- whether it's a 404 or a 405 and
which methods the 405 lists. If they ever disagree, Hypothesis shrinks the
story to the shortest list of steps that still goes wrong.

The model is deliberately *not* a copy of ``RouteTable``'s code. It turns
each pattern into a regular expression (``re``) instead of walking
segments, and it sorts instead of picking a minimum.

A second machine does the same for ``Headers`` against a plain list of
``(name, value)`` pairs: names match whatever their capital letters, and
repeated headers keep their order.

``RouteTable`` has no way to remove a route (an app's routes are fixed once
it starts), so the stories only add and look up.

See ``docs/property-testing.md`` for the walk-through.
"""

import re
import unittest
from typing import cast

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule

from pyweb.messages import Headers
from pyweb.routing import WEBSOCKET, RouteTable
from tests.strategies import header_names

# The thorough profile runs 2,000 examples of each test, and shrinking a
# failure takes a few seconds more, so these get longer than the 10 seconds
# pyproject.toml gives every other test.
pytestmark = pytest.mark.timeout(300)

# --- Route strategies: a tiny alphabet, so routes and paths often collide ----

# A handful of path pieces. "7" and "-2" fit <int:...>; nineteen 9s is one
# digit too long for it (routing.py caps ints at 18 digits); "" is the empty
# piece between two slashes, as in "/a//b".
PATH_PIECES = ["a", "b", "7", "-2", "9" * 19, ""]
LITERALS = ["a", "b", "7", ""]
MOST_SEGMENTS = 3


def route_methods() -> st.SearchStrategy[str]:
    """Pick a method a route can be added for, WebSocket upgrades included."""
    return st.sampled_from(["GET", "HEAD", "POST", WEBSOCKET])


def request_methods() -> st.SearchStrategy[str]:
    """Pick a method a request can arrive with, including one no route ever uses."""
    return st.sampled_from(["GET", "HEAD", "POST", "DELETE", WEBSOCKET])


@st.composite
def patterns(draw: st.DrawFn) -> str:
    """Make a route pattern like ``/a/<p1>``, ``/<int:p0>`` or ``/b/<path:p1>``."""
    segments: list[str] = []
    count = draw(st.integers(min_value=1, max_value=MOST_SEGMENTS))
    for index in range(count):
        kinds = ["literal", "str", "int"] + (["path"] if index == count - 1 else [])
        kind = draw(st.sampled_from(kinds))
        if kind == "literal":
            segments.append(draw(st.sampled_from(LITERALS)))
        elif kind == "str":
            segments.append(f"<p{index}>")
        else:
            # Each parameter is named after its position, so names never repeat.
            segments.append(f"<{kind}:p{index}>")
    return "/" + "/".join(segments)


def paths() -> st.SearchStrategy[str]:
    """Make a request path from the tiny alphabet, like ``/``, ``/a/7`` or ``/b//-2``."""
    pieces = st.lists(st.sampled_from(PATH_PIECES), max_size=MOST_SEGMENTS + 1)
    return pieces.map("/".join).map("/".__add__)


# --- The model: a list of rules, read the slow and obvious way ---------------

# What each kind of pattern piece matches, as a regular expression.
PARAMETER = re.compile(r"<(?:(str|int|path):)?(\w+)>")
PIECE_REGEX = {"str": r"[^/]+", "int": r"-?[0-9]{1,18}", "path": r".+"}


def pattern_regex(pattern: str) -> re.Pattern[str]:
    """Turn a pattern like ``/a/<int:id>`` into a regex like ``/a/(?P<id>-?[0-9]{1,18})``."""
    regex = ""
    for piece in re.split(r"(<[^>]*>)", pattern):
        found = PARAMETER.fullmatch(piece)
        if found is None:
            regex += re.escape(piece)
        else:
            kind, name = found.group(1) or "str", found.group(2)
            regex += f"(?P<{name}>{PIECE_REGEX[kind]})"
    return re.compile(regex, re.DOTALL)


def specificity(pattern: str) -> list[bool]:
    """Say, piece by piece, whether a pattern has a blank there (literals sort first)."""
    return [piece.startswith("<") for piece in pattern.split("/")[1:]]


def fill(pattern: str, path: str) -> dict[str, object] | None:
    """Return the blanks a path fills in a pattern (ints as ints), or None if it doesn't fit."""
    found = pattern_regex(pattern).fullmatch(path)
    if found is None:
        return None
    return {
        name: int(text) if f"<int:{name}>" in pattern else text
        for name, text in found.groupdict().items()
    }


# why: 20 steps find the same bugs; 50 (the default) is most of the run time
@settings(stateful_step_count=20)
class RouteMachine(RuleBasedStateMachine):
    """Add random routes to a RouteTable and a plain list, and look up paths in both."""

    # Routes added earlier in the story, so later steps can add one again or
    # build a path that's sure to fit it.
    routes = Bundle("routes")

    def __init__(self) -> None:
        """Start with an empty route table and an empty list of rules."""
        super().__init__()
        self.table: RouteTable[int] = RouteTable()
        self.model: list[tuple[str, str, int]] = []

    def expected(self, method: str, path: str) -> tuple[int | None, dict[str, object], set[str]]:
        """Answer a look-up from the list: (value, params, allowed methods)."""
        fits = [(m, p, v) for m, p, v in self.model if fill(p, path) is not None]
        # HEAD is answered by a GET route when there's no HEAD route of its own.
        for wanted in [method, "GET"] if method == "HEAD" else [method]:
            # sorted() keeps equals in the order they were added: first added wins.
            for m, p, v in sorted(fits, key=lambda rule: specificity(rule[1])):
                if m == wanted:
                    return v, cast("dict[str, object]", fill(p, path)), set()
        allowed = {m for m, _, _ in fits}
        return None, {}, allowed | ({"HEAD"} if "GET" in allowed else set())

    def check(self, method: str, path: str) -> None:
        """Look a path up in both and check they give the same answer."""
        found = self.table.match(method, path)
        value, params, allowed = self.expected(method, path)
        # repr tells 7 from "7", so it checks <int:...> really gave an int.
        assert (found.value, repr(found.params), found.allowed) == (value, repr(params), allowed)
        # The answer is the caller's to keep: scribbling on it must not change
        # the next answer (the invariant would notice).
        found.params["scribble"] = "added by the caller"

    @rule(target=routes, method=route_methods(), pattern=patterns())
    def add_route(self, method: str, pattern: str) -> tuple[str, str]:
        """Add a route to both, or check the table refuses a repeat the list already has."""
        if any((m, p) == (method, pattern) for m, p, _ in self.model):
            with pytest.raises(ValueError, match="duplicate"):
                self.table.add(method, pattern, len(self.model))
        else:
            assert self.table.add(method, pattern, len(self.model)) is None
            self.model.append((method, pattern, len(self.model)))
        return method, pattern

    @rule(route=routes)
    def add_route_again(self, route: tuple[str, str]) -> None:
        """Adding a route that's already there is refused, and changes nothing."""
        method, pattern = route
        with pytest.raises(ValueError, match="duplicate"):
            self.table.add(method, pattern, -1)

    @rule(method=request_methods(), path=paths())
    def look_up_any_path(self, method: str, path: str) -> None:
        """Look up a random path: it may fit one route, several, or none."""
        self.check(method, path)

    @rule(method=request_methods(), route=routes, pieces=st.data())
    def look_up_a_path_that_fits(
        self, method: str, route: tuple[str, str], pieces: st.DataObject
    ) -> None:
        """Fill a known route's blanks with random pieces, then look that path up."""
        path = PARAMETER.sub(lambda _: pieces.draw(st.sampled_from(PATH_PIECES)), route[1])
        self.check(method, path)

    @invariant()
    def every_route_still_answers_the_same(self) -> None:
        """After every step, each route's own path (blanks filled with 7) answers the same."""
        for method, pattern, _ in self.model:
            # Its own method finds it; DELETE (no route uses it) gets a 405.
            for asked in (method, "DELETE"):
                self.check(asked, PARAMETER.sub("7", pattern))


# pytest runs this like any other test class: one run plays 100 random stories.
# (Hypothesis doesn't say what type ``TestCase`` is, so ``cast`` tells pyright.)
TestRouteModel = cast("type[unittest.TestCase]", RouteMachine.TestCase)  # pyright: ignore[reportUnknownMemberType]


# --- Headers: a case-insensitive multi-dict, against a list of pairs ---------

COMMON_NAMES = ["host", "accept", "set-cookie"]


@st.composite
def any_case_names(draw: st.DrawFn) -> str:
    """Make a common header name in random capitals: ``HOST``, ``Host``, ``hOsT``..."""
    name = draw(st.sampled_from(COMMON_NAMES))
    upper = draw(st.lists(st.booleans(), min_size=len(name), max_size=len(name)))
    return "".join(ch.upper() if up else ch for ch, up in zip(name, upper, strict=True))


def header_texts() -> st.SearchStrategy[str]:
    """Make any header value Headers accepts: any text without CR, LF or NUL."""
    # messages.py refuses CR, LF and NUL, which could split one header in two.
    return st.text(
        alphabet=st.characters(exclude_characters="\r\n\x00", exclude_categories=("Cs",))
    )


def bad_header_texts() -> st.SearchStrategy[str]:
    """Make a header value Headers must refuse: text with a CR, LF or NUL in it."""
    return st.tuples(header_texts(), st.sampled_from("\r\n\x00"), header_texts()).map("".join)


def same_name(one: str, other: str) -> bool:
    """Say whether two header names are the same, ignoring capitals."""
    return one.casefold() == other.casefold()


# why: 20 steps find the same bugs; 50 (the default) is most of the run time
@settings(stateful_step_count=20)
class HeadersMachine(RuleBasedStateMachine):
    """Add, set and remove random headers on Headers and on a list of pairs."""

    names = Bundle("names")

    def __init__(self) -> None:
        """Start with no headers in either."""
        super().__init__()
        self.headers = Headers()
        self.model: list[tuple[str, str]] = []

    @rule(target=names, name=st.one_of(any_case_names(), header_names()), value=header_texts())
    def add_header(self, name: str, value: str) -> str:
        """Add a header to both; any existing ones with that name stay put."""
        assert self.headers.add(name, value) is None
        self.model.append((name, value))
        return name

    @rule(name=st.one_of(names, any_case_names()), value=header_texts())
    def set_header(self, name: str, value: str) -> None:
        """Set a header: every old value goes, and the new one joins the end."""
        assert self.headers.set(name, value) is None
        self.model = [(k, v) for k, v in self.model if not same_name(k, name)]
        self.model.append((name, value))

    @rule(name=st.one_of(names, any_case_names(), header_names()))
    def remove_header(self, name: str) -> None:
        """Remove every header with that name, whatever its capitals."""
        assert self.headers.remove(name) is None
        self.model = [(k, v) for k, v in self.model if not same_name(k, name)]

    @rule(name=st.one_of(names, any_case_names()), value=bad_header_texts())
    def refuse_bad_value(self, name: str, value: str) -> None:
        """Refuse a value with CR, LF or NUL in it, and change nothing."""
        with pytest.raises(ValueError, match="not allowed"):
            self.headers.add(name, value)
        with pytest.raises(ValueError, match="not allowed"):
            self.headers.set(name, value)

    @rule(name=st.one_of(names, any_case_names(), header_names()), default=header_texts())
    def read_header(self, name: str, default: str) -> None:
        """Read a header every way there is, in any capitals, and match the list."""
        values = [v for k, v in self.model if same_name(k, name)]
        for asked in (name, name.upper(), name.lower()):
            assert self.headers.get_all(asked) == values
            assert self.headers.get(asked) == (values[0] if values else None)
            assert self.headers.get(asked, default) == (values[0] if values else default)
            assert (asked in self.headers) == bool(values)

    @invariant()
    def every_header_in_order(self) -> None:
        """All the headers come out in the order they went in, as a fresh copy."""
        items = self.headers.items()
        assert items == self.model
        items.append(("X-Sneaky", "added to the copy"))
        assert self.headers.items() == self.model


# pytest runs this like any other test class: one run plays 100 random stories.
TestHeadersModel = cast("type[unittest.TestCase]", HeadersMachine.TestCase)  # pyright: ignore[reportUnknownMemberType]
