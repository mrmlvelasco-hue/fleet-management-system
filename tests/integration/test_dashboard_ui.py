from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.user_management.org_scope_service import UserOrgScopeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService


def _login(client, db, *, codes=()):
    role = Role(name="DashUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="dash_ui_user", email="dash_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "dash_ui_user", "password": "pw123456"})
    return u


def test_dashboard_shows_real_fleet_count_not_placeholder(client, db):
    _login(client, db)
    branch = BranchService().create(code="BR-DASHUI", name="Dash UI Branch")
    vt = VehicleTypeService().create(code="LV-DASHUI", name="Light", category="LIGHT")
    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota", model="Vios",
                           year=2024, branch_id=branch.id,
                           conduction_number="DASHUI-000")

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Fleet" in resp.data
    # No longer showing the raw placeholder dash for the count
    assert b'<div class="fs-4 fw-semibold">\xe2\x80\x94</div>' not in resp.data


def test_dashboard_fleet_count_respects_org_scope(client, db):
    branch_mine = BranchService().create(code="BR-DASHUI2", name="Dash UI Branch 2")
    branch_other = BranchService().create(code="BR-DASHUI3", name="Dash UI Branch 3")
    vt = VehicleTypeService().create(code="LV-DASHUI2", name="Light", category="LIGHT")
    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota", model="Vios",
                           year=2024, branch_id=branch_mine.id,
                           conduction_number="DASHUI-001")
    VehicleService().create(vehicle_type_id=vt.id, brand="Honda", model="City",
                           year=2024, branch_id=branch_other.id,
                           conduction_number="DASHUI-002")

    user = _login(client, db)
    UserOrgScopeService().assign(user.id, scope_type="BRANCH",
                                branch_id=branch_mine.id)

    resp = client.get("/")
    assert resp.status_code == 200
    # Fleet card should show 1 (only their branch's vehicle), not 2
    assert b'<div class="fs-4 fw-semibold">1</div>' in resp.data


def test_hidden_widget_does_not_render(client, db):
    from app.modules.system_admin.models import DashboardWidget
    widgets = [
        ("FLEET", "Fleet", "bi-truck", 1),
        ("MAINTENANCE", "Maintenance", "bi-wrench", 2),
        ("APPROVALS", "Approvals", "bi-check2-square", 3),
        ("REGISTRATIONS", "Registrations", "bi-card-checklist", 4),
        ("TIRES", "Tires", "bi-circle", 5),
        ("BATTERIES", "Batteries", "bi-battery-half", 6),
    ]
    for code, label, icon, sort in widgets:
        db.session.add(DashboardWidget(code=code, label=label, icon=icon,
                                       sort_order=sort, default_visible=True))
    db.session.commit()

    user = _login(client, db, codes=["dashboardconfig.view", "dashboardconfig.update"])
    client.post("/admin/dashboard-config", data={
        "visible_widgets": ["FLEET", "APPROVALS", "TIRES", "BATTERIES", "REGISTRATIONS"],
        # MAINTENANCE intentionally omitted -> hidden
    })
    resp = client.get("/")
    assert resp.status_code == 200
    assert b">Maintenance<" not in resp.data
    assert b">Fleet<" in resp.data


def test_for_my_action_widget_still_present(client, db):
    _login(client, db)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"For My Action" in resp.data


def test_vehicles_due_for_maintenance_widget_present(client, db):
    _login(client, db)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Vehicles Due for Maintenance" in resp.data
