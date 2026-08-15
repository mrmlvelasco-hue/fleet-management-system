"""The sidebar appearance picker, end to end through the routes.

Service-level tests alone would prove the resolution rules and nothing
about whether a page actually renders the chosen skin or whether the
endpoint that saves it is reachable and protected -- so the route and
the rendered shell are exercised here directly.
"""
import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def admin(app, db):
    from app.modules.user_management.models import User
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    return User.query.filter_by(username="admin").first()


def _login(client):
    return client.post("/login", data={"username": "admin",
                                       "password": "Testpass123!"},
                       follow_redirects=True)


# ── The shell renders the resolved skin server-side ─────────────────

def test_shell_carries_the_skin_attribute(app, client, db, admin):
    """Rendered by the SERVER, not applied by JS after paint. A
    client-side swap would repaint the sidebar on every single page
    load -- a visible flash of the wrong colour each time."""
    _login(client)
    html = client.get("/dashboard").get_data(as_text=True)
    assert 'data-sidebar-skin="classic"' in html


def test_shell_reflects_a_users_chosen_skin(app, client, db, admin):
    from app.core.appearance.skin_service import SidebarSkinService
    SidebarSkinService().set_for_user(admin, "soft-dark")
    db.session.commit()
    _login(client)
    html = client.get("/dashboard").get_data(as_text=True)
    assert 'data-sidebar-skin="soft-dark"' in html


def test_login_page_renders_without_a_user(app, client, db):
    """The shell is rendered before authentication too; resolving a
    skin for an anonymous visitor must not error."""
    assert client.get("/login").status_code == 200


# ── The picker is present in the chrome ─────────────────────────────

def test_picker_offers_every_skin(app, client, db, admin):
    _login(client)
    html = client.get("/dashboard").get_data(as_text=True)
    for code in ("classic", "soft-dark", "soft-light", "soft-crimson"):
        assert f'data-skin-code="{code}"' in html


def _pressed_states(html):
    """{skin code: aria-pressed value} for every swatch on the page.

    Parsed rather than string-matched on adjacent attributes, which
    would break the moment the template's attribute order changed --
    a test failing for that reason tells you nothing about the app.
    """
    import re
    states = {}
    for tag in re.findall(r"<button[^>]*data-skin-code[^>]*>", html):
        code = re.search(r'data-skin-code="([^"]+)"', tag).group(1)
        pressed = re.search(r'aria-pressed="([^"]+)"', tag)
        states.setdefault(code, pressed.group(1) if pressed else None)
    return states


def test_picker_marks_the_current_choice_as_selected(app, client, db, admin):
    from app.core.appearance.skin_service import SidebarSkinService
    SidebarSkinService().set_for_user(admin, "soft-light")
    db.session.commit()
    _login(client)
    states = _pressed_states(client.get("/dashboard").get_data(as_text=True))
    assert states["soft-light"] == "true"
    # and only that one
    assert all(v == "false" for k, v in states.items() if k != "soft-light")


# ── Saving a choice ─────────────────────────────────────────────────

def test_saving_a_skin_requires_login(app, client, db, admin):
    resp = client.post("/preferences/sidebar-skin", json={"skin": "soft-dark"})
    assert resp.status_code in (302, 401)


def test_saving_a_skin_persists_it(app, client, db, admin):
    from app.modules.user_management.models import User
    _login(client)
    resp = client.post("/preferences/sidebar-skin",
                       json={"skin": "soft-dark"})
    assert resp.status_code == 200
    assert resp.get_json()["skin"] == "soft-dark"
    db.session.expire_all()
    assert User.query.filter_by(username="admin").first().sidebar_skin \
        == "soft-dark"


