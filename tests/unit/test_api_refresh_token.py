"""Tests for the refresh-token flow.

Why this exists: the React app holds its access token in memory only, so
a new browser tab or a page refresh has no credential and lands on the
login screen. That was an acceptable trade when React was a prototype
opened occasionally. As the primary UI it breaks ordinary workflow --
ctrl-clicking a plate to open a vehicle in a background tab logs the
user out.

The fix is a refresh token in an httpOnly cookie. The browser sends it
automatically, JavaScript cannot read it, so a new tab can obtain a
fresh access token without the long-lived credential ever being
reachable by an injected script. localStorage would solve the same
problem by making the credential readable, which is the wrong trade for
a system holding fleet and financial records.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role
from app.core.security.registry import sync_permissions


@pytest.fixture()
def auth_env(db, app):
    sync_permissions()
    db.session.commit()
    user = User(username="refresher", email="r@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [Role(name="Refresh Role")]
    db.session.add(user)
    db.session.commit()
    app.config["CORS_ORIGINS"] = ["http://localhost:5173"]
    return user


def _login(client):
    return client.post("/api/v1/auth/token",
                       json={"username": "refresher", "password": "secret123"})


def _body(resp):
    return json.loads(resp.get_data(as_text=True))


# ── Cookie issuance ─────────────────────────────────────────────────────────

def test_login_sets_a_refresh_cookie(db, client, auth_env):
    r = _login(client)
    assert r.status_code == 200
    assert any("fms_refresh" in h for h in r.headers.getlist("Set-Cookie"))


def test_refresh_cookie_is_httponly_and_samesite(db, client, auth_env):
    """HttpOnly is the entire point: a token JavaScript can read is a
    token an injected script can steal, which is what holding the access
    token in memory was avoiding in the first place."""
    r = _login(client)
    cookie = next(h for h in r.headers.getlist("Set-Cookie")
                  if "fms_refresh" in h)
    assert "HttpOnly" in cookie
    assert "SameSite" in cookie
    # Scoped to the refresh route, so it is not attached to every API
    # call -- an ordinary request has no business carrying it.
    assert "Path=/api/v1/auth" in cookie


def test_access_token_is_not_in_the_cookie(db, client, auth_env):
    """Only the refresh token is a cookie. The access token stays in the
    response body and in memory, so it is never sent automatically and
    cannot be replayed by a cross-site request."""
    r = _login(client)
    cookie = next(h for h in r.headers.getlist("Set-Cookie")
                  if "fms_refresh" in h)
    assert _body(r)["access_token"] not in cookie


# ── Refreshing ──────────────────────────────────────────────────────────────

def test_refresh_returns_a_new_access_token(db, client, auth_env):
    _login(client)
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    assert _body(r)["access_token"]


def test_refresh_without_a_cookie_is_unauthorised(db, client, auth_env):
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 401


def test_refreshed_token_works_on_a_real_endpoint(db, client, auth_env):
    """The point of the whole flow: a new tab gets a usable credential."""
    _login(client)
    token = _body(client.post("/api/v1/auth/refresh"))["access_token"]
    r = client.get("/api/v1/me",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert _body(r)["username"] == "refresher"


def test_an_access_token_cannot_be_used_to_refresh(db, client, auth_env):
    """The two token types are distinct. If an access token could be
    replayed at the refresh endpoint, its short lifetime -- the reason
    it is safe to hold in memory -- would mean nothing.
    """
    access = _body(_login(client))["access_token"]
    client.delete_cookie("fms_refresh", path="/api/v1/auth")
    r = client.post("/api/v1/auth/refresh",
                    headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 401


def test_a_refresh_token_cannot_be_used_as_a_bearer(db, client, auth_env):
    """And the reverse: the long-lived token must not open endpoints."""
    _login(client)
    cookie = client.get_cookie("fms_refresh", path="/api/v1/auth")
    r = client.get("/api/v1/me",
                   headers={"Authorization": f"Bearer {cookie.value}"})
    assert r.status_code == 401


def test_refresh_is_refused_for_a_deactivated_account(db, client, auth_env):
    """Disabling an account must end access promptly. A refresh token
    that keeps minting credentials for a disabled user would outlive the
    decision to revoke them.
    """
    user = auth_env
    _login(client)
    user.is_active = False
    db.session.commit()
    assert client.post("/api/v1/auth/refresh").status_code == 401


# ── Logout ──────────────────────────────────────────────────────────────────

def test_logout_clears_the_refresh_cookie(db, client, auth_env):
    _login(client)
    r = client.post("/api/v1/auth/logout")
    assert r.status_code == 200
    cookie = next(h for h in r.headers.getlist("Set-Cookie")
                  if "fms_refresh" in h)
    # Expired in the past, which is how a cookie is actually removed.
    assert "Expires=Thu, 01 Jan 1970" in cookie or "Max-Age=0" in cookie


def test_refresh_after_logout_is_unauthorised(db, client, auth_env):
    _login(client)
    client.post("/api/v1/auth/logout")
    client.delete_cookie("fms_refresh", path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 401


# ── CORS ────────────────────────────────────────────────────────────────────

def test_refresh_route_allows_credentials_for_a_known_origin(
        db, client, auth_env):
    """The refresh route is the ONLY credentialed one. The browser will
    not send the cookie cross-origin unless the server says it may."""
    _login(client)
    r = client.post("/api/v1/auth/refresh",
                    headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("Access-Control-Allow-Credentials") == "true"
    assert r.headers.get("Access-Control-Allow-Origin") == \
        "http://localhost:5173"


def test_credentials_are_not_allowed_for_an_unknown_origin(
        db, client, auth_env):
    _login(client)
    r = client.post("/api/v1/auth/refresh",
                    headers={"Origin": "http://evil.example.com"})
    assert r.headers.get("Access-Control-Allow-Credentials") is None
    assert r.headers.get("Access-Control-Allow-Origin") is None


def test_ordinary_endpoints_stay_non_credentialed(db, client, auth_env):
    """Only /auth/refresh may echo Allow-Credentials. Everything else
    stays cookie-free, so CSRF against the API remains closed off by
    construction."""
    token = _body(_login(client))["access_token"]
    r = client.get("/api/v1/me",
                   headers={"Authorization": f"Bearer {token}",
                            "Origin": "http://localhost:5173"})
    assert r.headers.get("Access-Control-Allow-Credentials") is None
