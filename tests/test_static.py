"""Tests for static file serving.

Static files are like a filing cabinet -- each file has a known
location and the server delivers it as-is.
"""

from pathlib import Path

from pyweb.request import Request
from pyweb.response import Response
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
        assert resp.body == b"body { color: red; }"
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

    def test_sibling_prefix_dir_blocked(self, tmp_path: Path) -> None:
        """A sibling dir sharing a name prefix must NOT be reachable."""
        base = tmp_path / "static"
        base.mkdir()
        secret_dir = tmp_path / "static_secret"
        secret_dir.mkdir()
        (secret_dir / "secret.txt").write_text("top secret", encoding="utf-8")

        handler = serve_static(str(base))
        req = Request(method="GET", path="/static/../static_secret/secret.txt")
        resp = handler(req)
        assert resp.status == STATUS_404
        assert b"top secret" not in resp.to_bytes()


class TestBinaryFiles:
    """Pictures and icons are bytes, not text -- they must arrive untouched."""

    def test_png_is_served_byte_for_byte(self, tmp_path: Path) -> None:
        """Regression: images used to crash with a UnicodeDecodeError (500)."""
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\x80"
        (tmp_path / "logo.png").write_bytes(png)
        handler = serve_static(str(tmp_path))
        resp = handler(Request(method="GET", path="/static/logo.png"))
        assert resp.status == STATUS_200
        assert resp.headers["Content-Type"] == "image/png"
        assert resp.body == png
        wire = resp.to_bytes()
        assert wire.endswith(b"\r\n\r\n" + png)
        assert f"Content-Length: {len(png)}".encode() in wire

    def test_text_body_still_encodes_as_utf8(self) -> None:
        """A text body is still sent as UTF-8 with the right byte length."""
        resp = Response(status=STATUS_200, body="héllo")
        wire = resp.to_bytes()
        assert wire.endswith("héllo".encode())
        assert b"Content-Length: 6" in wire