def test_saving_an_invalid_skin_is_rejected_cleanly(app, client, db, admin):
    """A 400 with a message, not a 500 -- this endpoint is reachable
    from any browser session."""
    _login(client)
    resp = client.post("/preferences/sidebar-skin",
                       json={"skin": "../../etc/passwd"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()
    db.session.expire_all()
    from app.modules.user_management.models import User
    assert User.query.filter_by(username="admin").first().sidebar_skin is None


def test_saved_skin_survives_into_the_next_request(app, client, db, admin):
    """The whole point of storing this server-side rather than in a
    cookie: the choice follows the account, so the very next page load
    -- and any other device -- already reflects it."""
    _login(client)
    client.post("/preferences/sidebar-skin", json={"skin": "soft-crimson"})
    html = client.get("/dashboard").get_data(as_text=True)
    assert 'data-sidebar-skin="soft-crimson"' in html


def test_skin_choice_is_independent_of_dark_mode(app, client, db, admin):
    """Skin and dark mode are separate axes: choosing a light skin must
    not clear a dark-mode preference or vice versa."""
    _login(client)
    client.set_cookie("fms-theme", "dark")
    client.post("/preferences/sidebar-skin", json={"skin": "soft-light"})
    html = client.get("/dashboard").get_data(as_text=True)
    assert 'data-sidebar-skin="soft-light"' in html


# ── The stylesheet backing all this ─────────────────────────────────

def test_skin_stylesheet_is_served(app, client, db, admin):
    _login(client)
    assert client.get("/static/css/sidebar-skins.css").status_code == 200


def test_every_skin_has_css_rules_including_a_dark_mode_variant(app):
    """Each skin must define both its own surface AND how it behaves
    once dark mode is on -- the agreed rule is that a light skin maps
    to a dark surface in dark mode rather than leaving a bright strip
    against a dark app."""
    from pathlib import Path
    css = Path(app.root_path, "static", "css", "sidebar-skins.css").read_text()
    for code in ("soft-dark", "soft-light", "soft-crimson"):
        assert f'[data-sidebar-skin="{code}"]' in css, f"no rules for {code}"
    for code in ("soft-dark", "soft-light", "soft-crimson"):
        # COMPOUND, not descendant. data-bs-theme and data-sidebar-skin
        # both live on <html>, so a descendant selector (with a space)
        # can never match -- it asks for a skinned DESCENDANT of a dark
        # element. Written that way first, the entire dark-mode section
        # silently did nothing while this test still passed, because it
        # only grepped for the selector text. Asserting the absence of
        # the broken form is the part that actually has teeth.
        assert (f'[data-bs-theme="dark"][data-sidebar-skin="{code}"]'
                in css), f"no dark-mode variant for {code}"
        assert (f'[data-bs-theme="dark"] [data-sidebar-skin="{code}"]'
                not in css), (
            f"{code}'s dark-mode rule uses a descendant selector, which "
            f"cannot match when both attributes are on <html>")


def test_mobile_drawer_drops_the_floating_card_treatment(app):
    """Neumorphism is designed for a card with air around it. The mobile
    sidebar is flush to the screen edge, where the outer shadow renders
    off-screen and the rounded left corners read as a glitch."""
    from pathlib import Path
    css = Path(app.root_path, "static", "css", "sidebar-skins.css").read_text()
    assert "@media (max-width: 860px)" in css


def test_touch_targets_meet_the_minimum_on_mobile(app):
    """44px is the documented minimum on both iOS and Android; the nav
    rows were under it in every skin before this."""
    from pathlib import Path
    css = Path(app.root_path, "static", "css", "sidebar-skins.css").read_text()
    assert "min-height: 44px" in css


def test_bottom_sheet_sizes_to_its_content(app):
    """Bootstrap's offcanvas-bottom defaults to 30vh, which on a 390x844
    phone rendered the sheet at 253px -- only two of the four skins were
    reachable and the rest sat below the fold in an internal scroll.
    The height is driven by a Bootstrap custom property, so the property
    is what has to be overridden; setting `height` alone lost the
    cascade in a real browser.
    """
    from pathlib import Path
    css = Path(app.root_path, "static", "css", "sidebar-skins.css").read_text()
    assert "--bs-offcanvas-height: auto" in css


def test_mobile_sheet_is_a_direct_child_of_body(app):
    """Bootstrap injects the offcanvas backdrop into the offcanvas
    element's PARENT node rather than into <body>. Nested inside the
    app wrapper, the backdrop was being created in that same wrapper --
    so keep the sheet at body level, outside the shell markup."""
    from pathlib import Path
    base = Path(app.root_path, "templates", "layout", "base.html").read_text()
    sheet_at = base.index('id="skinSheet"')
    wrapper_close = base.index('{% endif %}', base.index('auth_content'))
    assert sheet_at > wrapper_close, (
        "skinSheet must sit outside the authenticated/anonymous shell "
        "branches, as a direct child of <body>")


def test_main_column_can_shrink_below_its_content(app):
    """The column beside the sidebar is a flex item, and flex items
    default to min-width:auto -- so a wide table widened the whole
    column, and on a phone the browser widened the LAYOUT VIEWPORT to
    match (measured 929px on Trip Tickets against a 390px device).
    Everything then rendered zoomed out and fixed overlays landed
    off-screen. theme.css had `.fms-main { min-width: 0 }` for this,
    but base.html builds that column from Bootstrap utilities and never
    applies .fms-main, so the rule never reached the element.
    """
    from pathlib import Path
    css = Path(app.root_path, "static", "css", "theme.css").read_text()
    assert "#fms-wrapper > .flex-grow-1" in css
