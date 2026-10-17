"""Tests for password hashing, signed sessions, CSRF tokens and throttling."""

import base64
import json

import pytest
from pycrypt.hmac import hmac_sign

from pyweb.security import (
    LoginThrottle,
    SessionSigner,
    csrf_matches,
    hash_password,
    new_csrf_token,
    verify_password,
)

ROUNDS = 1_000
DEMO_KEY = "demo-key-not-real-0123456789abcdef"
OTHER_KEY = "another-demo-key-0123456789abcdef"
GOOD_WORD = "correct horse"
BAD_WORD = "wrong horse"
START = 1_000_000.0
MAX_AGE = 100.0
HASH_PARTS = 4
SALT_HEX_LEN = 32
KEY_HEX_LEN = 64
MAX_FAILURES = 5
LOCKOUT = 30.0
WINDOW = 300.0
TOKEN_BYTES_MIN = 32
HUGE_ROUNDS = 10_000_001
FUTURE_SKEW = 120.0


class Clock:
    """Provide a hand-turned clock for tests."""

    def __init__(self, now: float = START) -> None:
        """Start the clock at a given time."""
        self.now = now

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now


def _b64(raw: bytes) -> str:
    """Encode bytes as unpadded base64url text."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class TestPasswords:
    """Test hashing and checking passwords."""

    def test_format(self) -> None:
        """Produce the documented four-part string."""
        parts = hash_password(GOOD_WORD, rounds=ROUNDS).split("$")
        assert len(parts) == HASH_PARTS
        assert parts[0] == "pbkdf2_sha256"
        assert parts[1] == str(ROUNDS)
        assert len(parts[2]) == SALT_HEX_LEN
        assert len(parts[3]) == KEY_HEX_LEN

    def test_round_trip(self) -> None:
        """Accept the right password and reject a wrong one."""
        stored = hash_password(GOOD_WORD, rounds=ROUNDS)
        assert verify_password(GOOD_WORD, stored)
        assert not verify_password(BAD_WORD, stored)

    def test_salt_makes_hashes_differ(self) -> None:
        """Give two hashes of one password different strings."""
        first = hash_password(GOOD_WORD, rounds=ROUNDS)
        second = hash_password(GOOD_WORD, rounds=ROUNDS)
        assert first != second

    @pytest.mark.parametrize(
        "stored",
        [
            "",
            "md5$x",
            "pbkdf2_sha256$x$y$z",
            "pbkdf2_sha256$1000$zz$zz",
            "pbkdf2_sha256$0$aa$bb",
            "pbkdf2_sha256$-5$aa$bb",
            f"pbkdf2_sha256${HUGE_ROUNDS}$aa$bb",
            "pbkdf2_sha256$1000$aa$bb$cc",
            "pbkdf2_sha256$1000$aa$",
            "pbkdf2_sha256$1000$$bb",
            "pbkdf2_sha256$1000$éé$bb",
        ],
    )
    def test_malformed_is_false(self, stored: str) -> None:
        """Return False, never raise, for malformed stored strings."""
        assert verify_password(GOOD_WORD, stored) is False

    def test_bad_rounds_rejected_on_hash(self) -> None:
        """Refuse to hash with fewer than one round."""
        with pytest.raises(ValueError, match="rounds"):
            hash_password(GOOD_WORD, rounds=0)

    def test_tampered_key_is_false(self) -> None:
        """Reject a stored string whose key was altered."""
        head, _, key = hash_password(GOOD_WORD, rounds=ROUNDS).rpartition("$")
        flipped = "0" if key[0] != "0" else "1"
        assert not verify_password(GOOD_WORD, f"{head}${flipped}{key[1:]}")


class TestSessionSigner:
    """Test signing and checking session tokens."""

    def test_round_trip(self) -> None:
        """Return the signed data plus an issue time."""
        clock = Clock()
        signer = SessionSigner(DEMO_KEY, max_age=MAX_AGE, clock=clock)
        data = signer.unsign(signer.sign({"user": "ada", "n": 1}))
        assert data == {"user": "ada", "n": 1, "iat": START}

    def test_short_secret_raises(self) -> None:
        """Refuse a secret shorter than 32 characters."""
        with pytest.raises(ValueError, match="32"):
            SessionSigner("short")

    @pytest.mark.parametrize("part", [0, 1])
    def test_one_changed_character_is_none(self, part: int) -> None:
        """Reject a token with one character changed in either part."""
        signer = SessionSigner(DEMO_KEY, clock=Clock())
        pieces = signer.sign({"user": "ada"}).split(".")
        text = pieces[part]
        flipped = "A" if text[0] != "A" else "B"
        pieces[part] = flipped + text[1:]
        assert signer.unsign(".".join(pieces)) is None

    def test_other_secret_is_none(self) -> None:
        """Reject a token signed with a different secret."""
        token = SessionSigner(DEMO_KEY, clock=Clock()).sign({"user": "ada"})
        assert SessionSigner(OTHER_KEY, clock=Clock()).unsign(token) is None

    def test_expiry(self) -> None:
        """Accept a token until max_age has passed, then reject it."""
        clock = Clock()
        signer = SessionSigner(DEMO_KEY, max_age=MAX_AGE, clock=clock)
        token = signer.sign({"user": "ada"})
        clock.now = START + MAX_AGE
        assert signer.unsign(token) is not None
        clock.now = START + MAX_AGE + 1
        assert signer.unsign(token) is None

    def test_future_iat_is_none(self) -> None:
        """Reject a token issued far in the future."""
        clock = Clock()
        signer = SessionSigner(DEMO_KEY, clock=clock)
        token = signer.sign({"user": "ada"})
        clock.now = START - FUTURE_SKEW
        assert signer.unsign(token) is None

    def test_small_future_skew_ok(self) -> None:
        """Tolerate a few seconds of clock difference."""
        clock = Clock()
        signer = SessionSigner(DEMO_KEY, clock=clock)
        token = signer.sign({"user": "ada"})
        clock.now = START - 5
        assert signer.unsign(token) is not None

    @pytest.mark.parametrize(
        "token",
        ["", "nodot", "a.b.c", ".", "abc.", ".abc", "é.é", "x" * 5000],
    )
    def test_bad_shape_is_none(self, token: str) -> None:
        """Return None for tokens of the wrong shape."""
        assert SessionSigner(DEMO_KEY, clock=Clock()).unsign(token) is None

    @pytest.mark.parametrize(
        "raw",
        [b"not json", b"[1, 2]", b'"text"', b"{}", b'{"iat": "soon"}', b'{"iat": true}'],
    )
    def test_validly_signed_garbage_is_none(self, raw: bytes) -> None:
        """Return None when a correctly signed payload is not a session."""
        payload = _b64(raw)
        token = f"{payload}.{hmac_sign(payload, DEMO_KEY)}"
        assert SessionSigner(DEMO_KEY, clock=Clock()).unsign(token) is None

    def test_bad_base64_is_none(self) -> None:
        """Return None when the signed text is not base64."""
        payload = "!!!"
        token = f"{payload}.{hmac_sign(payload, DEMO_KEY)}"
        assert SessionSigner(DEMO_KEY, clock=Clock()).unsign(token) is None

    def test_payload_is_unpadded_base64url(self) -> None:
        """Emit base64url text with no padding characters."""
        token = SessionSigner(DEMO_KEY, clock=Clock()).sign({"user": "ada?>"})
        payload = token.split(".")[0]
        assert "=" not in payload
        padded = payload + "=" * (-len(payload) % 4)
        assert json.loads(base64.urlsafe_b64decode(padded))["user"] == "ada?>"


class TestCsrf:
    """Test CSRF token helpers."""

    def test_new_tokens_differ_and_are_long(self) -> None:
        """Make unpredictable tokens."""
        first, second = new_csrf_token(), new_csrf_token()
        assert first != second
        assert len(first) >= TOKEN_BYTES_MIN

    def test_matches(self) -> None:
        """Match equal tokens only."""
        token = new_csrf_token()
        assert csrf_matches(token, token)
        assert not csrf_matches(token, token + "x")

    @pytest.mark.parametrize("given", [None, ""])
    def test_missing_is_false(self, given: str | None) -> None:
        """Reject a missing or empty submitted token."""
        assert not csrf_matches("abc", given)

    def test_empty_expected_is_false(self) -> None:
        """Reject when there is no expected token, even if both are empty."""
        assert not csrf_matches("", "")

    def test_non_ascii_is_false(self) -> None:
        """Return False, not raise, for non-ASCII input."""
        assert not csrf_matches("abc", "ébc")


class TestLoginThrottle:
    """Test locking out repeated failures."""

    def _make(self, clock: Clock) -> LoginThrottle:
        """Build a throttle with default limits."""
        return LoginThrottle(clock=clock)

    def test_locks_after_max_failures(self) -> None:
        """Lock a name on the fifth failure."""
        clock = Clock(0.0)
        throttle = self._make(clock)
        for _ in range(MAX_FAILURES - 1):
            throttle.record_failure("ada")
        assert not throttle.locked("ada")
        throttle.record_failure("ada")
        assert throttle.locked("ada")
        assert not throttle.locked("bob")

    def test_unlocks_after_lockout(self) -> None:
        """Unlock once the lockout time has passed."""
        clock = Clock(0.0)
        throttle = self._make(clock)
        for _ in range(MAX_FAILURES):
            throttle.record_failure("ada")
        clock.now = LOCKOUT - 1
        assert throttle.locked("ada")
        clock.now = LOCKOUT + 1
        assert not throttle.locked("ada")

    def test_old_failures_do_not_count(self) -> None:
        """Ignore failures older than the window."""
        clock = Clock(0.0)
        throttle = self._make(clock)
        for _ in range(MAX_FAILURES - 1):
            throttle.record_failure("ada")
        clock.now = WINDOW + 1
        throttle.record_failure("ada")
        assert not throttle.locked("ada")

    def test_success_clears(self) -> None:
        """Forget earlier failures after a success."""
        clock = Clock(0.0)
        throttle = self._make(clock)
        for _ in range(MAX_FAILURES - 1):
            throttle.record_failure("ada")
        throttle.record_success("ada")
        throttle.record_failure("ada")
        assert not throttle.locked("ada")

    def test_names_are_case_sensitive(self) -> None:
        """Treat Ada and ada as different names."""
        throttle = self._make(Clock(0.0))
        for _ in range(MAX_FAILURES):
            throttle.record_failure("Ada")
        assert throttle.locked("Ada")
        assert not throttle.locked("ada")

    def test_memory_is_bounded(self) -> None:
        """Prune expired entries so the table does not grow forever."""
        clock = Clock(0.0)
        throttle = self._make(clock)
        for index in range(50):
            throttle.record_failure(f"user{index}")
        clock.now = WINDOW + LOCKOUT + 1
        throttle.record_failure("fresh")
        assert throttle.tracked() == 1
