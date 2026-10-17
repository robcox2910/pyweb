"""Tests for CSRF checks, bearer tokens and keeping secrets out of logs."""

import logging
from typing import Any

import pytest

from pyweb.testing import TestClient, TestResponse
from tests.aio._auth_helpers import (
    ADA_WORD,
    BOB_WORD,
    FORBIDDEN,
    MAX_FAILURES,
    OK,
    UNAUTHORIZED,
    Site,
    build,
    go,
    log_in,
)


class TestCsrf:
    """Test that cookie sessions need a CSRF token for unsafe methods."""

    def post_with(self, **options: Any) -> TestResponse:
        """Log in as Ada and POST /jobs with the given options."""
        client = TestClient(build().app)

        async def flow() -> TestResponse:
            """Log in, then post."""
            csrf = await log_in(client)
            for key, value in list(options.items()):
                options[key] = {k: (csrf if v == "RIGHT" else v) for k, v in value.items()}
            return await client.post("/jobs", **options)

        return go(flow())

    def test_missing_token_is_403(self) -> None:
        """Refuse a cookie POST with no token."""
        response = self.post_with()
        assert response.status == FORBIDDEN
        assert response.json() == {"error": "Forbidden", "message": "csrf"}

    def test_wrong_header_is_403(self) -> None:
        """Refuse a cookie POST with the wrong header token."""
        assert self.post_with(headers={"X-CSRF-Token": "wrong"}).status == FORBIDDEN

    def test_right_header_is_200(self) -> None:
        """Accept a cookie POST with the right header token."""
        assert self.post_with(headers={"X-CSRF-Token": "RIGHT"}).status == OK

    def test_right_form_field_is_200(self) -> None:
        """Accept a cookie POST with the right form field."""
        assert self.post_with(form={"csrf_token": "RIGHT"}).status == OK

    def test_wrong_form_field_is_403(self) -> None:
        """Refuse a cookie POST with the wrong form field."""
        assert self.post_with(form={"csrf_token": "wrong"}).status == FORBIDDEN

    def test_json_field_does_not_count(self) -> None:
        """Ignore a csrf_token inside a JSON body: only the header counts there."""
        assert self.post_with(json={"csrf_token": "RIGHT"}).status == FORBIDDEN

    def test_wrong_header_beats_right_form_field(self) -> None:
        """Use the header when it is present, even if the form field is right."""
        response = self.post_with(headers={"X-CSRF-Token": "wrong"}, form={"csrf_token": "RIGHT"})
        assert response.status == FORBIDDEN

    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
    def test_other_unsafe_methods_are_checked(self, method: str) -> None:
        """Refuse PUT, PATCH and DELETE with no token too."""
        client = TestClient(build().app)

        async def flow() -> int:
            """Log in, then send the method without a token."""
            await log_in(client)
            path = "/jobs/1" if method == "PUT" else "/jobs"
            return (await client.request(method, path)).status

        assert go(flow()) == FORBIDDEN

    def test_session_without_csrf_claim_is_403(self) -> None:
        """Refuse unsafe methods for a session that has no csrf claim."""
        site = build()
        client = TestClient(site.app)
        client.cookies["session"] = site.signer.sign({"kind": "session", "user": "ada"})

        async def flow() -> tuple[int, int]:
            """GET still works; POST is refused."""
            got = (await client.get("/jobs")).status
            posted = await client.post("/jobs", headers={"X-CSRF-Token": ""})
            return got, posted.status

        assert go(flow()) == (OK, FORBIDDEN)

    def test_anonymous_post_needs_no_token(self) -> None:
        """Let a stranger post the login form without a token."""
        response = go(
            TestClient(build().app).post("/login", form={"name": "ada", "password": ADA_WORD})
        )
        assert response.status == OK


class TestBearer:
    """Test API tokens sent as Authorization: Bearer."""

    def fetch_token(self, site: Site) -> str:
        """Log in as Ada and fetch an API token."""
        client = TestClient(site.app)

        async def flow() -> str:
            """Log in, then ask for a token."""
            await log_in(client)
            body = (await client.get("/token")).json()
            assert isinstance(body, dict)
            token = body["token"]
            assert isinstance(token, str)
            return token

        return go(flow())

    def test_bearer_works_and_is_csrf_exempt(self) -> None:
        """Accept a bearer token for GET and for POST without any CSRF token."""
        site = build()
        bearer = {"Authorization": f"Bearer {self.fetch_token(site)}"}
        client = TestClient(site.app)
        got = go(client.get("/jobs", headers=bearer))
        posted = go(client.post("/jobs", headers=bearer))
        assert got.json() == {"user": "ada"}
        assert posted.status == OK

    def test_session_token_is_not_an_api_token(self) -> None:
        """Refuse a session-kind token sent as a bearer token."""
        site = build()
        token = site.signer.sign({"kind": "session", "user": "ada", "csrf": "x"})
        response = go(
            TestClient(site.app).get("/jobs", headers={"Authorization": f"Bearer {token}"})
        )
        assert response.status == UNAUTHORIZED

    def test_api_token_is_not_a_session_cookie(self) -> None:
        """Treat an api-kind token in the cookie as anonymous."""
        site = build()
        client = TestClient(site.app)
        client.cookies["session"] = self.fetch_token(site)
        assert go(client.get("/jobs")).status == UNAUTHORIZED

    def test_bad_bearer_does_not_fall_back_to_cookie(self) -> None:
        """Stay anonymous with a bad bearer token, even with a good cookie."""
        site = build()
        client = TestClient(site.app)
        go(log_in(client))
        response = go(client.get("/jobs", headers={"Authorization": "Bearer nonsense"}))
        assert response.status == UNAUTHORIZED

    def test_other_schemes_are_ignored(self) -> None:
        """Ignore a non-Bearer Authorization header and use the cookie."""
        site = build()
        client = TestClient(site.app)
        go(log_in(client))
        response = go(client.get("/jobs", headers={"Authorization": "Basic Zm9vOmJhcg=="}))
        assert response.status == OK


class TestLogging:
    """Test that secrets never reach the logs."""

    def test_no_secret_in_any_log_record(self, caplog: pytest.LogCaptureFixture) -> None:
        """Log nothing containing a password, token, cookie value or CSRF token."""
        site = build()
        client = TestClient(site.app)
        bearer = TestBearer().fetch_token
        secrets: list[str] = [ADA_WORD, BOB_WORD, "wrong-guess-99"]

        async def flow() -> None:
            """Walk through every path that might be tempted to log."""
            for _ in range(MAX_FAILURES + 1):
                await client.post("/login", form={"name": "bob", "password": "wrong-guess-99"})
            csrf = await log_in(client)
            secrets.extend([csrf, client.cookies["session"]])
            await client.post("/jobs", headers={"X-CSRF-Token": csrf + "x"})
            await client.post("/jobs", form={"csrf_token": csrf})
            await client.get("/admin")
            await client.post("/logout", headers={"X-CSRF-Token": csrf})
            await client.get("/jobs", headers={"Authorization": "Bearer not-a-token-123"})

        with caplog.at_level(logging.DEBUG):
            go(flow())
            token = bearer(site)
            go(client.get("/jobs", headers={"Authorization": f"Bearer {token}"}))
        secrets.extend([token, "not-a-token-123"])
        assert {"pyweb.auth"} <= {record.name for record in caplog.records}
        for record in caplog.records:
            logged = f"{record.getMessage()} {record.exc_text or ''}"
            for secret in secrets:
                assert secret not in logged
