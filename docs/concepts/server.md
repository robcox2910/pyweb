# Server

## The Post Office

We've built all the pieces:

- **Requests** -- reading the incoming letters
- **Responses** -- writing the reply letters
- **Router** -- the mailroom that sorts letters to handlers
- **Templates** -- form letters with blanks to fill in

Now we need the **post office** itself -- the building that sits on the
street, waits for letters to arrive, sorts them, and sends replies.

That's the **server**.

## How a Web Server Works

```
1. Open the door (bind to a port)
2. Wait for someone to walk in (accept a connection)
3. Read their letter (receive the HTTP request)
4. Sort it in the mailroom (dispatch through the router)
5. Hand them the reply (send the HTTP response)
6. Go back to step 2
```

In code:

```python
from pyweb.router import Router
from pyweb.response import html_response
from pyweb.server import Server

router = Router()

@router.get("/")
def homepage(request):
    return html_response("<h1>Welcome to my website!</h1>")

server = Server(router, host="127.0.0.1", port=8000)
server.serve_forever()
```

Visit `http://127.0.0.1:8000` in your browser and you'll see your page!

## What's a Port?

Think of the server's address like a big apartment building. The **IP
address** (127.0.0.1) is the street address, and the **port** (8000)
is the apartment number. Many servers can run on the same computer,
each on a different port.

- Port 80 = the standard "front door" for websites
- Port 443 = the secure (HTTPS) door
- Port 8000 = a side door we use for development

## What's a Socket?

Under the hood, the server uses a **socket** -- a connection point,
like a mailbox slot. The server creates a socket, binds it to a port,
and listens for incoming letters. When a letter arrives, it reads it,
processes it, and drops the reply back into the slot.

## Static Files

PyWeb can serve files directly from a folder -- HTML pages, CSS
stylesheets, images, and JavaScript:

```python
from pyweb.static import static_handler

router.add_route("GET", "/static", static_handler("./public"))
```

Now files in the `./public` folder are accessible at `/static/style.css`,
`/static/logo.png`, etc.

## Logging

Every request is logged so you can see what is happening:

```
GET / -> 200 OK
GET /about -> 200 OK
GET /missing -> 404 Not Found
POST /api/data -> 201 Created
```

## What We Test

- The server processes valid requests and returns correct responses.
- Malformed requests return 500 Internal Server Error.
- Unknown paths return 404.
- The server can be started and stopped.

## What's Next?

You've built a complete web server from scratch! It can parse HTTP
requests, route URLs, render templates, serve static files, and
send responses. That's the same thing nginx, Apache, and Flask do --
just simpler and built for learning.
