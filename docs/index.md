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
