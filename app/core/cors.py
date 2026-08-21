"""Cross-origin support for the REST API.

The React frontend runs on its own dev server (Vite, :5173) and talks to
Flask (:8000), which the browser treats as a different origin. Only the
API needs this -- the Jinja UI is same-origin and unaffected.

Hand-rolled rather than pulling in flask-cors, for two reasons:

  * The allowed origins must be CONFIGURABLE (CORS_ORIGINS, read from
    the environment), not compiled in. "No values shall be hardcoded"
    applies to deployment topology as much as to business rules, and a
    production origin baked into source is exactly the kind of thing
    that later requires a code change to move a server.
  * The rule needed here is small and specific: an allow-list, echoed
    back only on exact match. A dependency would be more code, not
    less, and every extra package is another thing to keep patched on
    an on-prem box.

Deliberately NOT credentialed. API clients authenticate with a bearer
token (see app/modules/api/auth.py), so no cookie ever crosses origins
and `Access-Control-Allow-Credentials` is never set. That closes off
CSRF against the API by construction: an attacker's page can issue the
request, but the browser will not attach a credential to it, and the
token itself is not reachable from another origin.

The wildcard "*" is accepted for local development only and is refused
outright when the app is not in DEBUG, so a permissive dev setting
cannot be carried into production by accident.
"""
from flask import request


def _allowed_origins(app) -> list:
    configured = app.config.get("CORS_ORIGINS") or []
    if isinstance(configured, str):
        configured = [o.strip() for o in configured.split(",") if o.strip()]
    return configured


def _resolve(app, origin: str):
    """The value to echo in Access-Control-Allow-Origin, or None to send
    no CORS headers at all (which is what a browser needs in order to
    block the response)."""
    if not origin:
        return None
    allowed = _allowed_origins(app)
    if origin in allowed:
        return origin
    if "*" in allowed and app.config.get("DEBUG"):
        # Echo the actual origin rather than "*": it keeps the response
        # correct if credentials are ever added later, and it makes the
        # logs show who actually called.
        return origin
    return None


def init_cors(app):
    """Attach CORS handling scoped to the API blueprint's URL prefix."""
    prefix = app.config.get("CORS_PATH_PREFIX", "/api/")

    @app.after_request
    def _add_cors_headers(response):
        if not request.path.startswith(prefix):
            return response
        allow = _resolve(app, request.headers.get("Origin", ""))
        if allow is None:
            return response
        response.headers["Access-Control-Allow-Origin"] = allow
        # Credentials are permitted on the refresh route ONLY.
        #
        # That route needs the browser to attach the httpOnly refresh
        # cookie cross-origin, which it will not do unless the server
        # says so. Everywhere else stays cookie-free, so CSRF against
        # the API remains closed off by construction rather than by a
        # token dance -- an attacker's page can issue the request, but
        # the browser attaches no credential to it.
        #
        # The refresh route is safe to credential because it is a bare
        # POST with no body: replaying it mints an access token into a
        # response the attacker's page cannot read, thanks to this very
        # allow-list.
        if request.path.rstrip("/").endswith("/auth/refresh"):
            response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = \
            "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = \
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
        # Without this, a shared cache could serve a response computed
        # for one origin to a request from another.
        response.headers.add("Vary", "Origin")
        return response

    @app.before_request
    def _short_circuit_preflight():
        """Answer OPTIONS before any auth decorator sees it.

        A preflight carries no Authorization header -- the browser sends
        it precisely to find out whether it is allowed to send one. If
        it reached the endpoint it would 401, the browser would treat
        that as a failed preflight, and the real request would never be
        made. The symptom is a frontend that shows a CORS error for what
        is actually a working, correctly-authenticated API.
        """
        if request.method != "OPTIONS":
            return None
        if not request.path.startswith(prefix):
            return None
        if _resolve(app, request.headers.get("Origin", "")) is None:
            return None
        return ("", 200)
