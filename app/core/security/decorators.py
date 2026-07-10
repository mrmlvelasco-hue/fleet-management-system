"""@require_permission('code') — 403s unless current_user holds the permission."""
from functools import wraps

from flask import abort
from flask_login import current_user


def require_permission(code: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.has_permission(code):
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
