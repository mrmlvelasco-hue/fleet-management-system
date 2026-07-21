"""Tests for PasswordPolicyService — wiring up PASSWORD_MIN_LENGTH,
PASSWORD_MAX_LENGTH, PASSWORD_HISTORY_LENGTH, PASSWORD_EXPIRY_DAYS, and
PASSWORD_WARNING_DAYS, which existed as System Parameters since Phase 1c
but were never actually enforced anywhere -- the password forms had
their own hardcoded Length(min=8), inconsistent with PASSWORD_MIN_LENGTH's
own default of 6, and nothing else was checked at all.
"""
from datetime import datetime, timezone, timedelta

import pytest

from app.core.security.password_policy import (
    PasswordPolicyService, PasswordPolicyError)
from app.modules.user_management.service import UserService
from app.modules.user_management.models import User, PasswordHistory


def _seed_password_params(db, **overrides):
    from app.modules.system_admin.models import SystemParameter
    values = {
        "PASSWORD_MIN_LENGTH": "6", "PASSWORD_MAX_LENGTH": "20",
        "PASSWORD_HISTORY_LENGTH": "6", "PASSWORD_EXPIRY_DAYS": "90",
        "PASSWORD_WARNING_DAYS": "30",
    }
    values.update(overrides)
    for code, value in values.items():
        db.session.add(SystemParameter(code=code, value=value,
                                       data_type="INTEGER"))
    db.session.commit()


def test_rejects_password_shorter_than_configured_minimum(db):
    _seed_password_params(db)
    with pytest.raises(PasswordPolicyError):
        PasswordPolicyService().validate_new_password("abc12")  # 5 chars


def test_rejects_password_longer_than_configured_maximum(db):
    _seed_password_params(db)
    with pytest.raises(PasswordPolicyError):
        PasswordPolicyService().validate_new_password("a" * 21)


def test_accepts_password_within_configured_bounds(db):
    _seed_password_params(db)
    PasswordPolicyService().validate_new_password("GoodPass123")  # no raise


def test_create_user_records_initial_password_in_history(db):
    _seed_password_params(db)
    user = UserService().create_user(
        username="histtest", email="h@example.com", password="FirstPass1")
    assert user.password_changed_at is not None
    assert PasswordHistory.query.filter_by(user_id=user.id).count() == 1


def test_rejects_reuse_of_a_password_within_history_window(db):
    _seed_password_params(db)
    user = UserService().create_user(
        username="histtest2", email="h2@example.com", password="FirstPass1")
    with pytest.raises(PasswordPolicyError):
        PasswordPolicyService().validate_new_password("FirstPass1", user=user)


def test_accepts_a_genuinely_new_password_not_in_history(db):
    _seed_password_params(db)
    user = UserService().create_user(
        username="histtest3", email="h3@example.com", password="FirstPass1")
    PasswordPolicyService().validate_new_password("CompletelyDifferent2", user=user)


def test_history_length_of_zero_disables_history_check(db):
    _seed_password_params(db, PASSWORD_HISTORY_LENGTH="0")
    user = UserService().create_user(
        username="histtest4", email="h4@example.com", password="FirstPass1")
    # Should NOT raise, since history checking is disabled.
    PasswordPolicyService().validate_new_password("FirstPass1", user=user)


def test_password_never_changed_is_not_expired(db):
    _seed_password_params(db)
    user = User(username="expirytest", email="e@example.com",
               password_hash="x", password_changed_at=None)
    db.session.add(user)
    db.session.commit()
    assert PasswordPolicyService().is_expired(user) is False


def test_password_older_than_expiry_days_is_expired(db):
    _seed_password_params(db)
    user = User(username="expirytest2", email="e2@example.com",
               password_hash="x",
               password_changed_at=datetime.now(timezone.utc) - timedelta(days=100))
    db.session.add(user)
    db.session.commit()
    assert PasswordPolicyService().is_expired(user) is True


def test_password_within_expiry_window_is_not_expired(db):
    _seed_password_params(db)
    user = User(username="expirytest3", email="e3@example.com",
               password_hash="x",
               password_changed_at=datetime.now(timezone.utc) - timedelta(days=10))
    db.session.add(user)
    db.session.commit()
    assert PasswordPolicyService().is_expired(user) is False


def test_expiring_soon_within_warning_window_but_not_yet_expired(db):
    _seed_password_params(db)
    user = User(username="expirytest4", email="e4@example.com",
               password_hash="x",
               password_changed_at=datetime.now(timezone.utc) - timedelta(days=70))
    db.session.add(user)
    db.session.commit()
    svc = PasswordPolicyService()
    assert svc.is_expired(user) is False
    assert svc.is_expiring_soon(user) is True
    assert svc.days_until_expiry(user) == 19


def test_expiry_disabled_when_expiry_days_is_zero(db):
    _seed_password_params(db, PASSWORD_EXPIRY_DAYS="0")
    user = User(username="expirytest5", email="e5@example.com",
               password_hash="x",
               password_changed_at=datetime.now(timezone.utc) - timedelta(days=1000))
    db.session.add(user)
    db.session.commit()
    assert PasswordPolicyService().is_expired(user) is False


def test_record_password_change_prunes_history_beyond_configured_length(db):
    _seed_password_params(db, PASSWORD_HISTORY_LENGTH="2")
    user = UserService().create_user(
        username="prunetest", email="p@example.com", password="Pass1")
    svc = PasswordPolicyService()
    svc.record_password_change(user, "hash2")
    svc.record_password_change(user, "hash3")
    db.session.commit()
    # Only the most recent 2 should remain, even though 3 changes happened.
    assert PasswordHistory.query.filter_by(user_id=user.id).count() == 2
