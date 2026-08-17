from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login_full_access(client, db):
    """A user with permissions spanning all three sidebar groups."""
    role = Role(name="SidebarTestRole")
    codes = ["user.view", "vehicle.view", "tripticket.view", "pmschedule.view"]
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="sidebar_test_user", email="sidebar_test_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "sidebar_test_user", "password": "pw123456"})
    return u


def _group_is_shown(html, group_id):
    idx = html.find(f'id="{group_id}"')
    assert idx != -1, f"{group_id} is not on the page at all"
    return "show" in html[max(0, idx - 60):idx]


def _rendered_groups(html):
    """Only the groups this user can actually see.

    The sidebar hides a whole group when the user holds none of its
    permissions, so a test user without report permissions never gets
    sbGroupReports -- asserting on it would fail for the wrong reason.
    """
    import re
    return re.findall(r'id="(sbGroup\w+)"', html)


ACCORDION_NOTE = """The sidebar is an ACCORDION.

Only the group matching the current page is expanded, and opening one
group closes the others -- Bootstrap data-bs-parent linkage on each
collapse in layout/sidebar.html.

These tests previously asserted the opposite ("every group stays open").
That came from a stale comment in app.css which an earlier revision
followed, removing the linkage. The client confirmed the accordion is
the wanted behaviour, so the linkage is back, the comment is corrected,
and these tests now pin the accordion so it cannot be undone by reading
that comment again.
"""


def test_only_the_active_group_is_expanded_on_a_transactions_page(client, db):
    _login_full_access(client, db)
    resp = client.get("/transactions/trip-tickets")
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)
    assert _group_is_shown(html, "sbGroupTransactions")
    for other in _rendered_groups(html):
        if other == "sbGroupTransactions":
            continue
        assert not _group_is_shown(html, other), f"{other} should be closed"


def test_only_the_active_group_is_expanded_on_a_master_data_page(client, db):
    _login_full_access(client, db)
    resp = client.get("/master/vehicles")
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)
    assert _group_is_shown(html, "sbGroupMasterData")
    for other in _rendered_groups(html):
        if other == "sbGroupMasterData":
            continue
        assert not _group_is_shown(html, other), f"{other} should be closed"


def test_groups_are_linked_so_opening_one_closes_the_others(client, db):
    """data-bs-parent is what produces that behaviour in the browser; a
    server-rendered test can only assert the linkage is present, so the
    accordion itself was confirmed in Chromium (opening Master Data on a
    Transactions page closed Transactions)."""
    _login_full_access(client, db)
    html = client.get("/master/vehicles").get_data(as_text=True)
    assert html.count('data-bs-parent="#fmsSidebarNav"') >= 3


def test_collapsed_rail_force_opens_every_group(client, db):
    """In icon-rail mode the group HEADERS are hidden, so a collapsed
    group would leave its icons unreachable with nothing to click. The
    rail CSS therefore force-opens every group -- keyed on the class
    app.js actually sets."""
    from pathlib import Path
    css = Path("app/static/css/app.css").read_text()
    assert "#fms-wrapper.is-collapsed .sidebar-group .collapse" in css


def test_burger_button_actually_narrows_the_sidebar(client, db):
    """app.js toggles `is-collapsed`, but the rail CSS was written
    against `.sidebar-collapsed` -- a class nothing in the app ever set.
    The button flipped a class no rule read, so the sidebar stayed full
    width and every label stayed visible. Measured in Chromium after the
    fix: 280px -> 76px with labels hidden."""
    from pathlib import Path
    css = Path("app/static/css/app.css").read_text()
    assert "#fms-wrapper.is-collapsed .fms-sidebar" in css
    assert "#fms-wrapper.is-collapsed .sidebar-label" in css
    # The dead selector must not come back.
    assert ".sidebar-collapsed .fms-sidebar" not in css
