from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login_full_access(client, db):
    """A user with permissions spanning all three sidebar groups, so we
    can tell which group the server chose to expand by default."""
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


def test_dashboard_expands_no_group_by_default(client, db):
    """On Dashboard (no group matches), all three groups should render
    collapsed — the reported bug was that every group was ALWAYS
    expanded regardless of context, forcing a long scroll to reach
    lower groups like Transactions."""
    _login_full_access(client, db)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b'id="sbGroupSysAdmin" class="collapse "' in resp.data or \
           b'id="sbGroupSysAdmin"' in resp.data
    # None of the three collapse divs should carry the "show" class here.
    html = resp.data.decode()
    for group_id in ["sbGroupSysAdmin", "sbGroupMasterData", "sbGroupTransactions"]:
        # Find the div and confirm it doesn't have 'show' in its class list
        idx = html.find(f'id="{group_id}"')
        assert idx != -1
        snippet = html[max(0, idx - 60):idx]
        assert "show" not in snippet


def test_master_data_page_expands_only_master_data_group(client, db):
    _login_full_access(client, db)
    resp = client.get("/master/vehicles")
    assert resp.status_code == 200
    html = resp.data.decode()

    md_idx = html.find('id="sbGroupMasterData"')
    md_snippet = html[max(0, md_idx - 60):md_idx]
    assert "show" in md_snippet

    tx_idx = html.find('id="sbGroupTransactions"')
    tx_snippet = html[max(0, tx_idx - 60):tx_idx]
    assert "show" not in tx_snippet


def test_transactions_page_expands_only_transactions_group(client, db):
    _login_full_access(client, db)
    resp = client.get("/transactions/trip-tickets")
    assert resp.status_code == 200
    html = resp.data.decode()

    tx_idx = html.find('id="sbGroupTransactions"')
    tx_snippet = html[max(0, tx_idx - 60):tx_idx]
    assert "show" in tx_snippet

    md_idx = html.find('id="sbGroupMasterData"')
    md_snippet = html[max(0, md_idx - 60):md_idx]
    assert "show" not in md_snippet


def test_pm_schedule_page_expands_master_data_not_transactions(client, db):
    """PM Templates moved into Master Data — viewing it should expand
    Master Data, not Transactions, and should NOT force scrolling past
    an expanded Transactions group to get back to it."""
    _login_full_access(client, db)
    resp = client.get("/admin/pm-schedules")
    assert resp.status_code == 200
    html = resp.data.decode()

    md_idx = html.find('id="sbGroupMasterData"')
    md_snippet = html[max(0, md_idx - 60):md_idx]
    assert "show" in md_snippet

    tx_idx = html.find('id="sbGroupTransactions"')
    tx_snippet = html[max(0, tx_idx - 60):tx_idx]
    assert "show" not in tx_snippet


def test_sidebar_still_lists_every_permitted_module_when_collapsed(client, db):
    """Collapsing by default must not hide modules from the DOM — they
    should still be present (just visually collapsed via CSS/JS), so a
    person can still reach everything they have access to."""
    _login_full_access(client, db)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Trip Tickets" in resp.data
    assert b"Vehicles" in resp.data
    assert b"Users" in resp.data
    assert b"PM Templates" in resp.data
