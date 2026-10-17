# Cookies and Sessions

## The Theme-Park Wristband

At a theme park you show your ticket once at the gate and get a
**wristband**. After that, every ride just looks at the band. The band
says who you are and when it was issued, and it carries a **tamper-proof
stamp** that only the park can make. If you scribble "VIP" on it, the
stamp no longer matches and the ride operator turns you away.

A **session cookie** is that wristband. After login, the server hands the
browser a token; the browser shows it on every request. `SessionSigner`
makes the stamp with a secret only the server knows.

## Try It

```python
from pyweb.security import SessionSigner, csrf_matches, new_csrf_token

now = [1_000_000.0]
signer = SessionSigner("demo-key-not-real-0123456789abcdef", max_age=60, clock=lambda: now[0])
token = signer.sign({"user": "ada"})
print(signer.unsign(token))
payload, stamp = token.split(".")
print(signer.unsign("X" + payload[1:] + "." + stamp))
now[0] += 61
print(signer.unsign(token))
csrf = new_csrf_token()
print(csrf_matches(csrf, csrf), csrf_matches(csrf, None))
```

Output:

```text
{'user': 'ada', 'iat': 1000000.0}
None
None
True False
```

The demo secret above is fake. A real secret is at least 32 random
characters, kept out of your code (for example in an environment
variable) and never printed or logged.

## The Rules

- **Tamper-proof.** Change one character of the token and `unsign`
  returns `None`; treat that visitor as a stranger.
- **Expiry.** Each token records when it was issued (`iat`). After
  `max_age` (8 hours by default) it stops working, so a stolen band
  does not work forever.
- **HttpOnly.** When you set the cookie, add `HttpOnly` so page
  JavaScript cannot read it (a hostile script cannot steal what it
  cannot see). Add `Secure` on HTTPS and `SameSite=Lax` to limit
  cross-site sending.
- **Stamped is not secret.** The token is signed, not hidden. Anyone can
  read it, so never put passwords inside.
- **CSRF.** A form carries a random `new_csrf_token()` too, and the server
  checks it with `csrf_matches`, so another website cannot post forms for
  you.

## What We Test

- A signed token comes back with its data and issue time.
- One changed character in either half is rejected.
- A different secret, bad base64, bad JSON and wrong shapes give `None`.
- Tokens expire with a fake clock; far-future tokens are refused.
- Short secrets are refused; CSRF compare handles `None` and empty.

## Next Up

Keep guessers out too: see [Passwords](passwords.md) for hashing and the
login throttle.
