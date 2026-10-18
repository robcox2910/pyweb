"""Strategies -- recipes for random header names, shared by the property tests.

A strategy tells Hypothesis how to invent one kind of input. Each one here
is a named function, named for what it makes, with a one-line docstring.
``tests/test_properties.py`` and ``tests/test_model.py`` both use them.
"""

import string

from hypothesis import strategies as st

# The characters a header name may use: RFC 9110 calls them "tchar"
# (section 5.6.2). Letters, digits and a handful of symbols -- no spaces,
# no colon, nothing that could confuse where the name ends.
HEADER_NAME_CHARS = string.ascii_letters + string.digits + "!#$%&'*+-.^_`|~"

# A header name is rarely long, and a short one shrinks to the same bug.
LONGEST_HEADER_NAME = 12


# Names PyWeb reads itself. A random name is almost never one of these, so
# they're mixed in on purpose to reach the code that reads them.
NAMES_PYWEB_READS = ["Content-Length", "Transfer-Encoding", "Connection", "content-length"]


def header_names() -> st.SearchStrategy[str]:
    """Make any legal header name: ``Host``, ``x-a``, ``!``, ``Content-Length``..."""
    random_names = st.text(alphabet=HEADER_NAME_CHARS, min_size=1, max_size=LONGEST_HEADER_NAME)
    return st.one_of(st.sampled_from(NAMES_PYWEB_READS), random_names)
