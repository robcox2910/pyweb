# Property Testing (Every Letter Gets Sorted)

## The Post Office Inspector

PyWeb is a post office. Letters (requests) arrive as a bag of bytes; the
sorting room reads the address label and the stickers on the envelope;
the mailroom sends each letter to the right desk; and a reply goes back
out.

How would you check the sorting room works? The usual way: post one
letter addressed to `GET /about`, and check it was read as method `GET`
and path `/about`. That's an **example test**. It's useful, but it only
tells you about the one letter you thought of.

Now imagine a post office inspector. All day long they post thousands of
letters: tidy ones, scruffy ones, ones with stickers in Greek, and ones
that are just torn-up confetti. For each one they check a single rule:

> Whatever letter you post, the sorting room reads exactly what's
> written on it -- and if it can't read it, it says so in the one way the
> post office knows how to handle.

A rule like that is a **property**. It doesn't say what any one letter
looks like. It says something that must be true for *every* letter.

When the inspector finds a letter that breaks the rule, they don't just
report "something in this morning's sacks went wrong". They try again
with less and less -- fewer stickers, shorter addresses -- checking it
still goes wrong each time, until it's as simple as it can be. That
boiling-down is called **shrinking**, and it's the best part: the
smallest failing letter usually points straight at the bug.

In Python, the inspector is a library called
[Hypothesis](https://hypothesis.readthedocs.io/). PyWeb's property tests
live in `tests/test_properties.py`, next to the ordinary example tests,
and the model checks (more on those below) are in `tests/test_model.py`.

## A Property from PyWeb

The sorting room is `pyweb/http1.py`. Its `parse_head` turns the bytes
of a request head into a `RequestHead`: method, target, version and a
list of headers. So the inspector writes a letter out by hand, posts it,
and checks the sorting room reads back exactly what they wrote:

```python
def methods() -> st.SearchStrategy[str]:
    """Pick one of the methods PyWeb understands: GET, POST, HEAD and friends."""
    return st.sampled_from(sorted(KNOWN_METHODS))


def versions() -> st.SearchStrategy[str]:
    """Pick one of the HTTP versions PyWeb speaks: ``HTTP/1.0`` or ``HTTP/1.1``."""
    return st.sampled_from(["HTTP/1.0", "HTTP/1.1"])


def targets() -> st.SearchStrategy[str]:
    """Make any request target: one word of any bytes, with no space, CR, LF or NUL."""
    letters = st.characters(min_codepoint=1, max_codepoint=0xFF, exclude_characters=" \r\n")
    return st.text(alphabet=letters, min_size=1)


def trim_spaces(text: str) -> str:
    """Remove spaces and tabs from both ends, as HTTP does around a header value."""
    return text.strip(" \t")


def header_values() -> st.SearchStrategy[str]:
    """Make any header value a request may carry: any bytes but CR, LF and NUL."""
    letters = st.characters(min_codepoint=1, max_codepoint=0xFF, exclude_characters="\r\n")
    ends = st.sampled_from(["", *BLANK_LOOKALIKES])
    return st.tuples(ends, st.text(alphabet=letters), ends).map("".join).map(trim_spaces)


def request_heads() -> st.SearchStrategy[RequestHead]:
    """Make any request head PyWeb accepts: method, target, version and a few headers."""
    headers = st.lists(st.tuples(header_names(), header_values()), max_size=MOST_HEADERS)
    return st.builds(RequestHead, methods(), targets(), versions(), headers)


def write_head(head: RequestHead) -> bytes:
    """Write a request head out the way a browser would, without the final blank line."""
    lines = [f"{head.method} {head.target} {head.version}"]
    lines += [f"{name}: {value}" for name, value in head.headers]
    return "\r\n".join(lines).encode("latin-1")


@given(head=request_heads())
def test_request_head_written_out_reads_back_the_same(head: RequestHead) -> None:
    """Whatever request head you write out, reading it back gives the very same head."""
    parsed = parse_head(write_head(head), LIMITS)
    assert parsed == head
    # Headers stay a list of pairs: repeats are kept, in the order they came,
    # and each name keeps its own capital letters. ``repr`` checks that too.
    assert repr(parsed) == repr(head)
```

Line by line:

- **`methods()`, `versions()`, `targets()` and `header_values()`** are
  **strategies**: recipes for making random inputs, each named for what
  it makes. `targets()` makes any address at all -- `/`, `/a?b=c`, odd
  symbols like `º*` -- as long as it's one word. `header_names()` (in
  `tests/strategies.py`, shared with the model check) makes any legal
  header name, and mixes in ones PyWeb reads itself, like
  `Content-Length`.
