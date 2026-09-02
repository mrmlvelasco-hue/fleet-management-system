"""Refresh tokens for native clients.

The web app keeps its refresh token in an httpOnly cookie, which is the
right answer for a browser: a token JavaScript can read is a token an
injected script can steal.

It is the wrong answer inside a Capacitor WebView. The app's own origin
is http://localhost while the API is on another host, so the cookie is
cross-site; with SameSite=Lax the browser simply will not send it. A
driver who has been offline for a few hours reopens the app, the access
token has expired, the refresh silently fails, and they are logged out
holding unsent checklist drafts -- exactly the failure the offline work
exists to prevent.

So a native client asks for the refresh token in the response BODY and
holds it in Keychain/Keystore via Capacitor Preferences. The httpOnly
cookie defends against browser CSRF and script access; a native app has
no attacker-controlled page from which to mount either, so nothing is
being given up that was protecting it.

The web path is deliberately untouched. Both flows are asserted here
side by side, because the risk in a change like this is not that the new
path fails loudly -- it is that the old one quietly starts behaving like
the new one.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.modules.api.auth import REFRESH_COOKIE
from app.modules.user_management.models import User


@pytest.fixture()
def user(db):
    # mobile_access is required for the native branch as of Phase 1a.
    # This file is entirely ABOUT the native branch, so the fixture must
    # grant it; the flag's own behaviour is covered separately in
    # test_api_mobile_access_gate.py. Set explicitly rather than
    # defaulted so it stays obvious that a native login now depends on
    # it.
    u = User(username="driver1", email="d1@e.com",
             password_hash=hash_password("secret123"), is_active=True,
             mobile_access=True)
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, native=False):
    body = {"username": "driver1", "password": "secret123"}
    if native:
        body["client"] = "native"
    r = client.post("/api/v1/auth/token", json=body)
    return r, json.loads(r.get_data(as_text=True))


# ------------------------------------------------------------- web path

def test_web_login_still_sets_an_httponly_cookie(db, client, user):
    r, body = _login(client)
    assert r.status_code == 200
    assert any(REFRESH_COOKIE in h for h in r.headers.getlist("Set-Cookie"))
    # The long-lived credential must NOT be readable by script in the
    # browser flow. This is the assertion that stops the native change
    # leaking into the web path.
    assert "refresh_token" not in body


def test_web_refresh_still_works_from_the_cookie_alone(db, client, user):
    _login(client)
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    assert json.loads(r.get_data(as_text=True))["access_token"]


# ---------------------------------------------------------- native path

def test_native_login_returns_the_refresh_token_in_the_body(db, client, user):
    _r, body = _login(client, native=True)
    assert body["refresh_token"]
    assert body["access_token"]


def test_native_refresh_accepts_the_token_in_the_body(db, client, user):
    _r, body = _login(client, native=True)
    r = client.post("/api/v1/auth/refresh",
                    json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 200
    assert json.loads(r.get_data(as_text=True))["access_token"]


def test_native_refresh_issues_a_DIFFERENT_token(db, client, user):
    """Every issued token must be distinguishable.

    Without a jti, two tokens minted in the same second for the same
    user are byte-identical -- iat and exp are second-resolution -- so
    the client cannot tell a rotated token from its own one echoed back.

    Note what this does NOT assert: the old token is still accepted
    afterwards, because these are stateless JWTs with no server-side
    store. See the limitation recorded on issue_refresh_token.
    """
    _r, body = _login(client, native=True)
    r = client.post("/api/v1/auth/refresh",
                    json={"refresh_token": body["refresh_token"]})
    rotated = json.loads(r.get_data(as_text=True))
    assert rotated["refresh_token"]
    assert rotated["refresh_token"] != body["refresh_token"]


def test_native_refresh_survives_with_no_cookie_at_all(db, client, user):
    """The actual WebView situation. A fresh client carries no cookie
    jar, which is precisely why the cookie flow cannot serve it."""
    _r, body = _login(client, native=True)
    fresh = client.application.test_client()
    r = fresh.post("/api/v1/auth/refresh",
                   json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 200


# ------------------------------------------------------------- refusals

def test_an_access_token_is_not_accepted_as_a_refresh_token(db, client, user):
    """The typ split is the whole reason a short access-token lifetime
    means anything. Replaying one here would mint long-lived credentials
    from a token held in memory."""
    _r, body = _login(client, native=True)
    r = client.post("/api/v1/auth/refresh",
                    json={"refresh_token": body["access_token"]})
    assert r.status_code == 401


def test_a_garbage_refresh_token_is_refused(db, client, user):
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": "nonsense"})
    assert r.status_code == 401


def test_a_disabled_account_cannot_refresh(db, client, user):
    """Re-checked on every refresh, not just at login. A device in a
    drawer must not keep minting credentials for a revoked account."""
    _r, body = _login(client, native=True)
    user.is_active = False
    from app.extensions import db as _db
    _db.session.commit()

    r = client.post("/api/v1/auth/refresh",
                    json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 401
