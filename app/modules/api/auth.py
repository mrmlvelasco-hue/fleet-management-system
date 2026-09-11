"""JWT authentication for the REST API.

Deliberately separate from the web UI's session login (Flask-Login),
which stays exactly as it is: browsers keep using sessions and CSRF,
while API clients (the mobile app, GPS/telematics units) present a
bearer token. Both paths resolve to the SAME User row and therefore the
SAME role/permission checks -- there is no parallel authorisation model
to keep in sync, and revoking a user's access in the UI revokes their
API access too.
"""
import functools
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from flask import current_app, jsonify, request

from app.extensions import db
from app.modules.user_management.models import User

# Short-lived by design: a token that leaks off a driver's phone or out
# of a vehicle-mounted GPS unit should stop working quickly. Clients
# re-authenticate rather than holding a long-lived credential.
TOKEN_TTL_HOURS = 12


def _secret() -> str:
    return current_app.config["SECRET_KEY"]


def issue_token(user: User, ttl_hours: int = TOKEN_TTL_HOURS) -> dict:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=ttl_hours)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        # Marks this as an ACCESS token. _user_from_request refuses
        # anything else, so the long-lived refresh token cannot be
        # presented as a bearer and open every endpoint directly.
        "typ": "access",
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, _secret(), algorithm="HS256")
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_at": expires_at.isoformat(),
        "expires_in": ttl_hours * 3600,
    }


def _user_from_request():
    """Resolve the caller from the Authorization header, or return
    (None, error_message)."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None, "Missing or malformed Authorization header. Expected "\
                     "'Authorization: Bearer <token>'."
    token = header[7:].strip()
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
        if payload.get("typ") == "refresh":
            return None, "Refresh tokens cannot be used to call the API."
    except jwt.ExpiredSignatureError:
        return None, "Token has expired. Request a new one from "\
                     "/api/v1/auth/token."
    except jwt.InvalidTokenError:
        return None, "Invalid token."

    user = db.session.get(User, int(payload.get("sub", 0)))
    if user is None or not user.is_active:
        # Covers a token issued before the account was disabled -- the
        # token itself is still cryptographically valid, so the account
        # state must be re-checked on every request, not just at login.
        return None, "User account is no longer active."
    return user, None


def api_auth_required(permission: str = None):
    """Authenticate the bearer token and, optionally, require a specific
    permission -- the SAME permission codes the web UI uses, so API
    access can never exceed what that user could do in the browser."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user, error = _user_from_request()
            if user is None:
                return jsonify({"error": "unauthorized",
                               "message": error}), 401
            if permission and not user.has_permission(permission):
                return jsonify({
                    "error": "forbidden",
                    "message": f"This account lacks the '{permission}' "
                               f"permission."}), 403
            return fn(*args, api_user=user, **kwargs)
        return wrapper
    return decorator


# ── Refresh tokens ──────────────────────────────────────────────────────────
#
# The React app holds its ACCESS token in memory only, so a new tab or a
# refresh has no credential. That is deliberate -- a token JavaScript can
# read is a token an injected script can steal -- but it means ordinary
# actions like ctrl-clicking a row into a background tab log the user
# out.
#
# The refresh token closes that gap without giving the credential back to
# JavaScript: it lives in an httpOnly cookie the browser attaches
# automatically and no script can read. A new tab exchanges it for a
# short-lived access token and carries on.
#
# The two token types are deliberately distinct (`typ`). If an access
# token could be replayed at the refresh endpoint, its short lifetime --
# the whole reason it is safe to hold in memory -- would mean nothing;
# and if a refresh token were accepted as a bearer, the long-lived
# credential would open every endpoint directly.

REFRESH_COOKIE = "fms_refresh"
REFRESH_TTL_DAYS = 14
# Scoped to the auth routes so an ordinary API call never carries it.
# A request that has no business refreshing has no business holding the
# credential that could.
REFRESH_COOKIE_PATH = "/api/v1/auth"


def issue_refresh_token(user: User) -> tuple:
    """Returns (token, expires_at).

    The jti makes every issued token unique. Without it, two tokens
    minted in the same second for the same user are BYTE-IDENTICAL --
    iat and exp are second-resolution -- so "rotation" produced the same
    string back and a caller could not tell a rotated token from a
    replayed one.

    LIMITATION worth stating plainly: these are stateless JWTs with no
    server-side store, so rotation does not REVOKE the previous token.
    An old refresh token keeps working until it expires (14 days).
    Rotation limits the window in which a captured token is the *current*
    one; it does not close it. Genuinely revoking a session -- the case
    that matters when a driver's phone is lost -- needs a jti denylist
    or a per-user token generation counter checked on every refresh.
    The jti here is what makes that possible to add later without
    reissuing anyone's credentials.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=REFRESH_TTL_DAYS)
    token = jwt.encode({
        "sub": str(user.id),
        "typ": "refresh",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": expires_at,
    }, _secret(), algorithm="HS256")
    return token, expires_at


def user_from_refresh_token(token: str):
    """The user this refresh token belongs to, or None."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
    except Exception:
        return None
    if payload.get("typ") != "refresh":
        return None
    user = db.session.get(User, int(payload.get("sub") or 0))
    # Re-checked on every refresh, not just at login: disabling an
    # account must end access promptly, and a refresh token that kept
    # minting credentials for a disabled user would outlive the decision
    # to revoke them.
    if user is None or not user.is_active:
        return None
    return user


def set_refresh_cookie(response, token, expires_at, remember=True):
    """Persist the refresh token, or keep it for this browser session.

    `remember=False` omits `expires`, which makes it a SESSION cookie:
    the browser discards it when it closes. The token itself is still
    valid server-side for its full life -- this only controls how long
    the browser keeps hold of it. That is the honest scope of a
    "Remember me" checkbox, and it is worth being precise about,
    because it means unchecking the box protects the next person at
    this machine, not against someone who already captured the token.
    """
    response.set_cookie(
        REFRESH_COOKIE, token,
        httponly=True,
        # Lax rather than Strict: the cookie must survive a normal
        # top-level navigation into a new tab, which is the case this
        # whole mechanism exists to serve.
        samesite="Lax",
        # Its own setting, NOT `not DEBUG`.
        #
        # Deriving it from DEBUG tied this to how Flask was launched: a
        # run configuration that never loaded .env produced a Secure
        # cookie on a plain-http LAN address, which Chrome drops on
        # arrival without an error anyone would notice. Login kept
        # working (the access token is in the body), so the damage only
        # showed up later as a 401 from /auth/refresh in a new tab.
        # Defaults secure; see app.config._refresh_cookie_secure.
        secure=current_app.config.get("REFRESH_COOKIE_SECURE", True),
        path=REFRESH_COOKIE_PATH,
        # Omitted entirely when not remembering -- a cookie with no
        # expiry is a session cookie. Setting expires=None explicitly
        # would be the same thing, but leaving the argument out makes
        # the intent readable at the call site.
        **({"expires": expires_at} if remember else {}),
    )
    return response


def clear_refresh_cookie(response):
    response.set_cookie(REFRESH_COOKIE, "", expires=0,
                        path=REFRESH_COOKIE_PATH, httponly=True)
    return response
