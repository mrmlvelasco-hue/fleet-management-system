"""AuthService: credential verification, failed-attempt lockout, last-login."""
from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.core.security.password import verify_password
from app.modules.user_management.models import User


class AccountLockedError(Exception):
    """Raised when a locked account attempts to authenticate."""


class AuthService:
    def authenticate(self, username: str, password: str) -> "User | None":
        user = User.query.filter_by(username=username, is_active=True).first()
        if user is None:
            return None
        max_attempts = current_app.config["MAX_FAILED_LOGIN_ATTEMPTS"]
        if (not user.is_lockout_exempt
                and user.failed_login_attempts >= max_attempts):
            raise AccountLockedError(
                "Account locked after too many failed attempts. "
                "Contact an administrator.")
        if not verify_password(user.password_hash, password):
            # Still counted even for an exempt account -- a spike of
            # failed attempts against it is a real security signal an
            # administrator should be able to see, even though it never
            # actually blocks sign-in for this account.
            user.failed_login_attempts += 1
            db.session.commit()
            return None
        user.failed_login_attempts = 0
        user.last_login_at = datetime.now(timezone.utc)
        db.session.commit()
        return user
