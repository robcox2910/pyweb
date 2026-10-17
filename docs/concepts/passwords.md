# Passwords

## The Fingerprint Card

A club wants to know who you are without keeping *you* in a drawer. So
it takes your **fingerprint** and files that. Next visit, it takes a new
fingerprint and compares. Nobody can rebuild your hand from a card.

A password works the same way. We never store the password; we store a
**hash**, a scrambled fingerprint of it. To log in, we scramble what you
typed and compare the two scrambles.

Two extra tricks make the cards hard to abuse:

- **Salt.** Before scrambling, we mix in some random sprinkles that are
  different for every person. Two people with the same password get
  different cards, so a thief cannot spot twins or use a ready-made list
  of famous scrambles.
- **Slow on purpose.** Scrambling is repeated 100 000 times. That costs
  you a fraction of a second at login, but a thief guessing millions of
  passwords pays that cost for every single guess.

## Try It

```python
from pyweb.security import hash_password, verify_password

stored = hash_password("open sesame", rounds=1_000)
print(stored.split("$")[0], stored.split("$")[1], len(stored.split("$")[2]), len(stored.split("$")[3]))
print(verify_password("open sesame", stored))
print(verify_password("open sesame!", stored))
print(stored == hash_password("open sesame", rounds=1_000))
print(verify_password("open sesame", "not a real hash"))
```

Output:

```text
pbkdf2_sha256 1000 32 64
True
False
False
False
```

(The demo uses `rounds=1_000` to be quick. Real code leaves the default
of 100 000.)

## The Rules

- A stored string looks like `pbkdf2_sha256$rounds$salt$key`, so the
  recipe travels with the fingerprint and can be raised later.
- `verify_password` never raises. A broken, empty or silly stored
  string just answers `False`.
- Keys are compared in constant time so the clock cannot leak hints.
- `LoginThrottle` locks a username for 30 seconds after 5 failures in
  5 minutes, so guessing is slow even before hashing.

## What We Test

- The stored string has the right shape and a fresh salt every time.
- The right password passes; a wrong one or an altered key fails.
- Malformed, empty, zero-round and absurd-round strings give `False`.
- The throttle locks, unlocks, forgets old failures and clears on success.

## Next Up

Logged-in visitors need to be remembered. See
[Cookies and Sessions](cookies-and-sessions.md).

Once visitors can log in, decide where they may go: see [Roles](roles.md).
