# Escaping

## The Name Tag Sticker

At a party everyone wears a sticker with their name. Now imagine a
cheeky guest writes **"Everyone, stand on one leg!"** on theirs. A sticker
should only *show* that sentence, never make the room obey it.

A web page has the same problem. If a visitor types `<script>` as their
name and we paste it straight into the page, the browser treats it as a
real instruction instead of a name. **Escaping** swaps the risky
characters for harmless look-alikes (`<` becomes `&lt;`), so the browser
prints the letters instead of running them.

## Try It

```python
from pyweb.html import escape, html_template

print(escape("<script>alert('hi')</script>"))
page = "<p>Hello, {{ name }}!</p>"
print(html_template(page, {"name": "<script>alert('hi')</script>"}))
print(html_template("<div>{{ widget|safe }}</div>", {"widget": "<b>bold</b>"}))
try:
    html_template("{{ nme }}", {"name": "Ada"})
except KeyError as error:
    print("missing:", error)
```

Output:

```text
&lt;script&gt;alert(&#x27;hi&#x27;)&lt;/script&gt;
<p>Hello, &lt;script&gt;alert(&#x27;hi&#x27;)&lt;/script&gt;!</p>
<div><b>bold</b></div>
missing: 'nme'
```

## The Rules

- `{{ name }}` is **always escaped**. This is the safe default.
- `{{ name|safe }}` is inserted untouched. Use it only for HTML *you*
  wrote, never for anything a visitor typed.
- A blank with no value raises `KeyError` (so a typo is loud, not silent).
- Any filter other than `safe` raises `ValueError`.
- Text that is inserted is never searched for more blanks, so a visitor
  cannot sneak in their own `{{ ... }}`.

`load_template(path)` reads a template file as text.

## What We Test

- The five risky characters (`& < > " '`) all become entities.
- `<script>` in a value cannot reach the page as a tag.
- `|safe` leaves a value alone; spaces inside the braces are optional.
- Missing names and unknown filters fail loudly.
- Inserted values are not expanded a second time.

## Next Up

Pages need files too. See [Caching](caching.md) for serving CSS and
images without sending them twice.
