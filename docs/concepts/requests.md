# Requests

## The Incoming Letter

Every time your browser visits a website, it sends a **letter** to the
server. This letter is called an HTTP **request**. Just like a real
letter, it has three parts:

### 1. The Request Line (the envelope)

The very first line tells the server what you want:

```
GET /about HTTP/1.1
```

- **GET** -- the method (what you want to do)
- **/about** -- the path (which page you want)
- **HTTP/1.1** -- the version (which "language" to speak)

### 2. The Headers (the sticky notes)

After the first line come the **headers** -- extra notes attached to
the letter:

```
Host: example.com
Accept: text/html
User-Agent: Mozilla/5.0
```

Each header is a name and a value, separated by a colon. They tell
the server things like "I'm a browser" or "I prefer HTML."

### 3. The Body (the letter inside)

Some requests carry data -- like a form you filled out. The body comes
after a blank line:

```
POST /login HTTP/1.1
Host: example.com
Content-Type: application/json

{"username": "alice", "password": "secret"}
```

GET requests usually have no body. POST requests usually do.

## Query Parameters

Sometimes you put extra info right in the URL:

```
GET /search?q=pokemon&page=2 HTTP/1.1
```

The `?q=pokemon&page=2` part is the **query string**. PyWeb parses
it into a dictionary: `{"q": "pokemon", "page": "2"}`.

## How PyWeb Parses a Request

```python
from pyweb.request import parse_request

raw = "GET /search?q=pokemon HTTP/1.1\r\nHost: example.com\r\n\r\n"
request = parse_request(raw)

request.method       # "GET"
request.path         # "/search"
request.headers      # {"Host": "example.com"}
request.query_params # {"q": "pokemon"}
request.body         # ""
```

## What We Test

- GET, POST, PUT, DELETE methods parse correctly.
- Headers are parsed as name-value pairs.
- POST bodies are captured.
- Query strings are split into parameters.
- Malformed requests raise clear errors.

## Next Up

The server has read the letter. Now it needs to write a reply!
Head to [Responses](responses.md).