- **`trim_spaces`** is there because HTTP's rules say spaces and tabs
  around a value are padding: `X-A:  b  ` *means* `b`.
  So the strategy only makes values that can come back unchanged.
- **`ends`** is the inspector being sneaky. Form feed or `\xa0` (a
  no-break space) *look* blank, but they aren't padding, so the sorting
  room must keep them, even at the very end of a value.
- **`request_heads()`** builds a whole `RequestHead` with up to 8
  headers. Two can share a name, or differ only in capitals.
- **`write_head`** writes the letter out by hand, one line per piece.
- **`@given(head=request_heads())`** runs the test many times (100 in a
  normal run), each with a fresh `head`. **The docstring** says the rule
  in one plain sentence.
- **The first `assert`** is the rule itself: write it out, read it back,
  and you must get exactly what you started with. This kind of property
  is called a **round-trip**.
- **The second `assert`** compares `repr()`s too (the text you'd type to
  make each value), an extra check that types, order and capitals match.

Notice what the test *doesn't* say. It never says "the bytes
`GET / HTTP/1.1` give method `GET`". It doesn't need to know any
answers. It only needs a rule that's always true.

PyWeb also has a property that any bytes at all give a head or an
`HttpParseError`. The server answers bad letters by catching that error
and sending back its status, so any other error would crash the
connection. There's a catch, though. **A sorting room that refuses
every letter passes the "any bytes" property**, because refusing is
always allowed. The round-trip sits beside it: it fails straight away
if a good letter is ever refused.

Here are all the other properties:

| Rule | Test |
|---|---|
| Whatever bytes arrive, the server reads a head or refuses it with a status the docs list (`400`, `411`, `431`, `501`, `505`), nothing else | `test_any_bytes_give_a_head_or_http_parse_error` |
| PyWeb reads `Content-Length` exactly as the plain rules of HTTP say, or refuses it | `test_content_length_follows_the_plain_rules` |
| Python's own `http.client` reads back every status, reason and header PyWeb writes in a reply | `test_response_head_reads_back_with_the_standard_library` |
| A path and query escaped by Python's `urllib` split back into exactly what went in | `test_quoted_target_splits_back_into_path_and_query` |

The "any bytes" strategy posts pure random bytes, a good head with a few
bytes snipped or spliced in, or a good head with a known troublemaker
line slipped in (a folded line, a 19-digit `Content-Length`,
`Transfer-Encoding`).

## Checking Against a Model

A property checks one action. But the mailroom is something you set up
step by step: add a route, add another, look a path up. Bugs often hide
in the *order* of steps, or in two routes that both fit the same path.

So the inspector carries a notebook with every route written in it, in
the order it was added, and looks paths up the slow, obvious way. After
every step, the mailroom must give the same answer as the notebook.

That notebook is a **model**: something simple that we already trust.
Here's the heart of `RouteMachine` in `tests/test_model.py`:

