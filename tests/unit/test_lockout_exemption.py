"""Account lockout exemption.

Requested: exclude the admin account from lockout, since it is the one
account every other account's lockout can be recovered through, and
being permanently locked out of it is the worse failure mode.

Built as a configurable, visible per-account flag rather than a
hardcoded check against the literal username "admin" -- that would be
fragile against a rename and invisible to anyone reading the
authentication code, inconsistent with how every other business rule
in this system is configuration rather than code.
"""
import pytest

from app.modules.auth.service import AuthService, AccountLockedError


@pytest.fixture()
def exempt_user(db):
    from app.modules.user_management.service import UserService
    user = UserService().create_user(
        username="exemptuser", email="exempt@example.com",
        password="Passw0rd!23", is_lockout_exempt=True)
    return user


@pytest.fixture()
def ordinary_user(db):
    from app.modules.user_management.service import UserService
    user = UserService().create_user(
        username="ordinaryuser", email="ordinary@example.com",
        password="Passw0rd!23")
    return user


def test_fresh_seeded_admin_is_exempt_by_default(app, db):
    """The literal request: a brand new install's admin account should
    never be able to lock itself out."""
    from app.cli import _seed_admin
    from app.modules.user_management.models import User
    _seed_admin("Admin123!")
    admin = User.query.filter_by(username="admin").first()
    assert admin.is_lockout_exempt is True


def test_exempt_account_never_locks_no_matter_how_many_failures(
        db, exempt_user):
    """Hammered well past the normal threshold -- must never raise."""
    for _ in range(20):
        AuthService().authenticate("exemptuser", "wrong-password")
    try:
        AuthService().authenticate("exemptuser", "still-wrong")
    except AccountLockedError:
        pytest.fail("an exempt account was locked out")


def test_exempt_account_still_authenticates_with_the_real_password(
        db, exempt_user):
    for _ in range(20):
        AuthService().authenticate("exemptuser", "wrong-password")
    result = AuthService().authenticate("exemptuser", "Passw0rd!23")
    assert result is not None
    assert result.username == "exemptuser"


def test_failed_attempts_are_still_counted_for_an_exempt_account(
        db, exempt_user):
    """Not locked, but still tracked -- a spike of failed attempts
    against an exempt account remains a visible security signal even
    though it never blocks sign-in."""
    for _ in range(3):
        AuthService().authenticate("exemptuser", "wrong-password")
    db.session.refresh(exempt_user)
    assert exempt_user.failed_login_attempts == 3


def test_an_ordinary_account_still_locks_normally(app, db, ordinary_user):
    """The exemption must not weaken lockout for accounts that are not
    explicitly marked exempt -- confirmed the mechanism still works for
    everyone else."""
    max_attempts = app.config["MAX_FAILED_LOGIN_ATTEMPTS"]
    for _ in range(max_attempts):
        AuthService().authenticate("ordinaryuser", "wrong-password")
    with pytest.raises(AccountLockedError):
        AuthService().authenticate("ordinaryuser", "wrong-password")


def test_existing_admin_account_is_not_retroactively_exempted(db):
    """Re-running the seed against an admin account that ALREADY
    exists must not silently flip the exemption on -- that would be
    an invisible, undiscussed change to an existing account's security
    posture. It's a deliberate choice an administrator makes via the
    UI, not something a routine re-seed should decide for them."""
    from app.modules.user_management.models import User
    from app.cli import _seed_admin

    _seed_admin("Admin123!")
    admin = User.query.filter_by(username="admin").first()
    admin.is_lockout_exempt = False   # an administrator's own choice
    db.session.commit()

    _seed_admin("Admin123!")          # re-run, as flask seed all would
    db.session.refresh(admin)
    assert admin.is_lockout_exempt is False


def test_users_list_shows_locked_for_a_non_exempt_account(app, db):
    from app.core.security.registry import sync_permissions
    from app.cli import _seed_admin
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")

    from app.modules.user_management.service import UserService
    user = UserService().create_user(
        username="lockedordinary", email="lo@example.com",
        password="Passw0rd!23")
    user.failed_login_attempts = 5
    db.session.commit()

    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"})
    html = client.get("/admin/users").get_data(as_text=True)
    assert "Locked" in html


def test_users_list_shows_watch_not_locked_for_an_exempt_account(app, db):
    """The distinction that matters visually: an exempt account with a
    high failure count must read as a signal to watch, never as
    "Locked" -- since it isn't."""
    from app.core.security.registry import sync_permissions
    from app.cli import _seed_admin
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")

    from app.modules.user_management.service import UserService
    user = UserService().create_user(
        username="exemptwatched", email="ew@example.com",
        password="Passw0rd!23", is_lockout_exempt=True)
    user.failed_login_attempts = 5
    db.session.commit()

    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"})
    html = client.get("/admin/users").get_data(as_text=True)
    assert "Watch" in html
    row_start = html.index("exemptwatched")
    row = html[row_start:row_start + 800]
    assert "Locked" not in row
