# Responses

## The Reply Letter

When the server gets a request, it needs to send back a **response**.
Think of it as the reply letter -- it tells the browser what happened
and includes the content it asked for.

## The Three Parts

### 1. The Status Line

```
HTTP/1.1 200 OK
```

The **status code** is a number that says what happened:

| Code | Meaning | Analogy |
|------|---------|---------|
| **200** | OK -- here's your page | "Found it! Here you go." |
| **201** | Created -- made something new | "Done! I created it for you." |
| **301** | Moved -- it's at a new address | "They moved. Try this address." |
| **400** | Bad Request -- I don't understand | "Your letter doesn't make sense." |
| **404** | Not Found -- page doesn't exist | "No one lives at that address." |
| **405** | Method Not Allowed -- wrong verb | "You can visit but not deliver." |
| **500** | Server Error -- something broke | "Sorry, I dropped your letter." |

### 2. The Headers

```
Content-Type: text/html
Content-Length: 42
```

Headers tell the browser what kind of content is coming (HTML? JSON?
an image?) and how big it is.

### 3. The Body

The actual content -- the HTML page, the JSON data, or the image.

## Building Responses in PyWeb

```python
from pyweb.response import html_response, json_response, text_response, not_found

# HTML page
resp = html_response("<h1>Hello!</h1>")

# JSON API response
resp = json_response({"name": "Pikachu", "power": 55})

# Plain text
resp = text_response("Just some text")

# 404 error page
resp = not_found("That page doesn't exist")
```

## What We Test

- Responses serialize to valid HTTP bytes.
- Content-Length is calculated automatically.
- Helper functions set the right Content-Type.
- Status codes map to the right phrases.

## Next Up

We can read letters and write replies. But how does the server know
*which* reply to send? Head to [Routing](routing.md).
