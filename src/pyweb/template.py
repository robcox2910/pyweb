"""Template engine -- form letters with blanks to fill in.

A template is like a form letter: "Dear {{ name }}, your score is
{{ score }}." The engine replaces the {{ blanks }} with real values.

This is a deliberately simple engine for learning. Real engines
(like Jinja2) support loops, conditionals, and more.
"""

import re

# Pattern for {{ variable_name }}.
_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(template: str, context: dict[str, str | int | float | bool]) -> str:
    """Render a template by replacing {{ placeholders }} with values.

    Args:
        template: The template string with {{ name }} placeholders.
        context: A dictionary mapping placeholder names to values.

    Returns:
        The rendered string with all placeholders replaced.

    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in context:
            return str(context[key])
        return match.group(0)  # Leave unreplaced if not in context.

    return _PLACEHOLDER_PATTERN.sub(_replace, template)


def render_file(file_path: str, context: dict[str, str | int | float | bool]) -> str:
    """Load a template file and render it.

    Args:
        file_path: Path to the template file.
        context: Values to substitute into the template.

    Returns:
        The rendered HTML string.

    Raises:
        FileNotFoundError: If the template file doesn't exist.

    """
    from pathlib import Path

    template = Path(file_path).read_text(encoding="utf-8")
    return render(template, context)
