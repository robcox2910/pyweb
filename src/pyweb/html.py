"""Escaping templates -- fill in blanks without letting a name become a tag.

Imagine a name tag sticker. If a guest writes ``<script>`` on it, we want
the sticker to *show* those letters, not to *do* what they say. Escaping
swaps the risky characters for harmless look-alikes (``&lt;`` for ``<``)
so a browser prints them instead of obeying them.

``{{ name }}`` is escaped; ``{{ name|safe }}`` is inserted as-is, for HTML
you wrote yourself and trust.
"""

import html as html_lib
import re
from collections.abc import Mapping
from pathlib import Path

__all__ = ["escape", "html_template", "load_template"]

_BLANK = re.compile(r"\{\{\s*([A-Za-z_]\w*)\s*(?:\|\s*(\w+)\s*)?\}\}")


def escape(value: object) -> str:
    """Return the text of a value with ``& < > " '`` turned into entities."""
    return html_lib.escape(str(value), quote=True)


def html_template(text: str, context: Mapping[str, object]) -> str:
    """Fill the ``{{ name }}`` blanks of a template, escaping each value.

    Args:
        text: The template text.
        context: The values for the blanks.

    Returns:
        The finished text. Inserted values are never searched for more blanks.

    Raises:
        ValueError: If a blank uses a filter other than ``safe``.
        KeyError: If a blank's name is not in the context.

    """

    def fill(match: re.Match[str]) -> str:
        """Return the replacement for one blank."""
        name, filter_name = match.group(1), match.group(2)
        if filter_name not in {None, "safe"}:
            msg = f"unknown filter {filter_name!r} for {name!r}"
            raise ValueError(msg)
        value = context[name]
        return str(value) if filter_name == "safe" else escape(value)

    return _BLANK.sub(fill, text)


def load_template(path: str | Path) -> str:
    """Read a template file as UTF-8 text."""
    return Path(path).read_text(encoding="utf-8")
