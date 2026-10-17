# Caching

## "I Already Have That One"

You borrow a library book, and next week you want it again. You show the
librarian the barcode from last time. If the book has not changed she says
"same book, keep yours", with no need to carry a heavy copy back and
forth.

Browsers do exactly this. Each file we serve carries an **ETag**, a
fingerprint of its bytes (here the SHA-256 hash, so it changes exactly
when the file does). Next time the browser sends it back in
`If-None-Match`. If it still matches we answer `304 Not Modified` with no
body at all.

## Try It

```python
import tempfile
from pathlib import Path

from pynet.loop import run

from pyweb.app import App
from pyweb.testing import TestClient

folder = Path(tempfile.mkdtemp())
(folder / "style.css").write_text("body { color: teal }")
app = App()
app.mount_static("/static", folder, max_age=600)


async def main() -> None:
    client = TestClient(app)
    first = await client.get("/static/style.css")
    tag = first.headers.get("ETag")
    print(first.status, first.headers.get("Cache-Control"))
    print("etag length:", len(tag or ""))
    again = await client.get("/static/style.css", headers={"If-None-Match": tag or ""})
    print(again.status, again.body)
    sneaky = await client.get("/static/%2e%2e/secret.txt")
    print(sneaky.status)


run(main())
```

Output:

```text
200 public, max-age=600
etag length: 66
304 b''
404
```

`Cache-Control: public, max-age=600` tells the browser it may reuse the
file for 600 seconds without even asking (the default is 3600).

## Staying Inside the Folder

`mount_static` only hands out plain files from the folder you give it.
Each of these answers a plain `404`:

- `..` or `%2e%2e` trying to climb out of the folder
- any name starting with a dot, such as `.env`
- empty or `.` path pieces
- a symlink that points outside the folder
- a folder instead of a file

The file is read on a helper thread, so a big file never freezes the
loop for other visitors.

## What We Test

- A file comes back with its `ETag` and `Cache-Control`.
- A matching `If-None-Match` (plain, weak `W/"..."`, `*`, or a list) gives
  `304` with no body.
- Binary files arrive byte for byte; `HEAD` has headers but no body.
- Encoded traversal, dotfiles and outside symlinks are all `404`.

## Next Up

Back to [Middleware](middleware.md) to wrap every request, static files
included.
