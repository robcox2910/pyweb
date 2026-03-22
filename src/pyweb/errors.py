"""Custom exceptions for PyWeb."""


class PyWebError(Exception):
    """Base exception for all PyWeb errors."""


class ParseError(PyWebError):
    """Raise when an HTTP request cannot be parsed."""
