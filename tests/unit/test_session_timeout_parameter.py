"""SESSION_TIMEOUT_MINUTES and the new SESSION_WARNING_MINUTES, actually
reaching the React app.

SESSION_TIMEOUT_MINUTES already existed -- seeded, shown in System
Parameters, described as "Session timeout in minutes" -- but the ONLY
place that read it was app/config.py's PERMANENT_SESSION_LIFETIME,
which governs Flask's own cookie-based `session` object (flask-login,
the legacy server-rendered pages). The React SPA authenticates entirely
through JWT access/refresh tokens and never touches that session at
all, so changing this parameter in System Parameters had zero effect on
how long someone could walk away from the React app and stay logged in
-- the exact "no values shall be hardcoded" breach LIST_PAGES had
before it was wired up (see test_list_pages_parameter.py, same shape
of bug, same fix pattern).

This gives the SPA its own live read of the parameter (session_service
below, mirroring default_page_size()'s exact validate-and-fall-back
shape) plus a new companion parameter for how much warning to give
before logging someone out, both carried on /me the same way
list_page_size already is.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.api.session_settings import (
    FALLBACK_SESSION_TIMEOUT_MINUTES, FALLBACK_SESSION_WARNING_MINUTES,
    session_timeout_minutes, session_warning_minutes)
from app.modules.system_admin.models import SystemParameter
from app.modules.user_management.models import User


def _set(code, value, data_type="INTEGER"):
    p = SystemParameter.query.filter_by(code=code).first()
    if p is None:
        p = SystemParameter(code=code, value=str(value), data_type=data_type,
                            group_name="SECURITY", description="test")
        db.session.add(p)
    else:
        p.value = str(value)
    db.session.commit()


class TestSessionTimeoutMinutes:
    def test_the_parameter_is_read(self, app):
        _set("SESSION_TIMEOUT_MINUTES", 45)
        assert session_timeout_minutes() == 45

    def test_a_missing_parameter_falls_back(self, app):
        assert session_timeout_minutes() == FALLBACK_SESSION_TIMEOUT_MINUTES

    def test_a_nonsense_value_does_not_break_login(self, app):
        _set("SESSION_TIMEOUT_MINUTES", "banana", data_type="STRING")
        assert session_timeout_minutes() == FALLBACK_SESSION_TIMEOUT_MINUTES

    def test_zero_or_negative_falls_back(self, app):
        _set("SESSION_TIMEOUT_MINUTES", 0)
        assert session_timeout_minutes() == FALLBACK_SESSION_TIMEOUT_MINUTES

    def test_an_absurdly_short_value_is_floored(self, app):
        # A driver mistyping "1" for what they meant as "10" should not
        # get logged out mid-keystroke -- a floor protects against a
        # configuration mistake locking everyone out within a minute.
        _set("SESSION_TIMEOUT_MINUTES", 1)
        assert session_timeout_minutes() >= 5


class TestSessionWarningMinutes:
    def test_the_parameter_is_read(self, app):
        _set("SESSION_WARNING_MINUTES", 3)
        _set("SESSION_TIMEOUT_MINUTES", 30)
        assert session_warning_minutes() == 3

    def test_a_missing_parameter_falls_back(self, app):
        assert session_warning_minutes() == FALLBACK_SESSION_WARNING_MINUTES

    def test_never_returns_more_than_the_timeout_itself(self, app):
        # A warning window longer than the session itself would mean
        # the countdown modal appears before the person has even
        # finished logging in -- nonsensical, and worth protecting
        # against explicitly rather than trusting every admin edit.
        _set("SESSION_TIMEOUT_MINUTES", 5)
        _set("SESSION_WARNING_MINUTES", 20)
        assert session_warning_minutes() < session_timeout_minutes()


class TestMeCarriesBothSettings:
    def test_me_reports_the_live_configured_values(self, client, app):
        _set("SESSION_TIMEOUT_MINUTES", 45)
        _set("SESSION_WARNING_MINUTES", 5)
        user = User(username="sessuser", email="sessuser@e.com",
                   password_hash=hash_password("secret123"), is_active=True)
        db.session.add(user)
        db.session.commit()

        r = client.post("/api/v1/auth/token",
                        json={"username": "sessuser", "password": "secret123"})
        token = json.loads(r.get_data(as_text=True))["access_token"]

        r2 = client.get("/api/v1/me",
                        headers={"Authorization": f"Bearer {token}"})
        body = json.loads(r2.get_data(as_text=True))
        assert body["session_timeout_minutes"] == 45
        assert body["session_warning_minutes"] == 5
