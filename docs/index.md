# What Is a Web Server?

## The Post Office of the Internet

Every time you visit a website, your browser sends a **letter** to a
computer called a **server**. The letter says: "Please send me the page
at this address." The server reads the letter, finds the right page,
and sends back a **reply**.

That's a **web server** -- a post office that receives letters
(requests) and sends back replies (responses).

```
Your Browser                    Web Server
    │                               │
    ├── "GET /about" ──────────────►│
    │   (the request)               │
    │                               ├── Find the right page
    │                               │
    │◄─────────── "<html>..." ──────┤
    │   (the response)              │
    │                               │
```

## What's in a Request?

Every request has three parts, just like a real letter:

1. **The method** -- what you want to do (GET = "show me", POST = "here's some data")
2. **The path** -- which page you want (`/about`, `/api/scores`)
3. **The headers** -- extra info (what language you speak, what browser you use)

```
GET /about HTTP/1.1
Host: example.com
Accept: text/html
```

## What's in a Response?

The server's reply also has three parts:

1. **The status code** -- did it work? (200 = yes, 404 = page not found)
2. **The headers** -- info about the reply (content type, length)
3. **The body** -- the actual content (HTML, JSON, an image)

```
HTTP/1.1 200 OK
Content-Type: text/html

<html><body>Hello!</body></html>
```

## Our Building Blocks

| Concept | Analogy | What It Does |
|---------|---------|-------------|
| **Request** | The incoming letter | Parsed from raw HTTP text |
| **Response** | The reply letter | Built and sent back |
| **Router** | The mailroom | Matches paths to handlers |
| **Handler** | The person who writes the reply | A function that builds a response |
| **Template** | A form letter with blanks | HTML with `{{ name }}` placeholders |
| **Server** | The post office | Listens for connections |

## Let's Start!

Head to [Requests](concepts/requests.md) to learn how HTTP requests work.

## Part 2: A Busy Restaurant

One waiter serving one table at a time gets slow. In Part 2 the server
becomes a busy restaurant: one shopkeeper (PyNet's event loop) looks
after many customers at once. Read these in order:

1. [Async Server](concepts/async-server.md) -- opening the restaurant
2. [Keep-Alive](concepts/keep-alive.md) -- customers who stay for more than one order
3. [Limits and Timeouts](concepts/limits-and-timeouts.md) -- polite rules for everyone
4. [Handlers and Threads](concepts/handlers-and-threads.md) -- the slow kitchen job
5. [Middleware](concepts/middleware.md) -- the doorman
6. [Testing Apps](concepts/testing-apps.md) -- practising without customers
7. [Server-Sent Events](concepts/server-sent-events.md) -- the radio station
8. [WebSockets in PyWeb](concepts/websockets-in-pyweb.md) -- the phone line
9. [Escaping](concepts/escaping.md) -- keeping tricks out of the page
10. [Caching](concepts/caching.md) -- remembering the answer
11. [Passwords](concepts/passwords.md) -- keeping secrets secret
12. [Cookies and Sessions](concepts/cookies-and-sessions.md) -- the wristband
13. [CSRF](concepts/csrf.md) -- the forged permission slip
14. [Roles](concepts/roles.md) -- backstage passes

Try the runnable programs in `examples/`: `async_hello.py`,
`sse_clock.py`, `ws_echo_page.py` and `login_demo.py`.
