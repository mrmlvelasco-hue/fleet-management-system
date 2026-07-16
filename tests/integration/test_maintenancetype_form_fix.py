from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="MaintTypeFormRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="rani", email="rani@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "rani", "password": "pw123456"})
    return u


def test_maintenancetype_form_uses_fixed_category_dropdown(client, db):
    """Regression: Category was a free-text input, subject to confusing
    browser autofill suggesting Vehicle Type's LIGHT/HEAVY/etc. values
    (since both fields shared name="category"). Now it's a dropdown
    driven by the admin-configurable MAINTENANCE_CATEGORY Lookup rather
    than a hardcoded PREVENTIVE/CORRECTIVE/PREDICTIVE list."""
    _login(client, db, codes=["maintenancetype.view", "maintenancetype.create"])
    resp = client.get("/master/maintenance-types/new")
    assert resp.status_code == 200
    assert b'<select name="category"' in resp.data
    assert b"Preventive Maintenance" in resp.data
    assert b"Corrective Maintenance" in resp.data
    assert b'autocomplete="off"' in resp.data


def test_maintenancetype_form_no_longer_shows_dead_interval_fields(client, db):
    """Regression: Interval KM/Days fields on Maintenance Type were
    completely unused by the due-calculation logic (only PMSchedule's
    Make/Model-specific intervals are used), causing confusion about
    where to actually configure PM intervals."""
    _login(client, db, codes=["maintenancetype.view", "maintenancetype.create"])
    resp = client.get("/master/maintenance-types/new")
    assert resp.status_code == 200
    assert b'name="interval_km"' not in resp.data
    assert b'name="interval_days"' not in resp.data
    assert b"PM Templates" in resp.data  # points user to the right place


def test_create_maintenancetype_without_interval_fields(client, db):
    _login(client, db, codes=["maintenancetype.view", "maintenancetype.create"])
    resp = client.post("/master/maintenance-types/new", data={
        "code": "PMS-REG", "name": "Regression Test PMS",
        "category": "PM", "description": "test",
    }, follow_redirects=True)
    assert resp.status_code == 200
    from app.modules.master_data.reference.models import MaintenanceType
    mt = MaintenanceType.query.filter_by(code="PMS-REG").first()
    assert mt is not None
    assert mt.category == "PM"
