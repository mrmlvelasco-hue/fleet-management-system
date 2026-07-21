"""Password policy enforcement.

PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH, PASSWORD_HISTORY_LENGTH,
PASSWORD_EXPIRY_DAYS, and PASSWORD_WARNING_DAYS have existed as System
Parameters since Phase 1c but were never actually consumed anywhere:
the password forms hardcoded their own `Length(min=8)` (inconsistent
with PASSWORD_MIN_LENGTH's own default of 6), max length wasn't checked
at all, nothing recorded password history, and nothing tracked when a
password was last changed to know if it had expired. This service is
what actually enforces all five.
"""
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.core.security.password import verify_password


class PasswordPolicyError(Exception):
    pass


class PasswordPolicyService:
    def __init__(self):
        from app.modules.system_admin.services.system_parameter_service import (
            SystemParameterService)
        self._params = SystemParameterService()

    def _int_param(self, code, default):
        try:
            return int(self._params.get(code, default=default))
        except (TypeError, ValueError):
            return default

    def validate_new_password(self, plain_password: str, user=None) -> None:
        """Raises PasswordPolicyError with a message suitable for
        showing the person directly. Checks length against
        PASSWORD_MIN_LENGTH/MAX_LENGTH, and — if `user` is given (i.e.
        this is a change for an existing account, not a brand-new one) —
        against PASSWORD_HISTORY_LENGTH previous passwords."""
        min_len = self._int_param("PASSWORD_MIN_LENGTH", 6)
        max_len = self._int_param("PASSWORD_MAX_LENGTH", 20)
        if len(plain_password) < min_len:
            raise PasswordPolicyError(
                f"Password must be at least {min_len} characters.")
        if len(plain_password) > max_len:
            raise PasswordPolicyError(
                f"Password must be at most {max_len} characters.")

        if user is not None:
            history_len = self._int_param("PASSWORD_HISTORY_LENGTH", 6)
            if history_len > 0:
                from app.modules.user_management.models import PasswordHistory
                recent = (PasswordHistory.query
                         .filter_by(user_id=user.id)
                         .order_by(PasswordHistory.id.desc())
                         .limit(history_len).all())
                for entry in recent:
                    if verify_password(entry.password_hash, plain_password):
                        raise PasswordPolicyError(
                            f"You can't reuse any of your last "
                            f"{history_len} passwords. Please choose a "
                            f"different one.")

    def record_password_change(self, user, new_hash: str) -> None:
        """Call this AFTER user.password_hash has been set to the new
        hash and the change is being committed — records it into history
        (pruning old entries beyond PASSWORD_HISTORY_LENGTH) and stamps
        password_changed_at so expiry can be computed from here forward."""
        from app.modules.user_management.models import PasswordHistory

        db.session.add(PasswordHistory(user_id=user.id,
                                       password_hash=new_hash))
        user.password_changed_at = datetime.now(timezone.utc)
        db.session.flush()

        history_len = self._int_param("PASSWORD_HISTORY_LENGTH", 6)
        all_entries = (PasswordHistory.query
                      .filter_by(user_id=user.id)
                      .order_by(PasswordHistory.id.desc()).all())
        for stale in all_entries[history_len:]:
            db.session.delete(stale)

    def is_expired(self, user, as_of=None) -> bool:
        """False (not expired) if password_changed_at was never recorded
        — that's true for every account that existed before this policy
        was wired up, and force-expiring everyone's password the moment
        this ships would be a bad surprise, not a security improvement.
        The clock starts the next time each of them actually changes
        their password."""
        if user.password_changed_at is None:
            return False
        expiry_days = self._int_param("PASSWORD_EXPIRY_DAYS", 90)
        if expiry_days <= 0:
            return False  # 0/negative = expiry disabled
        as_of = as_of or datetime.now(timezone.utc)
        changed_at = user.password_changed_at
        if changed_at.tzinfo is None:
            changed_at = changed_at.replace(tzinfo=timezone.utc)
        return as_of >= changed_at + timedelta(days=expiry_days)

    def days_until_expiry(self, user, as_of=None):
        """None if not applicable (no change date on record, or expiry
        disabled) — otherwise the number of days remaining, which can be
        negative if already expired."""
        if user.password_changed_at is None:
            return None
        expiry_days = self._int_param("PASSWORD_EXPIRY_DAYS", 90)
        if expiry_days <= 0:
            return None
        as_of = as_of or datetime.now(timezone.utc)
        changed_at = user.password_changed_at
        if changed_at.tzinfo is None:
            changed_at = changed_at.replace(tzinfo=timezone.utc)
        expires_at = changed_at + timedelta(days=expiry_days)
        return (expires_at - as_of).days

    def is_expiring_soon(self, user, as_of=None) -> bool:
        """For a non-blocking heads-up banner, not a forced redirect —
        within PASSWORD_WARNING_DAYS of expiring, but not expired yet
        (that case is handled by is_expired forcing a change instead)."""
        days_left = self.days_until_expiry(user, as_of=as_of)
        if days_left is None:
            return False
        warning_days = self._int_param("PASSWORD_WARNING_DAYS", 30)
        return 0 <= days_left <= warning_days