```python
class RouteMachine(RuleBasedStateMachine):
    """Add random routes to a RouteTable and a plain list, and look up paths in both."""

    routes = Bundle("routes")

    def __init__(self) -> None:
        """Start with an empty route table and an empty list of rules."""
        super().__init__()
        self.table: RouteTable[int] = RouteTable()
        self.model: list[tuple[str, str, int]] = []

    def check(self, method: str, path: str) -> None:
        """Look a path up in both and check they give the same answer."""
        found = self.table.match(method, path)
        value, params, allowed = self.expected(method, path)
        # repr tells 7 from "7", so it checks <int:...> really gave an int.
        assert (found.value, repr(found.params), found.allowed) == (value, repr(params), allowed)

    @rule(target=routes, method=route_methods(), pattern=patterns())
    def add_route(self, method: str, pattern: str) -> tuple[str, str]:
        """Add a route to both, or check the table refuses a repeat the list already has."""
        if any((m, p) == (method, pattern) for m, p, _ in self.model):
            with pytest.raises(ValueError, match="duplicate"):
                self.table.add(method, pattern, len(self.model))
        else:
            assert self.table.add(method, pattern, len(self.model)) is None
            self.model.append((method, pattern, len(self.model)))
        return method, pattern

    @invariant()
    def every_route_still_answers_the_same(self) -> None:
        """After every step, each route's own path (blanks filled with 7) answers the same."""
        for method, pattern, _ in self.model:
            # Its own method finds it; DELETE (no route uses it) gets a 405.
            for asked in (method, "DELETE"):
                self.check(asked, PARAMETER.sub("7", pattern))


TestRouteModel = cast("type[unittest.TestCase]", RouteMachine.TestCase)  # pyright: ignore[reportUnknownMemberType]
```

Piece by piece:

- **`RuleBasedStateMachine`** is Hypothesis's tool for testing things
  that change step by step. Hypothesis makes up a random **story** of
  steps and plays it.
- **`self.table` and `self.model`** are the mailroom and the notebook,
  a plain list of `(method, pattern, value)`. Both start empty.
