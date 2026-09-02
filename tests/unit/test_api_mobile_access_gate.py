"""The mobile channel gate.

`users.mobile_access` decides whether an account's PHONE may hold a
credential. It is enforced in exactly two places -- the native branch of
/auth/token and of /auth/refresh -- and nowhere else.

Both are needed, and the second is the one that makes the flag mean
anything. Gating only the login leaves a valid 14-day refresh token
sitting on a device that has just been revoked; it would keep minting
access tokens for a fortnight, which is precisely the lost-phone case
the switch exists for.

Every test here pairs the native assertion with the web one. The risk in
adding a gate to a shared endpoint is not that the new path fails loudly
-- it is that the old one quietly starts failing too, locking every
browser user out of an app they were never meant to be gated from.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.modules.api.auth import REFRESH_COOKIE
from app.modules.user_management.models import User


def _make_user(db, username, mobile_access):
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password("secret123"), is_active=True,
             mobile_access=mobile_access)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def phone_user(db):
    """Granted mobile access."""
    return _make_user(db, "fielddriver", True)


@pytest.fixture()
def desk_user(db):
    """An ordinary web user. Default state -- no mobile access."""
    return _make_user(db, "deskclerk", False)


def _login(client, username, native=False, password="secret123"):
    body = {"username": username, "password": password}
    if native:
        body["client"] = "native"
    r = client.post("/api/v1/auth/token", json=body)
    return r, json.loads(r.get_data(as_text=True))


# ------------------------------------------------------- /auth/token

def test_native_login_refused_without_mobile_access(db, client, desk_user):
    r, body = _login(client, "deskclerk", native=True)
    assert r.status_code == 403
    assert body["error"] == "mobile_access_denied"
    # No credential of any kind may come back with a refusal.
    assert "access_token" not in body
    assert "refresh_token" not in body


def test_native_login_allowed_with_mobile_access(db, client, phone_user):
    r, body = _login(client, "fielddriver", native=True)
    assert r.status_code == 200
    assert body["access_token"]
    assert body["refresh_token"]


def test_web_login_unaffected_by_the_flag(db, client, desk_user):
    """The whole point of gating the NATIVE branch only. A user with no
    mobile access is an ordinary user of the web app, not a disabled
    account."""
    r, body = _login(client, "deskclerk")
    assert r.status_code == 200
    assert body["access_token"]
    assert any(REFRESH_COOKIE in h for h in r.headers.getlist("Set-Cookie"))


def test_bad_password_still_returns_401_not_403(db, client, phone_user):
    """Ordering matters. If the flag were checked BEFORE the password,
    the endpoint would answer differently for a real username with the
    wrong password than for one without mobile access -- turning it into
    an oracle for which accounts exist and which are provisioned for the
    field app, without needing any password at all."""
    r, _body = _login(client, "fielddriver", native=True, password="wrong")
    assert r.status_code == 401


def test_no_mobile_access_and_bad_password_returns_401(db, client, desk_user):
    r, _body = _login(client, "deskclerk", native=True, password="wrong")
    assert r.status_code == 401


# ----------------------------------------------------- /auth/refresh

def test_native_refresh_refused_after_access_is_revoked(db, client,
                                                        phone_user):
    """The kill switch. Revoking mid-session must stop the device
    renewing, or the flag buys nothing on the lost phone it exists
    for."""
    _r, body = _login(client, "fielddriver", native=True)
    refresh = body["refresh_token"]

    phone_user.mobile_access = False
    db.session.commit()

    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401


def test_native_refresh_still_works_while_access_stands(db, client,
                                                        phone_user):
    _r, body = _login(client, "fielddriver", native=True)
    r = client.post("/api/v1/auth/refresh",
                    json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 200
    assert json.loads(r.get_data(as_text=True))["access_token"]


def test_cookie_refresh_unaffected_by_the_flag(db, client, desk_user):
    """A browser session belonging to someone with no mobile access must
    keep refreshing normally."""
    _login(client, "deskclerk")
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 200
