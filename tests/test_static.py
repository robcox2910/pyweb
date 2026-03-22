"""Tests for static file serving.

Static files are like a filing cabinet -- each file has a known
location and the server delivers it as-is.
"""

from pathlib import Path

from pyweb.request import Request
from pyweb.static import guess_mime_type, serve_static

STATUS_200 = 200
STATUS_404 = 404


class TestMimeType:
    """Verify MIME type guessing."""

    def test_html(self) -> None:
        """HTML files should return text/html."""
        assert "text/html" in guess_mime_type(Path("page.html"))

    def test_css(self) -> None:
        """CSS files should return text/css."""
        assert guess_mime_type(Path("style.css")) == "text/css"

    def test_js(self) -> None:
        """JS files should return application/javascript."""
        assert guess_mime_type(Path("app.js")) == "application/javascript"

    def test_unknown(self) -> None:
        """Unknown extensions should return application/octet-stream."""
        assert guess_mime_type(Path("file.xyz")) == "application/octet-stream"


class TestServeStatic:
    """Verify static file serving."""

    def test_serve_existing_file(self, tmp_path: Path) -> None:
        """An existing file should be served with correct MIME type."""
        (tmp_path / "style.css").write_text("body { color: red; }", encoding="utf-8")
        handler = serve_static(str(tmp_path))
        req = Request(method="GET", path="/static/style.css")
        resp = handler(req)
        assert resp.status == STATUS_200
        assert "body { color: red; }" in resp.body
        assert resp.headers["Content-Type"] == "text/css"

    def test_missing_file_returns_404(self, tmp_path: Path) -> None:
        """A missing file should return 404."""
        handler = serve_static(str(tmp_path))
        req = Request(method="GET", path="/static/missing.css")
        resp = handler(req)
        assert resp.status == STATUS_404

    def test_no_file_specified(self, tmp_path: Path) -> None:
        """Requesting the prefix alone should return 404."""
        handler = serve_static(str(tmp_path))
        req = Request(method="GET", path="/static")
        resp = handler(req)
        assert resp.status == STATUS_404

    def test_directory_traversal_blocked(self, tmp_path: Path) -> None:
        """Path traversal attempts should be blocked."""
        handler = serve_static(str(tmp_path))
        req = Request(method="GET", path="/static/../../../etc/passwd")
        resp = handler(req)
        assert resp.status == STATUS_404
