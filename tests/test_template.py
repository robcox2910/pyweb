"""Tests for the template engine.

Templates are form letters with blanks. These tests verify that
{{ placeholders }} are replaced correctly.
"""

from pathlib import Path

from pyweb.template import render, render_file


class TestRender:
    """Verify template rendering."""

    def test_simple_replacement(self) -> None:
        """A single placeholder should be replaced."""
        result = render("Hello, {{ name }}!", {"name": "Alice"})
        assert result == "Hello, Alice!"

    def test_multiple_replacements(self) -> None:
        """Multiple placeholders should all be replaced."""
        result = render("{{ name }} scored {{ score }}", {"name": "Bob", "score": 95})
        assert result == "Bob scored 95"

    def test_missing_key_preserved(self) -> None:
        """Placeholders without a matching key should stay unchanged."""
        result = render("Hello, {{ name }}!", {})
        assert result == "Hello, {{ name }}!"

    def test_no_placeholders(self) -> None:
        """A template with no placeholders should be returned as-is."""
        result = render("Just plain text", {"key": "value"})
        assert result == "Just plain text"

    def test_integer_value(self) -> None:
        """Integer values should be converted to strings."""
        result = render("Count: {{ n }}", {"n": 42})
        assert result == "Count: 42"

    def test_boolean_value(self) -> None:
        """Boolean values should be converted to strings."""
        result = render("Active: {{ flag }}", {"flag": True})
        assert result == "Active: True"

    def test_whitespace_in_braces(self) -> None:
        """Extra whitespace inside {{ }} should be handled."""
        result = render("{{  name  }}", {"name": "Alice"})
        assert result == "Alice"


class TestRenderFile:
    """Verify file-based template rendering."""

    def test_render_file(self, tmp_path: Path) -> None:
        """A template file should be loaded and rendered."""
        template_file = tmp_path / "greeting.html"
        template_file.write_text("Hello, {{ name }}!", encoding="utf-8")
        result = render_file(str(template_file), {"name": "Alice"})
        assert result == "Hello, Alice!"

    def test_missing_file_raises(self) -> None:
        """A missing template file should raise FileNotFoundError."""
        import pytest

        with pytest.raises(FileNotFoundError):
            render_file("/nonexistent/template.html", {})
