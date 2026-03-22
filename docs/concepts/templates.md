# Templates

## Form Letters with Blanks

Imagine a birthday party invitation:

> Dear **{{ name }}**, you are invited to **{{ host }}**'s party
> on **{{ date }}** at **{{ time }}**!

You print 20 copies and fill in a different name on each one. The
invitation is the **template**, and the names are the **context**.

A web template works the same way -- it's HTML with `{{ blanks }}`
that get filled in with real data.

## How It Works

```python
from pyweb.template import render

html = render(
    "<h1>Hello, {{ name }}!</h1><p>Your score: {{ score }}</p>",
    {"name": "Alice", "score": 95}
)
# Result: "<h1>Hello, Alice!</h1><p>Your score: 95</p>"
```

The engine finds every `{{ word }}` and replaces it with the matching
value from the context dictionary.

## Using Template Files

Instead of writing HTML in your Python code, you can put it in a file:

**templates/greeting.html:**
```html
<html>
<body>
    <h1>Hello, {{ name }}!</h1>
    <p>Welcome to {{ site_name }}.</p>
</body>
</html>
```

**Python code:**
```python
from pyweb.template import render_file

html = render_file("templates/greeting.html", {
    "name": "Alice",
    "site_name": "PyWeb"
})
```

## What Happens to Missing Values?

If a placeholder has no matching key in the context, it stays as-is:

```python
render("Hello, {{ name }}!", {})
# Result: "Hello, {{ name }}!"
```

This makes it easy to spot mistakes -- you'll see the raw `{{ name }}`
in your page instead of a blank space.

## What We Test

- Single and multiple placeholders are replaced.
- Missing keys are preserved (not errored).
- Integer and boolean values are converted to strings.
- Extra whitespace inside `{{ }}` is handled.
- Template files are loaded and rendered.

## Next Up

We have all the pieces -- parsing requests, building responses,
routing URLs, and rendering templates. Now we need to put them all
together. Head to [Server](server.md).
