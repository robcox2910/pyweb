"""Security primitives: password hashing, signed sessions, CSRF and throttling.

Everything here is small on purpose so you can read all of it. The heavy
lifting (PBKDF2 and HMAC) comes from PyCrypt.
"""

import base64
import binascii
import hmac
import json
import secrets
import time
from collections.abc import Callable, Mapping

from pycrypt.hmac import hmac_sign, hmac_verify
from pycrypt.kdf import derive_key

from pyweb.messages import JsonValue

__all__ = [
    "LoginThrottle",
    "SessionSigner",
    "csrf_matches",
    "hash_password",
    "new_csrf_token",
    "verify_password",
]

_SCHEME = "pbkdf2_sha256"
_HASH_PARTS = 4
_SALT_BYTES = 16
_KEY_BYTES = 32
_MAX_ROUNDS = 10_000_000
_MIN_SECRET_LENGTH = 32
_MAX_TOKEN_LENGTH = 4096
_FUTURE_SKEW = 60.0
_CSRF_BYTES = 32


def _encodable(text: str) -> bool:
    """Return True if the text can be encoded as UTF-8 (no lone surrogates)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def hash_password(password: str, *, rounds: int = 100_000) -> str:
    """Turn a password into a salted, slow fingerprint string.

    Args:
        password: The plain-text password.
        rounds: How many times to repeat the slow hashing step.

    Returns:
        ``"pbkdf2_sha256$<rounds>$<salt hex>$<key hex>"``.

    Raises:
        ValueError: If ``rounds`` is less than 1.

    """
    if rounds < 1:
        msg = "rounds must be at least 1"
        raise ValueError(msg)
    if not _encodable(password):
        msg = "password must be encodable as UTF-8"
        raise ValueError(msg)
    key, salt = derive_key(password, salt=secrets.token_bytes(_SALT_BYTES), rounds=rounds)
    return f"{_SCHEME}${rounds}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored fingerprint without ever raising.

    Args:
        password: The plain-text password to test.
        stored: A string made by :func:`hash_password`.

    Returns:
        True only if the stored string is well formed and matches.

    """
    if not _encodable(password):
        return False
    parts = stored.split("$")
    if len(parts) != _HASH_PARTS or parts[0] != _SCHEME:
        return False
    _, rounds_text, salt_hex, key_hex = parts
    try:
        rounds = int(rounds_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
    except ValueError:
        return False
    if not salt or len(expected) != _KEY_BYTES or not 1 <= rounds <= _MAX_ROUNDS:
        return False
    actual, _ = derive_key(password, salt=salt, key_length=len(expected), rounds=rounds)
    return hmac.compare_digest(actual, expected)


def _b64_encode(raw: bytes) -> str:
    """Encode bytes as base64url text without padding."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64_decode(text: str) -> bytes:
    """Decode base64url text, tolerating missing padding."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SessionSigner:
    """Sign small dictionaries into tamper-proof, expiring tokens."""

    def __init__(
        self,
        secret: str,
        *,
        max_age: float = 8 * 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Create a signer.

        Args:
            secret: The signing secret, at least 32 characters.
            max_age: Seconds a token stays valid.
            clock: Wall-clock seconds source (tests inject a fake).

        Raises:
            ValueError: If the secret is shorter than 32 characters.

        """
        if len(secret) < _MIN_SECRET_LENGTH:
            msg = f"secret must be at least {_MIN_SECRET_LENGTH} characters"
            raise ValueError(msg)
        self._secret = secret
        self._max_age = max_age
        self._clock = clock

    def sign(self, data: Mapping[str, JsonValue]) -> str:
        """Return ``payload.signature`` for the data, stamped with the time."""
        body = json.dumps({**data, "iat": self._clock()}, separators=(",", ":"))
        payload = _b64_encode(body.encode("utf-8"))
        return f"{payload}.{hmac_sign(payload, self._secret)}"

    def _verified_payload(self, token: str) -> dict[str, JsonValue] | None:
        """Return the decoded payload if the shape and signature are good."""
        if len(token) > _MAX_TOKEN_LENGTH or not token.isascii():
            return None
        payload, dot, signature = token.partition(".")
        if not dot or not payload or not signature or "." in signature:
            return None
        if not hmac_verify(payload, signature, self._secret):
            return None
        try:
            data: JsonValue = json.loads(_b64_decode(payload))
        except binascii.Error, ValueError:
            return None
        return data if isinstance(data, dict) else None

    def unsign(self, token: str) -> dict[str, JsonValue] | None:
        """Return the signed data, or None if the token is not trustworthy."""
        data = self._verified_payload(token)
        if data is None:
            return None
        issued = data.get("iat")
        if isinstance(issued, bool) or not isinstance(issued, int | float):
            return None
        age = self._clock() - issued
        if age > self._max_age or age < -_FUTURE_SKEW:
            return None
        return data


def new_csrf_token() -> str:
    """Return a fresh unguessable CSRF token."""
    return secrets.token_urlsafe(_CSRF_BYTES)


def csrf_matches(expected: str, given: str | None) -> bool:
    """Compare CSRF tokens in constant time; empty or missing never match."""
    if not expected or not given:
        return False
    if not (expected.isascii() and given.isascii()):
        return False
    return hmac.compare_digest(expected, given)


class LoginThrottle:
    """Lock a username out for a while after too many recent failures."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window: float = 300.0,
        lockout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a throttle.

        Args:
            max_failures: Failures inside ``window`` that trigger a lockout.
            window: Seconds a failure is remembered.
            lockout: Seconds a locked name stays locked.
            clock: Monotonic seconds source (tests inject a fake).

        """
        self._max_failures = max_failures
        self._window = window
        self._lockout = lockout
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        """Forget expired failures and finished lockouts."""
        for name in list(self._failures):
            recent = [t for t in self._failures[name] if now - t < self._window]
            if recent:
                self._failures[name] = recent
            else:
                del self._failures[name]
        for name in list(self._locked_until):
            if self._locked_until[name] <= now:
                del self._locked_until[name]

    def locked(self, username: str) -> bool:
        """Return True while the name is locked out."""
        now = self._clock()
        self._prune(now)
        return username in self._locked_until

    def record_failure(self, username: str) -> None:
        """Note a failed login and start a lockout if there were too many."""
        now = self._clock()
        self._prune(now)
        failures = self._failures.setdefault(username, [])
        failures.append(now)
        if len(failures) >= self._max_failures:
            self._locked_until[username] = now + self._lockout
            del self._failures[username]

    def record_success(self, username: str) -> None:
        """Forget all failures for the name after a good login."""
        self._prune(self._clock())
        self._failures.pop(username, None)

    def tracked(self) -> int:
        """Return how many names are currently remembered."""
        self._prune(self._clock())
        return len(self._failures.keys() | self._locked_until.keys())
