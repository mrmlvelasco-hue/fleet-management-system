"""An expired session must not dead-end the person on a raw error page.

Reported: clicking Cancel on a Maintenance Order after leaving the page
open produced Flask-WTF's bare white "Bad Request / The CSRF tokens do
not match" page -- no navigation, no explanation, no way back.

A CSRF failure in this app almost always means the SESSION expired
(30 minutes), so it is handled as that: page requests are redirected to
the login page with an explanation, and background requests get a
machine-readable 401 the front-end can act on.
"""
import pytest

from app import create_app


@pytest.fixture()
def csrf_app():
    """CSRF is disabled in the normal testing config so tests can post
    freely -- it has to be switched back ON to exercise this at all."""
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    return app


CANCEL_URL = "/transactions/maintenance-orders/22/cancel"


def test_page_post_redirects_instead_of_showing_the_raw_error(csrf_app):
    client = csrf_app.test_client()
    r = client.post(CANCEL_URL, data={"csrf_token": "stale-token"})
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")


def test_raw_csrf_error_page_is_never_shown(csrf_app):
    client = csrf_app.test_client()
    r = client.post(CANCEL_URL, data={"csrf_token": "stale-token"},
                    follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "CSRF tokens do not match" not in body
    assert "Bad Request" not in body


def test_the_person_is_told_why(csrf_app):
    client = csrf_app.test_client()
    r = client.post(CANCEL_URL, data={"csrf_token": "stale-token"},
                    follow_redirects=True)
    assert "session expired" in r.get_data(as_text=True).lower()


def test_ajax_gets_json_not_a_redirect(csrf_app):
    """A background request must not have an HTML login page injected
    into it -- the front-end needs something it can branch on."""
    client = csrf_app.test_client()
    r = client.post(CANCEL_URL, data={"csrf_token": "stale-token"},
                    headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 401
    assert r.is_json
    payload = r.get_json()
    assert payload["error_type"] == "SESSION_EXPIRED"
    assert payload["login_url"]


def test_csrf_token_lifetime_is_bound_to_the_session(csrf_app):
    """Two independent clocks would let a token lapse while the session
    is still alive, making the 'session expired' message untrue."""
    assert csrf_app.config.get("WTF_CSRF_TIME_LIMIT") is None


def test_csrf_protection_is_still_enabled_in_production_config():
    """Guard against 'fixing' this by simply switching CSRF off."""
    app = create_app("production")
    assert app.config["WTF_CSRF_ENABLED"] is True
