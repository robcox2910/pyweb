"""Security primitives: password hashing, signed sessions, CSRF and throttling.

Everything here is small on purpose so you can read all of it. The heavy
lifting (PBKDF2 and HMAC) comes from PyCrypt.
"""

import base64
import binascii
import hmac
import json
import secrets
import threading
import time
from collections import deque
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
        """Sign the data, stamped with the time it was issued.

        Args:
            data: Small JSON-friendly facts to protect, such as the user's
                name. They are readable by anyone holding the token (signed,
                not secret), so never put a password in them.

        Returns:
            A ``payload.signature`` token: base64url JSON (with an ``iat``
            timestamp added) plus its HMAC signature.

        """
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
        """Check a token and give back the data it carries.

        Never raises: anything odd simply answers ``None``.

        Args:
            token: A string made by :meth:`sign`, for example from a cookie.

        Returns:
            The signed data (including ``iat``), or ``None`` if the token is
            too long, malformed, signed with another secret, tampered with,
            older than ``max_age`` or stamped too far in the future.

        """
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
    """Lock a username out for a while after too many recent failures.

    Think of a bike lock with a counter: after five wrong codes it jams
    for half a minute. Safe to share between threads (``blocking=True``
    handlers run on PyNet's helper threads): every method holds a lock.

    ``begin`` counts an attempt *before* the slow password check, so a
    burst of parallel guesses for one name can never check more than
    ``max_failures`` passwords between lockouts: an attempt may only start
    while recorded failures plus attempts still in progress are below the
    limit. ``authenticate`` uses it for you.
    """

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
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._in_progress: dict[str, int] = {}
        # Oldest first, so pruning only looks at entries that have expired.
        self._failure_times: deque[tuple[float, str]] = deque()
        self._lockout_ends: deque[tuple[float, str]] = deque()

    def _prune(self, now: float) -> None:
        """Forget expired failures and finished lockouts (call with the lock held)."""
        while self._failure_times and now - self._failure_times[0][0] >= self._window:
            _, name = self._failure_times.popleft()
            times = self._failures.get(name)
            while times and now - times[0] >= self._window:
                times.popleft()
            if times is not None and not times:
                del self._failures[name]
        while self._lockout_ends and self._lockout_ends[0][0] <= now:
            _, name = self._lockout_ends.popleft()
            until = self._locked_until.get(name)
            if until is not None and until <= now:
                del self._locked_until[name]

    def _finish(self, username: str) -> None:
        """End one attempt started with ``begin``, if any (lock held)."""
        count = self._in_progress.get(username, 0)
        if count > 1:
            self._in_progress[username] = count - 1
        elif count == 1:
            del self._in_progress[username]

    def begin(self, username: str) -> bool:
        """Start a login attempt; return False if it must be refused.

        Refused while the name is locked out, or while its recent failures
        plus attempts still in progress already reach ``max_failures``.
        Finish every started attempt with ``record_success`` or
        ``record_failure``.
        """
        with self._lock:
            self._prune(self._clock())
            if username in self._locked_until:
                return False
            used = len(self._failures.get(username, ())) + self._in_progress.get(username, 0)
            if used >= self._max_failures:
                return False
            self._in_progress[username] = self._in_progress.get(username, 0) + 1
            return True

    def locked(self, username: str) -> bool:
        """Return True while the name is locked out."""
        with self._lock:
            self._prune(self._clock())
            return username in self._locked_until

    def record_failure(self, username: str) -> None:
        """Note a failed login and start a lockout if there were too many."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._finish(username)
            failures = self._failures.setdefault(username, deque())
            failures.append(now)
            self._failure_times.append((now, username))
            if len(failures) >= self._max_failures:
                self._locked_until[username] = now + self._lockout
                self._lockout_ends.append((now + self._lockout, username))
                del self._failures[username]

    def record_success(self, username: str) -> None:
        """Forget all failures for the name after a good login."""
        with self._lock:
            self._prune(self._clock())
            self._finish(username)
            self._failures.pop(username, None)

    def tracked(self) -> int:
        """Return how many names are currently remembered."""
        with self._lock:
            self._prune(self._clock())
            return len(self._failures.keys() | self._locked_until.keys())
