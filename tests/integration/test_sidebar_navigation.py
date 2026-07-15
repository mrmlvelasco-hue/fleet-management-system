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
    assert idx != -1
    snippet = html[max(0, idx - 60):idx]
    return "show" in snippet


def test_all_groups_expanded_by_default_on_dashboard(client, db):
    """Per explicit request: every group stays open regardless of which
    page is active — opening/viewing one module must never collapse
    another. The sidebar itself scrolls independently within the
    viewport (theme.css's sticky + overflow-y:auto on .fms-sidebar)
    rather than restricting which groups are visible."""
    _login_full_access(client, db)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    for group_id in ["sbGroupSysAdmin", "sbGroupMasterData", "sbGroupTransactions"]:
        assert _group_is_shown(html, group_id), f"{group_id} should be expanded"


def test_all_groups_still_expanded_on_a_master_data_page(client, db):
    _login_full_access(client, db)
    resp = client.get("/master/vehicles")
    assert resp.status_code == 200
    html = resp.data.decode()
    for group_id in ["sbGroupSysAdmin", "sbGroupMasterData", "sbGroupTransactions"]:
        assert _group_is_shown(html, group_id), f"{group_id} should be expanded"


def test_all_groups_still_expanded_on_a_transactions_page(client, db):
    """Confirms viewing a Transactions page does NOT collapse Master
    Data or System Administration — this is the exact behavior that was
    reported as unwanted."""
    _login_full_access(client, db)
    resp = client.get("/transactions/trip-tickets")
    assert resp.status_code == 200
    html = resp.data.decode()
    for group_id in ["sbGroupSysAdmin", "sbGroupMasterData", "sbGroupTransactions"]:
        assert _group_is_shown(html, group_id), f"{group_id} should be expanded"


def test_all_groups_still_expanded_on_a_pm_template_page(client, db):
    _login_full_access(client, db)
    resp = client.get("/admin/pm-schedules")
    assert resp.status_code == 200
    html = resp.data.decode()
    for group_id in ["sbGroupSysAdmin", "sbGroupMasterData", "sbGroupTransactions"]:
        assert _group_is_shown(html, group_id), f"{group_id} should be expanded"


def test_sidebar_lists_every_permitted_module(client, db):
    _login_full_access(client, db)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Trip Tickets" in resp.data
    assert b"Vehicles" in resp.data
    assert b"Users" in resp.data
    assert b"PM Templates" in resp.data


def test_sidebar_toggle_buttons_have_no_accordion_parent_linkage(client, db):
    """Confirms there's no Bootstrap data-bs-parent tying the three
    groups together — opening one must never auto-close another."""
    _login_full_access(client, db)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"data-bs-parent" not in resp.data
