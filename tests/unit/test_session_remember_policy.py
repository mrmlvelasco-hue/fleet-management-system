"""Session remember policy -- System Administration parameter
SESSION_REMEMBER_POLICY.

Reported: logged in last night, opened the dashboard this morning and was
still signed in, although SESSION_TIMEOUT_MINUTES is 30. The idle timer
only runs while a tab is open; closing the browser kills it, and in the
morning the 14-day "Remember me" refresh cookie silently signed the user
back in.

  ALWAYS_TIMEOUT (A, default)  the refresh token lives exactly
      SESSION_TIMEOUT_MINUTES and is renewed on every refresh, so it is a
      server-enforced idle window: away longer than the timeout -> sign in
      again, browser closed or not. "Remember me" no longer extends the
      session (the login page turns it into "Remember my username").
  REMEMBER_EXTENDS (B)         previous behaviour: "Remember me" keeps the
      14-day refresh cookie; unticked -> browser-session cookie.

Unknown values fall back to A -- a typo must fail safe, not open up
14-day sessions.
"""
import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import jwt
import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.system_admin.models import SystemParameter
from app.modules.user_management.models import Role, User


@pytest.fixture()
def env(db, app):
    sync_permissions()
    user = User(username="sessioner", email="s@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [Role(name="Session Role")]
    db.session.add(user)
    db.session.commit()
    app.config["CORS_ORIGINS"] = ["http://localhost:5173"]
    return user


def _param(db, code, value, dtype="STRING"):
    row = SystemParameter.query.filter_by(code=code).first()
    if row is None:
        row = SystemParameter(code=code, value=value, data_type=dtype,
                              group_name="SECURITY")
        db.session.add(row)
    row.value = value
    db.session.commit()


def _login(client, remember=True):
    return client.post("/api/v1/auth/token",
                       json={"username": "sessioner", "password": "secret123",
                             "remember": remember})


def _refresh_cookie(resp):
    return next(h for h in resp.headers.getlist("Set-Cookie")
                if h.startswith("fms_refresh="))


def _cookie_expiry(cookie):
    for part in cookie.split(";"):
        part = part.strip()
        if part.lower().startswith("expires="):
            return parsedate_to_datetime(part.split("=", 1)[1])
    return None


def _token_lifetime(cookie):
    token = cookie.split(";", 1)[0].split("=", 1)[1]
    claims = jwt.decode(token, options={"verify_signature": False})
    return claims["exp"] - claims["iat"]


def _near(actual, expected, slack=timedelta(minutes=2)):
    return abs(actual - expected) <= slack


# ── A: always time out (default) ────────────────────────────────────────

def test_default_is_always_timeout_even_when_remember_is_ticked(db, client, env):
    r = _login(client, remember=True)
    assert r.status_code == 200
    cookie = _refresh_cookie(r)
    assert _token_lifetime(cookie) == 30 * 60
    exp = _cookie_expiry(cookie)
    assert exp is not None          # survives a browser restart within the window
    assert _near(exp, datetime.now(timezone.utc) + timedelta(minutes=30))


def test_always_timeout_follows_the_configured_timeout(db, client, env):
    _param(db, "SESSION_TIMEOUT_MINUTES", "60", "INTEGER")
    cookie = _refresh_cookie(_login(client))
    assert _token_lifetime(cookie) == 60 * 60


def test_refresh_renews_the_idle_window(db, client, env):
    """Each refresh (the app sends one while the user is active) issues a
    new token good for another full timeout -- a sliding window."""
    _login(client)
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 200, r.get_data(as_text=True)
    cookie = _refresh_cookie(r)
    assert _token_lifetime(cookie) == 30 * 60
    assert _near(_cookie_expiry(cookie),
                 datetime.now(timezone.utc) + timedelta(minutes=30))


def test_unknown_policy_value_fails_safe_to_always_timeout(db, client, env):
    _param(db, "SESSION_REMEMBER_POLICY", "FOREVER")
    cookie = _refresh_cookie(_login(client, remember=True))
    assert _token_lifetime(cookie) == 30 * 60


# ── B: remember extends ─────────────────────────────────────────────────

def test_remember_extends_keeps_the_14_day_cookie(db, client, env):
    _param(db, "SESSION_REMEMBER_POLICY", "REMEMBER_EXTENDS")
    cookie = _refresh_cookie(_login(client, remember=True))
    assert _token_lifetime(cookie) == 14 * 24 * 3600
    assert _near(_cookie_expiry(cookie),
                 datetime.now(timezone.utc) + timedelta(days=14))


def test_remember_extends_unticked_is_a_browser_session_cookie(db, client, env):
    _param(db, "SESSION_REMEMBER_POLICY", "REMEMBER_EXTENDS")
    cookie = _refresh_cookie(_login(client, remember=False))
    assert _cookie_expiry(cookie) is None


# ── the policy is visible to the app ────────────────────────────────────

def test_me_reports_the_policy(db, client, env):
    tok = json.loads(_login(client).get_data(as_text=True))["access_token"]
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.get_json()["session_remember_policy"] == "ALWAYS_TIMEOUT"


def test_login_page_can_read_the_policy_without_signing_in(db, client, env):
    _param(db, "SESSION_REMEMBER_POLICY", "REMEMBER_EXTENDS")
    r = client.get("/api/v1/auth/session-policy")
    assert r.status_code == 200
    assert r.get_json() == {"session_remember_policy": "REMEMBER_EXTENDS"}


def test_the_parameter_is_seeded_for_system_administration(db, env):
    from app.cli import _seed_system_parameters
    _seed_system_parameters()
    row = SystemParameter.query.filter_by(code="SESSION_REMEMBER_POLICY").first()
    assert row is not None
    assert row.value == "ALWAYS_TIMEOUT"
    assert row.group_name == "SECURITY"
