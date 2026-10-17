"""Tests for escaping HTML templates."""

from pathlib import Path

import pytest

from pyweb.html import escape, html_template, load_template


class TestEscape:
    """Test turning risky characters into entities."""

    @pytest.mark.parametrize(
        ("raw", "safe"),
        [
            ("plain", "plain"),
            ("<script>alert(1)</script>", "&lt;script&gt;alert(1)&lt;/script&gt;"),
            ('say "hi"', "say &quot;hi&quot;"),
            ("it's", "it&#x27;s"),
            ("a & b", "a &amp; b"),
            (42, "42"),
        ],
    )
    def test_entities(self, raw: object, safe: str) -> None:
        """Replace the five dangerous characters."""
        assert escape(raw) == safe


class TestHtmlTemplate:
    """Test filling blanks safely."""

    def test_values_are_escaped(self) -> None:
        """Escape a value that looks like a tag."""
        out = html_template("<p>{{ name }}</p>", {"name": "<script>"})
        assert out == "<p>&lt;script&gt;</p>"

    def test_safe_filter_is_raw(self) -> None:
        """Leave a value alone when marked safe."""
        assert html_template("{{ bits|safe }}", {"bits": "<b>x</b>"}) == "<b>x</b>"

    def test_whitespace_is_optional(self) -> None:
        """Accept braces with or without spaces."""
        assert html_template("{{a}}{{  a | safe  }}", {"a": "&"}) == "&amp;&"

    def test_missing_name(self) -> None:
        """Raise KeyError naming the missing blank."""
        with pytest.raises(KeyError, match="who"):
            html_template("{{ who }}", {})

    def test_unknown_filter(self) -> None:
        """Reject a filter that does not exist."""
        with pytest.raises(ValueError, match="upper"):
            html_template("{{ a|upper }}", {"a": "x"})

    def test_inserted_text_is_not_reprocessed(self) -> None:
        """Do not expand blanks that appear inside a value."""
        assert html_template("{{ a }}", {"a": "{{ b }}"}) == "{{ b }}"

    def test_load_template(self, tmp_path: Path) -> None:
        """Read a template file as text."""
        path = tmp_path / "t.html"
        path.write_text("<h1>{{ x }}</h1>", encoding="utf-8")
        assert load_template(path) == "<h1>{{ x }}</h1>"
        assert load_template(str(path)) == "<h1>{{ x }}</h1>"
