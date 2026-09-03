""""Remember me" — how long the CLIENT keeps the refresh token.

Worth being precise about what this does and does not do. The token
stays valid server-side for its full life either way; the checkbox
controls only whether the browser or device holds on to it. So
unchecking protects the NEXT PERSON at this machine. It is not a
defence against someone who already captured the token, and the UI
should not imply otherwise.

Default TRUE, matching what the system has always done. Flipping the
default would silently start signing everyone out daily -- a change
nobody asked for.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.api.auth import REFRESH_COOKIE
from app.modules.user_management.models import User


@pytest.fixture()
def user(app):
    u = User(username="juan", email="juan@example.com",
             password_hash=hash_password("secret123"), is_active=True,
             mobile_access=True)
    db.session.add(u)
    db.session.commit()
    return u


def _login(c, **extra):
    # Named `c`, not `client`: the request body itself carries a
    # "client" key for the native flag, and the two collide.
    body = {"username": "juan", "password": "secret123"}
    body.update(extra)
    return c.post("/api/v1/auth/token", json=body)


def _cookie(resp):
    for header in resp.headers.getlist("Set-Cookie"):
        if header.startswith(REFRESH_COOKIE):
            return header
    return None


def test_default_is_to_remember(app, client, user):
    """Today's behaviour, preserved. Anyone signing in without sending
    the new field must keep the persistent cookie they have always
    had."""
    header = _cookie(_login(client))
    assert header is not None
    assert "Expires=" in header


def test_remember_true_persists_the_cookie(app, client, user):
    header = _cookie(_login(client, remember=True))
    assert "Expires=" in header


def test_remember_false_makes_it_a_session_cookie(app, client, user):
    """No Expires means the browser discards it on close -- which is
    the whole point on a shared machine."""
    header = _cookie(_login(client, remember=False))
    assert header is not None
    assert "Expires=" not in header
    assert "Max-Age=" not in header


def test_the_session_cookie_still_works_right_now(app, client, user):
    """Unchecking must not break the CURRENT session. It shortens how
    long the credential is kept, not whether it functions."""
    _login(client, remember=False)
    assert client.post("/api/v1/auth/refresh").status_code == 200


def test_native_login_is_told_the_choice(app, client, user):
    """The device decides whether to write the token to the Keystore.
    Told explicitly rather than left to infer from a field it did not
    send."""
    body = json.loads(_login(client, client="native",
                             remember=False).get_data(as_text=True))
    assert body["remember"] is False
    # The token still comes back -- the session works, it is simply not
    # persisted across app restarts.
    assert body["refresh_token"]


def test_native_login_defaults_to_remembering(app, client, user):
    body = json.loads(_login(client, client="native")
                      .get_data(as_text=True))
    assert body["remember"] is True


def test_a_string_false_is_understood(app, client, user):
    """Form posts and some clients send strings. "false" must not be
    read as truthy, which is the classic version of this bug."""
    header = _cookie(_login(client, remember="false"))
    assert "Expires=" not in header
