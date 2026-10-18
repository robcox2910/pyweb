"""Tests for the typed route table."""

import pytest

from pyweb.routing import WEBSOCKET, RouteMatch, RouteTable

ANSWER = 42
NEGATIVE = -3
EIGHTEEN = 18
HUGE = 5000
FIRST = 1
SECOND = 2


class TestMatching:
    """Test finding routes by method and path."""

    def test_literal_route_matches(self) -> None:
        """Match a plain literal path."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/hello", "hi")
        found = table.match("GET", "/hello")
        assert found.value == "hi"
        assert found.params == {}

    def test_root_route(self) -> None:
        """Match the root path."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/", "root")
        assert table.match("GET", "/").value == "root"

    def test_literal_beats_param(self) -> None:
        """Prefer a literal route over a parameter route."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/users/<name>", "param")
        table.add("GET", "/users/me", "literal")
        assert table.match("GET", "/users/me").value == "literal"
        found = table.match("GET", "/users/bob")
        assert found.value == "param"
        assert found.params == {"name": "bob"}

    def test_first_added_wins_between_params(self) -> None:
        """Prefer the earlier of two equally specific routes."""
        table: RouteTable[int] = RouteTable()
        table.add("GET", "/x/<int:a>", FIRST)
        table.add("GET", "/x/<b>", SECOND)
        assert table.match("GET", "/x/7").value == FIRST
        assert table.match("GET", "/x/abc").value == SECOND

    def test_literals_win_piece_by_piece_from_the_left(self) -> None:
        """Compare pieces left to right: a literal first piece beats a blank one."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/<x>/b", "blank first")
        table.add("GET", "/a/<y>", "literal first")
        assert table.match("GET", "/a/b").value == "literal first"
        assert table.match("GET", "/c/b").value == "blank first"

    def test_int_converts(self) -> None:
        """Convert digits, including a leading minus, to int."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/n/<int:id>", "n")
        assert table.match("GET", "/n/42").params == {"id": ANSWER}
        assert table.match("GET", "/n/-3").params == {"id": NEGATIVE}

    @pytest.mark.parametrize("path", ["/n/4x", "/n/", "/n/-", "/n/1.5"])
    def test_int_rejects_non_digits(self, path: str) -> None:
        """Answer not found for non-integer text."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/n/<int:id>", "n")
        found = table.match("GET", path)
        assert found.value is None
        assert found.allowed == frozenset()

    def test_int_digit_cap(self) -> None:
        """Match 18 digits but not an absurdly long number."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/n/<int:id>", "n")
        assert table.match("GET", "/n/" + "9" * EIGHTEEN).params == {"id": int("9" * EIGHTEEN)}
        found = table.match("GET", "/n/" + "9" * HUGE)
        assert found.value is None
        assert found.allowed == frozenset()

    def test_path_param_spans_slashes(self) -> None:
        """Capture the rest of the path, slashes included."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/files/<path:rest>", "f")
        assert table.match("GET", "/files/a/b/c").params == {"rest": "a/b/c"}

    def test_segment_param_does_not_span_slashes(self) -> None:
        """Keep a plain parameter within one segment."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/u/<name>", "u")
        assert table.match("GET", "/u/a/b").value is None

    def test_unknown_path_is_not_found(self) -> None:
        """Answer an empty allowed set when nothing matches."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/a", "a")
        assert table.match("GET", "/b") == RouteMatch(None, {}, frozenset())

    def test_trailing_slash_is_distinct(self) -> None:
        """Treat a trailing slash as a different path."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/a", "a")
        assert table.match("GET", "/a/").value is None


class TestMethods:
    """Test method handling."""

    def test_wrong_method_lists_allowed(self) -> None:
        """Report the methods that would have matched."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/a", "g")
        table.add("POST", "/a", "p")
        found = table.match("DELETE", "/a")
        assert found.value is None
        assert found.allowed == frozenset({"GET", "HEAD", "POST"})

    def test_head_falls_back_to_get(self) -> None:
        """Serve HEAD from the GET route."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/a", "g")
        assert table.match("HEAD", "/a").value == "g"

    def test_explicit_head_wins(self) -> None:
        """Prefer an explicit HEAD route."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/a", "g")
        table.add("HEAD", "/a", "h")
        assert table.match("HEAD", "/a").value == "h"

    def test_websocket_is_separate(self) -> None:
        """Keep upgrade routes apart from GET routes."""
        table: RouteTable[str] = RouteTable()
        table.add(WEBSOCKET, "/ws", "w")
        assert table.match(WEBSOCKET, "/ws").value == "w"
        assert table.match("GET", "/ws").allowed == frozenset({WEBSOCKET})


class TestValidation:
    """Test rejection of bad routes."""

    def test_duplicate_raises(self) -> None:
        """Reject the same method and pattern twice."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/a", "x")
        with pytest.raises(ValueError, match="duplicate"):
            table.add("GET", "/a", "y")

    def test_same_pattern_other_method_is_fine(self) -> None:
        """Allow one pattern under two methods."""
        table: RouteTable[str] = RouteTable()
        table.add("GET", "/a", "x")
        table.add("POST", "/a", "y")

    @pytest.mark.parametrize(
        "pattern",
        [
            "/<path:x>/more",
            "no-slash",
            "/<>",
            "/<float:x>",
            "/<int:>",
            "/<a>/<a>",
            "/a<b>",
            "/<1x>",
            "/<a",
        ],
    )
    def test_bad_patterns_raise(self, pattern: str) -> None:
        """Reject malformed patterns."""
        table: RouteTable[str] = RouteTable()
        with pytest.raises(ValueError, match="pattern"):
            table.add("GET", pattern, "x")
