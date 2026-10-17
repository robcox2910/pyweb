"""Tests for cached static file serving."""

import hashlib
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from pynet.loop import run
from pynet.loop.testing import FakeClock

from pyweb.app import App
from pyweb.testing import TestClient

STATUS_OK = 200
STATUS_NOT_MODIFIED = 304
STATUS_NOT_FOUND = 404
STATUS_METHOD_NOT_ALLOWED = 405
MAX_AGE = 120
BINARY = bytes(range(256)) * 4


def go[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on a fresh loop with an auto-jumping fake clock."""
    return run(coro, clock=FakeClock(autojump=True))


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """Build a public folder with a secret file beside it."""
    root = tmp_path / "public"
    (root / "sub").mkdir(parents=True)
    (root / "style.css").write_text("body{}", encoding="utf-8")
    (root / "sub" / "page.html").write_text("<p>hi</p>", encoding="utf-8")
    (root / "pic.bin").write_bytes(BINARY)
    (root / ".env").write_text("KEY=1", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    (root / "link.txt").symlink_to(tmp_path / "secret.txt")
    return root


def client_for(site: Path, *, max_age: int = MAX_AGE) -> TestClient:
    """Build a client for an app serving the folder at /static."""
    app = App()
    app.mount_static("/static", site, max_age=max_age)
    return TestClient(app)


class TestServing:
    """Test serving files with caching headers."""

    def test_file_with_etag_and_cache_control(self, site: Path) -> None:
        """Serve a file with its hash as ETag and a max-age."""
        response = go(client_for(site).get("/static/style.css"))
        digest = hashlib.sha256(b"body{}").hexdigest()
        assert response.status == STATUS_OK
        assert response.body == b"body{}"
        assert response.headers.get("ETag") == f'"{digest}"'
        assert response.headers.get("Cache-Control") == f"public, max-age={MAX_AGE}"
        assert response.headers.get("Content-Type") == "text/css"

    def test_default_max_age(self, site: Path) -> None:
        """Default to an hour of caching."""
        app = App()
        app.mount_static("/static", site)
        response = go(TestClient(app).get("/static/style.css"))
        assert response.headers.get("Cache-Control") == "public, max-age=3600"

    def test_nested_file(self, site: Path) -> None:
        """Serve a file from a sub folder."""
        response = go(client_for(site).get("/static/sub/page.html"))
        assert response.status == STATUS_OK
        assert response.text == "<p>hi</p>"

    def test_binary_bytes_intact(self, site: Path) -> None:
        """Send every byte value unchanged."""
        response = go(client_for(site).get("/static/pic.bin"))
        assert response.body == BINARY
        assert response.headers.get("Content-Type") == "application/octet-stream"

    def test_head_has_headers_no_body(self, site: Path) -> None:
        """Answer HEAD with the headers but no body."""
        response = go(client_for(site).head("/static/style.css"))
        assert response.status == STATUS_OK
        assert response.body == b""
        assert response.headers.get("ETag") is not None
        assert response.headers.get("Content-Length") == "6"

    def test_post_is_not_allowed(self, site: Path) -> None:
        """Refuse methods other than GET and HEAD."""
        response = go(client_for(site).post("/static/style.css"))
        assert response.status == STATUS_METHOD_NOT_ALLOWED

    def test_trailing_slash_prefix(self, site: Path) -> None:
        """Accept a prefix written with a trailing slash."""
        app = App()
        app.mount_static("/static/", site)
        assert go(TestClient(app).get("/static/style.css")).status == STATUS_OK


class TestConditional:
    """Test If-None-Match handling."""

    @staticmethod
    def etag(site: Path) -> str:
        """Fetch the current ETag of style.css."""
        response = go(client_for(site).get("/static/style.css"))
        return response.headers.get("ETag") or ""

    @pytest.mark.parametrize("form", ["{tag}", "W/{tag}", "*", '"nope", {tag}', "{tag} , W/{tag}"])
    def test_match_gives_304(self, site: Path, form: str) -> None:
        """Answer 304 with no body when the validator matches."""
        tag = self.etag(site)
        response = go(
            client_for(site).get(
                "/static/style.css", headers={"If-None-Match": form.format(tag=tag)}
            )
        )
        assert response.status == STATUS_NOT_MODIFIED
        assert response.body == b""
        assert response.headers.get("ETag") == tag

    def test_other_tag_gives_200(self, site: Path) -> None:
        """Send the file when the validator is stale."""
        response = go(client_for(site).get("/static/style.css", headers={"If-None-Match": '"old"'}))
        assert response.status == STATUS_OK
        assert response.body == b"body{}"


class TestRefusals:
    """Test paths that must never be served."""

    @pytest.mark.parametrize(
        "target",
        [
            "/static/%2e%2e/secret.txt",
            "/static/..%2fsecret.txt",
            "/static/.env",
            "/static/sub/../../secret.txt",
            "/static/../secret.txt",
            "/static/link.txt",
            "/static/sub",
            "/static/missing.css",
            "/static/sub//page.html",
            "/static/sub/./page.html",
            "/static/%2Eenv",
            "/static/a%00b",
        ],
    )
    def test_traversal_variants_are_404(self, site: Path, target: str) -> None:
        """Answer 404 for traversal, hidden files, symlinks and non-files."""
        response = go(client_for(site).get(target))
        assert response.status == STATUS_NOT_FOUND
        assert b"secret" not in response.body
