"""Typed routing: map a method and a path to a stored value.

Think of a building directory. You look up a name (the path) and get a
room number (the value). Some entries are exact ("/about"), and some have
blanks to fill in ("/users/<name>"). This module is pure: it does no I/O.

Pattern segments:
    ``literal``       matches that exact text.
    ``<name>``        matches one segment as a string.
    ``<int:name>``    matches one segment of digits (leading ``-`` allowed)
                      and converts it to ``int``.
    ``<path:name>``   matches the rest of the path, slashes included, and
                      must be the last segment.
"""

from dataclasses import dataclass, field

WEBSOCKET = "WEBSOCKET"
"""Pseudo-method used for WebSocket upgrade routes."""

_LITERAL, _STR, _INT, _PATH = "literal", "str", "int", "path"
_CONVERTERS = frozenset({_STR, _INT, _PATH})
_MIN_PARAM_LEN = 3  # "<x>"
_MAX_INT_DIGITS = 18


@dataclass(frozen=True)
class RouteMatch[T]:
    """The outcome of a route lookup.

    Attributes:
        value: The stored value when a route matched, else ``None``.
        params: Captured path parameters, converted to their types.
        allowed: Methods that match the path when the method did not
            (answer 405); empty when nothing matched (answer 404).

    """

    value: T | None
    params: dict[str, object]
    allowed: frozenset[str]


@dataclass(frozen=True)
class _Segment:
    """One parsed pattern segment."""

    kind: str
    text: str  # literal text, or the parameter name


@dataclass(frozen=True)
class _Route[T]:
    """A registered route."""

    method: str
    segments: tuple[_Segment, ...]
    value: T
    order: int


def _bad(pattern: str, why: str) -> ValueError:
    """Build the error for an invalid pattern."""
    return ValueError(f"invalid pattern {pattern!r}: {why}")


def _parse_segment(pattern: str, raw: str) -> _Segment:
    """Parse one pattern segment, raising ``ValueError`` if malformed."""
    if "<" not in raw and ">" not in raw:
        return _Segment(_LITERAL, raw)
    if not (raw.startswith("<") and raw.endswith(">")) or len(raw) < _MIN_PARAM_LEN:
        raise _bad(pattern, f"bad segment {raw!r}")
    kind, _, name = raw[1:-1].rpartition(":")
    kind = kind or _STR
    if kind not in _CONVERTERS:
        raise _bad(pattern, f"unknown converter {kind!r}")
    if not name.isidentifier():
        raise _bad(pattern, f"bad parameter name {name!r}")
    return _Segment(kind, name)


def _parse(pattern: str) -> tuple[_Segment, ...]:
    """Parse a whole pattern into segments."""
    if not pattern.startswith("/"):
        raise _bad(pattern, "must start with '/'")
    segments = tuple(_parse_segment(pattern, raw) for raw in pattern.split("/")[1:])
    names = [s.text for s in segments if s.kind != _LITERAL]
    if len(names) != len(set(names)):
        raise _bad(pattern, "repeated parameter name")
    if any(s.kind == _PATH for s in segments[:-1]):
        raise _bad(pattern, "'path' parameter must be last")
    return segments


def _is_int(text: str) -> bool:
    """Return whether text is an optional minus followed by ASCII digits."""
    digits = text.removeprefix("-")
    return len(digits) <= _MAX_INT_DIGITS and digits.isascii() and digits.isdigit()


def _convert(seg: _Segment, part: str) -> tuple[bool, object]:
    """Return ``(ok, value)`` for one non-path segment against one part."""
    if seg.kind == _LITERAL:
        return part == seg.text, part
    if not part:
        return False, part
    if seg.kind == _INT:
        return (True, int(part)) if _is_int(part) else (False, part)
    return True, part


def _capture(segments: tuple[_Segment, ...], parts: list[str]) -> dict[str, object] | None:
    """Return the captured params if parts fit the segments, else ``None``."""
    params: dict[str, object] = {}
    for index, seg in enumerate(segments):
        if seg.kind == _PATH:
            rest = "/".join(parts[index:])
            if not rest:
                return None
            params[seg.text] = rest
            return params
        if index >= len(parts):
            return None
        ok, value = _convert(seg, parts[index])
        if not ok:
            return None
        if seg.kind != _LITERAL:
            params[seg.text] = value
    return params if len(parts) == len(segments) else None


@dataclass
class RouteTable[T]:
    """Store routes and look them up by method and path."""

    _routes: list[_Route[T]] = field(default_factory=list[_Route[T]])

    def add(self, method: str, pattern: str, value: T) -> None:
        """Register a route.

        Args:
            method: HTTP method, or ``WEBSOCKET`` for upgrade routes.
            pattern: Path pattern such as ``/users/<int:id>``.
            value: The value returned when this route matches.

        Raises:
            ValueError: If the pattern is malformed or the same method and
                pattern were already added.

        """
        segments = _parse(pattern)
        for route in self._routes:
            if route.method == method and route.segments == segments:
                msg = f"duplicate route {method} {pattern}"
                raise ValueError(msg)
        self._routes.append(_Route(method, segments, value, len(self._routes)))

    def match(self, method: str, path: str) -> RouteMatch[T]:
        """Find the route for a method and an already-decoded path.

        Args:
            method: The request method.
            path: The percent-decoded request path.

        Returns:
            A match with the value and params, or ``value=None`` with the
            allowed methods (405) or an empty set (404).

        """
        parts = path.split("/")[1:] if path.startswith("/") else None
        if parts is None:
            return RouteMatch(None, {}, frozenset())
        hits: list[tuple[_Route[T], dict[str, object]]] = []
        for route in self._routes:
            params = _capture(route.segments, parts)
            if params is not None:
                hits.append((route, params))
        wanted = (method, "GET") if method == "HEAD" else (method,)
        for name in wanted:
            best = self._best([h for h in hits if h[0].method == name])
            if best is not None:
                return RouteMatch(best[0].value, best[1], frozenset())
        allowed = {route.method for route, _ in hits}
        if "GET" in allowed:
            allowed.add("HEAD")
        return RouteMatch(None, {}, frozenset(allowed))

    @staticmethod
    def _best(
        hits: list[tuple[_Route[T], dict[str, object]]],
    ) -> tuple[_Route[T], dict[str, object]] | None:
        """Pick the most specific hit: literals first, then first added."""
        if not hits:
            return None
        return min(
            hits,
            key=lambda h: (
                tuple(s.kind != _LITERAL for s in h[0].segments),
                h[0].order,
            ),
        )
