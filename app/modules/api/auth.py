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
