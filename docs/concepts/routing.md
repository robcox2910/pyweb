# Routing

## The Mailroom

Imagine a big office building. Letters arrive at the front desk, and
the mail clerk sorts them: "This one goes to Room 201, this one to
Room 305, and this one... hmm, no room with that number -- return
to sender."

A **router** does the same thing for web requests. Each URL path
(like `/about` or `/api/scores`) is mapped to a **handler function**
that knows how to build the response.

## How It Works

```
Request: GET /about
    │
    └── Router checks its list:
            GET /        → index_handler     ✗ (wrong path)
            GET /about   → about_handler     ✓ Match!
            POST /api    → api_handler       ✗ (wrong method + path)
    │
    └── Calls about_handler(request) → Response
```

## Registering Routes

There are two ways to register a route:

### 1. Using decorators (the easy way)

```python
from pyweb.router import Router
from pyweb.response import html_response

router = Router()

@router.get("/")
def homepage(request):
    return html_response("<h1>Welcome!</h1>")

@router.get("/about")
def about(request):
    return html_response("<h1>About Us</h1>")

@router.post("/api/message")
def create_message(request):
    return json_response({"status": "received"})
```

### 2. Using add_route (the explicit way)

```python
router.add_route("GET", "/contact", contact_handler)
```

## What Happens When No Route Matches?

If someone visits `/nonexistent`, the router returns a **404 Not Found**
response. If they use the wrong method (POST on a GET-only route),
it returns **405 Method Not Allowed**.

## Path Parameters

You can put variables in the path using `<angle_brackets>`:

```python
@router.get("/users/<id>")
def get_user(request):
    user_id = request.params["id"]
    return html_response(f"<h1>User {user_id}</h1>")
```

Visiting `/users/42` sets `request.params["id"]` to `"42"`.

## What We Test

- Routes match by method AND path.
- Unmatched paths return 404.
- Wrong method returns 405.
- Multiple routes work together.
- Decorators register routes correctly.

## Next Up

We can serve different pages for different URLs. But what if we
want to put real data into our HTML? Head to [Templates](templates.md).
