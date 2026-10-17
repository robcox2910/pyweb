"""Tests that LoginThrottle and authenticate are safe on many threads at once.

``authenticate`` runs in ``blocking=True`` handlers, which PyNet sends to
its helper threads, so several logins can touch the throttle together.
"""

import sys
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from pyweb.auth import InMemoryUsers, User, authenticate
from pyweb.messages import HTTPError
from pyweb.security import LoginThrottle

THREADS = 4
ROUNDS_PER_THREAD = 2_000
GUESSERS = 16
MAX_FAILURES = 5
TOO_MANY = 429
TINY = 1e-6
HASH_ROUNDS = 1_000
SWITCH_INTERVAL = 1e-6
WORD = "demo-password-not-real"


def run_together(work: Callable[[int], None], count: int) -> None:
    """Run ``work`` on ``count`` threads that all start at the same moment.

    Any exception a thread raised is raised again here, failing the test.
    """
    barrier = threading.Barrier(count)

    def worker(index: int) -> None:
        """Wait for everyone, then work."""
        barrier.wait()
        work(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(worker, index) for index in range(count)]
    for future in futures:
        future.result()


@pytest.fixture(autouse=True)
def eager_switching() -> Iterator[None]:
    """Make threads take turns very often, so races show up quickly."""
    before = sys.getswitchinterval()
    sys.setswitchinterval(SWITCH_INTERVAL)
    yield
    sys.setswitchinterval(before)


class TestThrottleThreads:
    """Test the throttle's bookkeeping under parallel use."""

    def test_parallel_use_raises_nothing(self) -> None:
        """Record, check, clear and prune from four threads without errors."""
        throttle = LoginThrottle(max_failures=MAX_FAILURES, window=TINY, lockout=TINY)

        def hammer(index: int) -> None:
            """Mix every public method on a handful of shared names."""
            for step in range(ROUNDS_PER_THREAD):
                name = f"user{(index + step) % 7}"
                throttle.record_failure(name)
                throttle.locked(name)
                if step % 3 == 0:
                    throttle.record_success(name)
                throttle.tracked()

        run_together(hammer, THREADS)

    def test_begin_lets_only_max_failures_guesses_through(self) -> None:
        """Allow at most ``max_failures`` attempts at once for one name."""
        throttle = LoginThrottle(max_failures=MAX_FAILURES)
        allowed: list[bool] = []

        def guess(_index: int) -> None:
            """Ask to start an attempt."""
            allowed.append(throttle.begin("ada"))

        run_together(guess, GUESSERS)
        assert allowed.count(True) == MAX_FAILURES
        for _ in range(MAX_FAILURES):
            throttle.record_failure("ada")
        assert throttle.locked("ada")
        assert not throttle.begin("ada")

    def test_finishing_frees_the_slot(self) -> None:
        """Let a new attempt start once an earlier one has succeeded."""
        throttle = LoginThrottle(max_failures=1)
        assert throttle.begin("ada")
        assert not throttle.begin("ada")
        throttle.record_success("ada")
        assert throttle.begin("ada")


class TestAuthenticateThreads:
    """Test that a burst of parallel wrong guesses cannot beat the lockout."""

    def test_parallel_guesses_never_exceed_max_failures(self) -> None:
        """Check at most ``max_failures`` passwords; refuse the rest with 429."""
        users = InMemoryUsers()
        users.add("ada", WORD, rounds=HASH_ROUNDS)
        throttle = LoginThrottle(max_failures=MAX_FAILURES)
        checked: list[User | None] = []
        refused: list[int] = []

        def guess(index: int) -> None:
            """Try a wrong password, counting checks and 429s."""
            try:
                checked.append(authenticate(users, throttle, "ada", f"guess-{index}"))
            except HTTPError as exc:
                refused.append(exc.status)

        run_together(guess, GUESSERS)
        assert len(checked) == MAX_FAILURES
        assert checked == [None] * MAX_FAILURES
        assert refused == [TOO_MANY] * (GUESSERS - MAX_FAILURES)
        assert throttle.locked("ada")
        with pytest.raises(HTTPError):
            authenticate(users, throttle, "ada", WORD)

    def test_store_crash_counts_as_failure(self) -> None:
        """Count an attempt whose user lookup crashed, and free its slot."""

        class Broken:
            """A user store that always fails."""

            def get(self, name: str) -> User | None:
                """Fail to look anyone up."""
                raise RuntimeError(name)

        throttle = LoginThrottle(max_failures=1)
        with pytest.raises(RuntimeError):
            authenticate(Broken(), throttle, "ada", WORD)
        assert throttle.locked("ada")