- **`check`** looks a path up in both and compares the whole answer:
  which route, which parameters, and -- when nothing fits -- the methods
  that would have (none means `404`, some means `405`). The notebook's
  side, `expected`, turns each pattern into a regular expression, keeps
  the ones that fit, and uses `sorted()` to apply the rule written in
  [Routing](concepts/routing.md#which-route-wins): literal pieces beat
  blanks, earlier routes win ties.
- **Each `@rule`** is one kind of step. `add_route` adds a route to both,
  and checks what `add` *says*: `None` for a new route, a `ValueError`
  for a repeat. The real file has rules to add a route again, look up
  any random path, and look up a path built to fit an earlier route.
- **`routes = Bundle("routes")`** is a bag of routes added earlier.
  `add_route` puts its route in the bag (`target=routes`); later rules
  pull one out. Without it, random paths would hardly ever fit a route
  with blanks in it.
- **The `@invariant()`** runs after every step. It fills each route's
  blanks with `7` and looks that path up, so a route that gets lost or
  shadowed is noticed straight away.
- **`TestRouteModel = ...TestCase`** turns the machine into a test that
  pytest can find and run. One run plays 100 random stories, each up to
  20 steps long (a `@settings` line on the class; Hypothesis's default
  is 50, which finds the same bugs but takes most of the run time).

Paths and patterns come from a tiny alphabet (`a`, `b`, `7`, `-2`,
nineteen `9`s and the empty piece), so they bump into each other all
the time.

The list is a good model because it's obviously right and it's *not*
`RouteTable`'s code. Both follow the same documented rule about which
route wins -- that's the rule they must agree on -- but the notebook
writes it down its own way: regular expressions and `sorted()`, where
`RouteTable` walks a path piece by piece. If we built the model by
copying `RouteTable`, any bug would be in both, and they'd agree
perfectly about the wrong answer.

A second machine, `HeadersMachine`, adds, sets and removes headers in
random capitals (`Host`, `hOsT`) on `Headers` and on a plain list of
pairs. Every read (`get`, `get_all`, `in`, `items()`) must agree: names
match whatever their capitals, and repeats keep their order.

## Try It Yourself

Let's break the mailroom on purpose and watch the inspector find it.

Open `src/pyweb/routing.py`, find `_convert`, and change one line, as if
someone thought only numbers needed checking for an empty piece:

```python
    if not part and seg.kind == _INT:  # was: if not part:
```

Now a blank like `<name>` happily fills itself with *nothing*. Run the
property tests:

```bash
uv run pytest tests/test_properties.py tests/test_model.py --no-cov
```

Hypothesis reports this (trimmed to the interesting part):

```
    def check(self, method: str, path: str) -> None:
        found = self.table.match(method, path)
        value, params, allowed = self.expected(method, path)
>       assert (found.value, repr(found.params), found.allowed) == (value, repr(params), allowed)
E       assert (0, "{'p0': ''}", frozenset()) == (None, '{}', set())
E         At index 0 diff: 0 != None
E       Failing test case: <state machine>
E       state = RouteMachine()
E       state.every_route_still_answers_the_same()
E       routes_0 = state.add_route(method='GET', pattern='/<p0>')
E       state.every_route_still_answers_the_same()
E       state.look_up_any_path(method='GET', path='/')
E       state.teardown()
```

Read the story at the bottom like a script. Start with an empty
mailroom. Add the route `GET /<p0>` -- "a page for any one name". Look
up `/`. The mailroom sends it to that route, with the name `''`. The
notebook says nothing fits: there *is* no name in `/`. Hypothesis shrank
long, messy stories down to **two steps**: one route, and the shortest
address there is.

The example tests all still pass. Only the property notices. The
examples check `<int:id>` against an empty piece (`/n/`), but none of
them tries an empty piece against a plain `<name>`. The `RouteMachine`
caught it; every property in `tests/test_properties.py` and the
`HeadersMachine` stayed green, because they never touch the router.

Put the line back to `if not part:` and run the tests again. They pass.

!!! tip "Older Hypothesis versions"
    Older versions of Hypothesis print `Falsifying example:` instead of
    `Failing test case:`. It means the same thing.

## A Bug We Found

This one was found by a person, not the inspector: a reviewer reading
`body_length` spotted a bare `.strip()`. We then wrote
`test_content_length_follows_the_plain_rules`, and on the old code the
inspector found it straight away:

```
E       AssertionError: assert ('length', 0) == ('refused', 400)
E       Failing test case: test_content_length_follows_the_plain_rules(
E           values=['0\x0b'],
E       )
```

`Content-Length: 0` followed by a **vertical tab** (`\x0b`) was read as
`0`. It should be refused with `400`.

HTTP says only spaces and tabs are padding, but Python's `.strip()`
removes *every* kind of blank. So PyWeb ignored bytes another server
might not, and two programs disagreeing about where a body ends is the
trick behind request smuggling (see
[Limits and Timeouts](concepts/limits-and-timeouts.md#tricky-requests)).

The example tests tried `"abc"`, `"-1"` and `"5 5"`, but nobody thought
of a blank that isn't a space. The property's strategy makes them on
purpose.

The fix was to trim only spaces and tabs (in `wants_keep_alive` too):

```python
    tokens = [t.strip(_PADDING) for value in lengths for t in value.split(",")]  # was: t.strip()
```

where `_PADDING = " \t"`. The shrunk input is now an ordinary example
test in `tests/aio/test_http1.py`:

```python
    def test_blank_lookalike_padding_is_400(self) -> None:
        """Reject ``0`` padded with a vertical tab: only spaces and tabs are padding."""
        # Found in review; the Content-Length property shrinks to this input.
        with pytest.raises(HttpParseError) as info:
            body_length(head([("Content-Length", "0\x0b")]))
        assert info.value.status == BAD_REQUEST
```

The property didn't *find* this bug, but now it guards against it and
every other padding or digit mix-up its strategy can make.

## Run It Yourself

Run just the property tests, and the model check (100 examples each, the
same every time):

```bash
uv run pytest tests/test_properties.py tests/test_model.py --no-cov
```

The `--no-cov` turns off the coverage check, which expects the *whole*
test suite to run and would fail on two files.

Run them the long way (2,000 random examples each, different every run)
to hunt for rarer bugs. It takes a few minutes:

```bash
uv run pytest tests/test_properties.py tests/test_model.py --no-cov --hypothesis-profile=thorough
```

The normal run uses a fixed seed, so it tries the same inputs every time
and a passing build means the same thing on every computer. The thorough
run is random, so each time you run it the inspector brings a brand-new
sack of letters. Both settings live in `conftest.py` at the top of the
project.

## Your Turn

Write one new property for PyWeb. Think about `wants_keep_alive` in
`pyweb/http1.py`. If a request carries a `Connection: close` header,
should the connection ever stay open, whatever else the head says?
What's the strategy? (Hint: `request_heads()` plus one extra header.)
What's the `assert`? Run it and watch it pass. Then break
`wants_keep_alive` on purpose and watch the inspector find it.
