import pytest

from app.core.security.password import hash_password
from app.modules.auth.service import AuthService, AccountLockedError
from app.modules.user_management.models import User


@pytest.fixture()
def user(db):
    u = User(username="carol", email="carol@example.com",
             password_hash=hash_password("pw123"))
    db.session.add(u)
    db.session.commit()
    return u


def test_authenticate_success_resets_counter_and_sets_last_login(app, user, db):
    user.failed_login_attempts = 2
    db.session.commit()
    svc = AuthService()
    result = svc.authenticate("carol", "pw123")
    assert result.id == user.id
    assert user.failed_login_attempts == 0
    assert user.last_login_at is not None


def test_authenticate_wrong_password_increments_counter(app, user, db):
    svc = AuthService()
    assert svc.authenticate("carol", "nope") is None
    assert user.failed_login_attempts == 1


def test_lockout_after_max_attempts(app, user, db):
    svc = AuthService()
    for _ in range(app.config["MAX_FAILED_LOGIN_ATTEMPTS"]):
        svc.authenticate("carol", "nope")
    with pytest.raises(AccountLockedError):
        svc.authenticate("carol", "pw123")


def test_authenticate_unknown_user_returns_none(app, db):
    assert AuthService().authenticate("ghost", "pw") is None
