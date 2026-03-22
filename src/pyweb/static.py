"""Static file serving -- deliver files straight from a folder.

When a browser asks for `/static/style.css`, the static handler finds
`style.css` in the configured directory and sends it back. It's like
a filing cabinet where each file has a known location.
"""

from collections.abc import Callable
from pathlib import Path

from pyweb.request import Request
from pyweb.response import Response, StatusCode, not_found

# Map file extensions to MIME types.
MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

DEFAULT_MIME = "application/octet-stream"


def guess_mime_type(file_path: Path) -> str:
    """Guess the MIME type of a file based on its extension.

    Args:
        file_path: Path to the file.

    Returns:
        The MIME type string.

    """
    return MIME_TYPES.get(file_path.suffix.lower(), DEFAULT_MIME)


def serve_static(directory: str, url_prefix: str = "/static") -> Callable[[Request], Response]:
    """Create a handler that serves files from a directory.

    Args:
        directory: The directory to serve files from.
        url_prefix: The URL prefix to strip (default "/static").

    Returns:
        A handler function for the router.

    """
    base_dir = Path(directory).resolve()

    def handler(request: Request) -> Response:
        # Strip the prefix to get the relative file path.
        relative = request.path.removeprefix(url_prefix).lstrip("/")
        if not relative:
            return not_found("No file specified")

        file_path = (base_dir / relative).resolve()

        # Security: prevent directory traversal (../).
        if not str(file_path).startswith(str(base_dir)):
            return not_found("Access denied")

        if not file_path.is_file():
            return not_found(f"File not found: {relative}")

        content = file_path.read_text(encoding="utf-8")
        mime = guess_mime_type(file_path)
        resp = Response(status=StatusCode.OK, body=content)
        resp.set_content_type(mime)
        return resp

    return handler  # type: ignore[return-value]
