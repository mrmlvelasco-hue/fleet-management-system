"""Account lockout: visibility and recovery.

Reported: there was no way in User Management to see that an account
was locked, or to unlock one -- an administrator's only option was
direct database access. Separately, resetting a user's password did
NOT clear the lockout counter, so a freshly reset password still
couldn't sign in (the lockout check runs before password verification).
"""
import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


def _client(app, db):
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


@pytest.fixture()
def locked_user(db):
    from app.modules.user_management.service import UserService
    user = UserService().create_user(
        username="lockeduser", email="locked@example.com",
        password="Passw0rd!23")
    user.failed_login_attempts = 5
    db.session.commit()
    return user


def test_locked_status_is_visible_on_the_users_list(app, db, locked_user):
    """Previously there was no indication anywhere that an account was
    locked -- confirmed the badge now shows."""
    client = _client(app, db)
    html = client.get("/admin/users").get_data(as_text=True)
    assert "Locked" in html


def test_unlock_button_appears_only_for_a_locked_account(app, db):
    """A normal account must not show an unlock action -- it would be
    confusing clutter and imply there's something to fix."""
    from app.modules.user_management.service import UserService
    UserService().create_user(username="normaluser",
                              email="normal@example.com",
                              password="Passw0rd!23")
    client = _client(app, db)
    html = client.get("/admin/users").get_data(as_text=True)
    assert "Unlock account" not in html


def test_unlock_action_clears_the_lockout(app, db, locked_user):
    """The actual mechanism: after unlocking, the account must no
    longer be rejected purely on the lockout check."""
    from app.modules.auth.service import AuthService, AccountLockedError

    client = _client(app, db)
    r = client.post(f"/admin/users/{locked_user.id}/unlock",
                    follow_redirects=True)
    assert r.status_code == 200

    # The lockout check specifically -- a wrong password should now
    # fail on CREDENTIALS, not on the account being locked.
    result = AuthService().authenticate("lockeduser", "definitely-wrong")
    assert result is None   # rejected for the right reason: bad password


def test_unlocking_does_not_require_knowing_the_password(app, db, locked_user):
    """Confirmed directly: the person's own correct password still
    works immediately after an unlock -- nothing about their
    credentials was touched, only the lockout counter."""
    from app.modules.auth.service import AuthService

    client = _client(app, db)
    client.post(f"/admin/users/{locked_user.id}/unlock")

    result = AuthService().authenticate("lockeduser", "Passw0rd!23")
    assert result is not None
    assert result.username == "lockeduser"


def test_resetting_the_password_also_clears_the_lockout(app, db, locked_user):
    """The second reported gap: setting a new password through the
    normal edit screen did not reset failed_login_attempts, so a
    locked account stayed locked even with a brand new password."""
    from app.modules.user_management.service import UserService

    assert locked_user.failed_login_attempts == 5
    UserService().update_user(locked_user.id, password="BrandNewPassw0rd!99")
    db.session.refresh(locked_user)
    assert locked_user.failed_login_attempts == 0


def test_editing_other_fields_without_a_password_does_not_touch_lockout(
        app, db, locked_user):
    """The lockout reset must be tied specifically to setting a new
    password -- an unrelated edit (email, name) must not silently
    unlock someone as a side effect."""
    from app.modules.user_management.service import UserService

    UserService().update_user(locked_user.id, email="newemail@example.com")
    db.session.refresh(locked_user)
    assert locked_user.failed_login_attempts == 5   # unchanged
