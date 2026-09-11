"""The refresh cookie's Secure attribute is configured explicitly.

Why this exists
---------------
`set_refresh_cookie()` derived Secure from `not DEBUG`. That coupled a
security attribute of the authentication cookie to the flag that also
turns on the interactive debugger, and it made the React "open in a new
tab" flow depend on how Flask happened to be launched:

  * PyCharm running wsgi.py from the wrong working directory never
    loaded .env, so FLASK_ENV was unset.
  * Over plain http on a LAN address the resulting Secure cookie was
    silently DROPPED by Chrome ("blocked because it had the Secure
    attribute but was not received over a secure connection").
  * Login still appeared to work, because the ACCESS token comes back in
    the JSON body. Only the refresh cookie was lost, so the failure
    surfaced one step later, as a 401 from /auth/refresh in a new tab --
    a long way from its cause.

So the attribute gets its own switch, defaulting to secure, and the two
concerns stop moving together.
"""
import json

import pytest

from app.config import BaseConfig, ProductionConfig, _refresh_cookie_secure
from app.modules.user_management.models import Role, User
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions


# ── The config knob itself ──────────────────────────────────────────


class TestRefreshCookieSecureDefault:
    def test_defaults_to_secure_when_unset(self):
        """An operator who sets nothing must get the SAFE value.

        This is the whole reason the default is not derived from DEBUG:
        forgetting to set a variable should never downgrade a cookie.
        """
        assert _refresh_cookie_secure({}) is True

    def test_base_config_default_is_secure(self):
        assert BaseConfig.REFRESH_COOKIE_SECURE is True

    def test_production_config_is_secure(self):
        assert ProductionConfig.REFRESH_COOKIE_SECURE is True

    @pytest.mark.parametrize("value", ["0", "false", "False", "no", "NO", "off"])
    def test_recognised_falsey_values_switch_it_off(self, value):
        """For LAN-over-http testing, which is the only legitimate case."""
        assert _refresh_cookie_secure({"REFRESH_COOKIE_SECURE": value}) is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on"])
    def test_truthy_values_keep_it_on(self, value):
        assert _refresh_cookie_secure({"REFRESH_COOKIE_SECURE": value}) is True

    def test_unrecognised_value_stays_secure(self):
        """A typo must not silently disable it.

        "flase" is not "false". Treating anything non-empty as truthy
        would be the usual shortcut, but here the failure mode of
        guessing wrong is an insecure cookie in production, so anything
        not explicitly recognised as off stays on.
        """
        assert _refresh_cookie_secure({"REFRESH_COOKIE_SECURE": "flase"}) is True

    def test_empty_string_stays_secure(self):
        """Set-but-empty is how a shell exports a variable it did not
        actually fill in; it must not read as "off"."""
        assert _refresh_cookie_secure({"REFRESH_COOKIE_SECURE": ""}) is True

    def test_is_independent_of_debug(self):
        """The regression this file exists to prevent.

        DEBUG must not be able to move this value in either direction.
        """
        assert _refresh_cookie_secure({"DEBUG": "1"}) is True
        assert _refresh_cookie_secure({"FLASK_ENV": "development"}) is True


# ── The cookie actually sent on login ────────────────────────────────


