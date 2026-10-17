"""Cached static files -- serve a folder and say "you already have that one".

Think of a library book with a barcode. When you come back and show the
barcode (the ``ETag``), the librarian only has to say "same book, keep
yours" (``304``) instead of handing it over again. The ETag here is the
SHA-256 fingerprint of the file's bytes, so it changes exactly when the
file does.

Only plain files inside the chosen folder are served. Anything that tries
to climb out (``..``), hides (a name starting with ``.``) or points
outside (a symlink) gets a plain ``404``.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path

from pycrypt.hashing import sha256_bytes
from pynet.loop import run_in_thread

from pyweb.messages import HTTPError, HttpRequest, HttpResponse
from pyweb.static import guess_mime_type

__all__ = ["static_handler"]

_NOT_FOUND = 404
_NOT_MODIFIED = 304


def _find(root: Path, relative: str) -> Path | None:
    """Return the real file for a decoded relative path, or ``None``."""
    parts = relative.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") or "\0" in part for part in parts):
        return None
    try:
        candidate = (root / relative).resolve()
    except OSError, ValueError:
        return None
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _load(root: Path, relative: str) -> tuple[Path, bytes] | None:
    """Find and read a file (runs on a worker thread)."""
    found = _find(root, relative)
    if found is None:
        return None
    try:
        return found, found.read_bytes()
    except OSError:
        return None


def _bare(tag: str) -> str:
    """Strip whitespace and a weak ``W/`` marker from an entity tag."""
    tag = tag.strip()
    return tag.removeprefix("W/")


def _matches(header: str, etag: str) -> bool:
    """Return whether an ``If-None-Match`` value matches an ETag (weak compare)."""
    if header.strip() == "*":
        return True
    return any(_bare(tag) == etag for tag in header.split(","))


def static_handler(
    directory: str | Path, *, max_age: int
) -> Callable[[HttpRequest, str], Awaitable[HttpResponse]]:
    """Build a handler serving files from a directory with ETag caching.

    Args:
        directory: The folder to serve.
        max_age: Seconds a browser may keep a file without asking again.

    Returns:
        An async handler taking the request and a ``file`` path parameter.

    """
    root = Path(directory).resolve()

    async def serve(request: HttpRequest, file: str) -> HttpResponse:
        """Answer with the file, a 304, or a 404."""
        loaded = await run_in_thread(_load, root, file)
        if loaded is None:
            raise HTTPError(_NOT_FOUND)
        path, data = loaded
        etag = f'"{sha256_bytes(data)}"'
        response = HttpResponse(_NOT_MODIFIED if _fresh(request, etag) else 200)
        response.headers.set("ETag", etag)
        response.headers.set("Cache-Control", f"public, max-age={max_age}")
        if response.status == _NOT_MODIFIED:
            return response
        response.headers.set("Content-Type", guess_mime_type(path))
        response.body = data
        return response

    return serve


def _fresh(request: HttpRequest, etag: str) -> bool:
    """Return whether the browser already has this exact file."""
    header = request.headers.get("If-None-Match")
    return header is not None and _matches(header, etag)
