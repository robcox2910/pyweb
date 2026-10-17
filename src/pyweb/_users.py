"""Users for PyWeb's auth: the user record, stores, and checking a password.

Private to PyWeb: import these from ``pyweb.auth`` instead.
"""

import functools
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from pyweb.messages import HTTPError, HttpRequest
from pyweb.security import LoginThrottle, hash_password, verify_password

logger = logging.getLogger("pyweb.auth")

_TOO_MANY_REQUESTS = 429


@dataclass(frozen=True)
class User:
    """A person who can log in.

    Attributes:
        name: The login name.
        password_hash: The fingerprint from ``hash_password`` (left out of
            the repr so a logged ``User`` never shows it).
        roles: The backstage passes this user holds.

    """

    name: str
    password_hash: str = field(repr=False)
    roles: frozenset[str] = frozenset()


class UserStore(Protocol):
    """Anything that can look users up by name."""

    def get(self, name: str) -> User | None:
        """Return the user with this name, or None."""
        ...


class InMemoryUsers(UserStore):
    """A user store kept in a dictionary (handy for demos and tests)."""

    def __init__(self) -> None:
        """Create an empty store."""
        self._users: dict[str, User] = {}

    def add(
        self, name: str, password: str, roles: Iterable[str] = (), *, rounds: int = 100_000
    ) -> User:
        """Hash the password, store a new user and return it."""
        user = User(name, hash_password(password, rounds=rounds), frozenset(roles))
        self._users[name] = user
        return user

    def get(self, name: str) -> User | None:
        """Return the user with this name, or None."""
        return self._users.get(name)


def current_user(request: HttpRequest) -> User | None:
    """Return the logged-in ``User`` for a request, or None for a stranger."""
    return request.user if isinstance(request.user, User) else None


@functools.cache
def _dummy_hash() -> str:
    """Return a throwaway hash, so unknown names cost as much as known ones."""
    return hash_password("not-a-real-password")


def authenticate(
    users: UserStore, throttle: LoginThrottle, name: str, password: str
) -> User | None:
    """Check a name and password, counting failures with the throttle.

    This does slow hashing on purpose, so call it from a handler
    registered with ``blocking=True``. The attempt is counted with
    ``throttle.begin`` *before* the password is checked, so parallel
    guesses for one name never check more than the throttle's
    ``max_failures`` passwords between lockouts.

    Raises:
        HTTPError: ``429`` while the name is locked out, or while enough
            attempts for it are already in progress to reach the limit.

    """
    if not throttle.begin(name):
        logger.info("Login refused: too many recent failures")
        raise HTTPError(_TOO_MANY_REQUESTS, "too many failed logins; try again later")
    found: User | None = None
    try:
        user = users.get(name)
        matched = verify_password(password, user.password_hash if user else _dummy_hash())
        if user is not None and matched:
            found = user
    finally:
        # Always finish the attempt; a crash counts as a failure (fail closed).
        if found is not None:
            throttle.record_success(name)
        else:
            throttle.record_failure(name)
            logger.debug("Login failed")
    return found