@pytest.fixture()
def auth_env(db, app):
    sync_permissions()
    db.session.commit()
    user = User(username="cookieuser", email="c@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [Role(name="Cookie Role")]
    db.session.add(user)
    db.session.commit()
    return user


def _login(client):
    return client.post("/api/v1/auth/token",
                       json={"username": "cookieuser", "password": "secret123"})


def _set_cookie_header(resp):
    """The raw Set-Cookie for the refresh cookie.

    Read as a RAW HEADER rather than through the cookie jar because the
    attributes are the thing under test -- a test client that has
    already parsed and stored the cookie tells us nothing about what the
    browser was asked to do with it.
    """
    for header, value in resp.headers:
        if header == "Set-Cookie" and value.startswith("fms_refresh="):
            return value
    return None


class TestRefreshCookieAttributes:
    def test_secure_attribute_present_when_configured_on(self, client, app, auth_env):
        app.config["REFRESH_COOKIE_SECURE"] = True
        header = _set_cookie_header(_login(client))
        assert header is not None
        assert "Secure" in header

    def test_secure_attribute_absent_when_configured_off(self, client, app, auth_env):
        """The LAN-over-http case from the bug report.

        Without this the browser drops the cookie on arrival and the
        next tab gets a 401 it cannot explain.
        """
        app.config["REFRESH_COOKIE_SECURE"] = False
        header = _set_cookie_header(_login(client))
        assert header is not None
        assert "Secure" not in header

    def test_debug_true_does_not_disable_secure(self, client, app, auth_env):
        """Mutation guard.

        If set_refresh_cookie ever goes back to reading DEBUG, this
        fails: DEBUG is on, the knob says secure, and secure must win.
        """
        app.config["DEBUG"] = True
        app.config["REFRESH_COOKIE_SECURE"] = True
        header = _set_cookie_header(_login(client))
        assert "Secure" in header

    def test_debug_false_does_not_force_secure(self, client, app, auth_env):
        """The other direction of the same mutation."""
        app.config["DEBUG"] = False
        app.config["REFRESH_COOKIE_SECURE"] = False
        header = _set_cookie_header(_login(client))
        assert "Secure" not in header

    def test_secure_when_the_setting_is_absent_entirely(
            self, client, app, auth_env):
        """The fallback inside set_refresh_cookie must be SECURE.

        Every other test in this class sets REFRESH_COOKIE_SECURE
        explicitly, so none of them exercised the default in
        `config.get(..., True)`. Mutation testing caught that: flipping
        that default to False left the whole file green, which would
        have shipped an insecure cookie to any deployment whose config
        predates this key -- exactly the upgrade path a real one takes.
        """
        app.config.pop("REFRESH_COOKIE_SECURE", None)
        header = _set_cookie_header(_login(client))
        assert header is not None
        assert "Secure" in header

    def test_httponly_and_samesite_unchanged(self, client, app, auth_env):
        """The other attributes are NOT part of this change.

        HttpOnly is what keeps the token away from injected script, and
        SameSite=Lax is what lets it survive the new-tab navigation this
        whole mechanism exists for. Relaxing Secure must not quietly
        take either with it.
        """
        app.config["REFRESH_COOKIE_SECURE"] = False
        header = _set_cookie_header(_login(client))
        assert "HttpOnly" in header
        assert "SameSite=Lax" in header

    def test_path_is_still_scoped_to_auth(self, client, app, auth_env):
        """Scoping is why an ordinary API call never carries the cookie."""
        app.config["REFRESH_COOKIE_SECURE"] = False
        header = _set_cookie_header(_login(client))
        assert "Path=/api/v1/auth" in header


class TestRefreshStillWorksEndToEnd:
    def test_login_then_refresh_succeeds_with_insecure_cookie(
            self, client, app, auth_env):
        """The actual user-visible outcome: a new tab stays signed in.

        Asserting the attribute alone would not catch a cookie that is
        sent but then rejected on the way back, which is exactly the
        shape of the original bug.
        """
        app.config["REFRESH_COOKIE_SECURE"] = False
        assert _login(client).status_code == 200
        resp = client.post("/api/v1/auth/refresh")
        assert resp.status_code == 200
        assert json.loads(resp.get_data(as_text=True)).get("access_token")

    def test_refresh_without_login_is_still_unauthorized(self, client, app):
        """Relaxing Secure must not turn refresh into a free token.

        This is the 401 from the bug report -- correct behaviour when
        there genuinely is no cookie, and it has to stay correct.
        """
        app.config["REFRESH_COOKIE_SECURE"] = False
        assert client.post("/api/v1/auth/refresh").status_code == 401
