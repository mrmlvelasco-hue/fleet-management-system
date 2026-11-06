"""
The Master Data admin screen's GET /api/v1/vehicle-types (masters.py,
needs vehicletype.view, returns is_active + description for the full
CRUD list) and the Vehicle-form dropdown's GET /api/v1/vehicle-types
(vehicles.py, needs only vehicle.view, returns just id/code/name/category)
were both registered on the exact same route. vehicles.py is imported
first in app/__init__.py, so its view silently won every request --
masters.py's admin list was unreachable through HTTP even though its
code was correct.

Symptom this produced in production: the Master Data -> Vehicle Types
screen showed every single row (all genuinely is_active=True in the
DB) as INACTIVE, and every Description as blank -- because the winning
handler's JSON never included those fields, and the frontend's
Boolean(undefined) is false.

Fix: the dropdown-only route moved to /vehicle-types/dropdown so the
admin CRUD route at /vehicle-types is reachable again.
"""
import json

from app.core.security.password import hash_password
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.user_management.models import User, Role, Permission
from app.core.security.registry import sync_permissions


def _token(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def test_bare_vehicle_types_path_resolves_to_a_single_handler(app):
    """Regression guard: fail loudly if a future route is ever added
    back onto the same path, instead of one silently shadowing another."""
    with app.app_context():
        matches = [r for r in app.url_map.iter_rules()
                   if str(r) == "/api/v1/vehicle-types" and "GET" in r.methods]
        assert len(matches) == 1, (
            f"expected exactly one GET handler for /api/v1/vehicle-types, "
            f"found {len(matches)}: {[m.endpoint for m in matches]}")


def test_admin_vehicle_types_list_includes_status_and_description(db, client):
    sync_permissions()
    db.session.commit()
    role = Role(name="Vehicle Type Admin")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicletype.view"])).all()
    user = User(username="vtadmin", email="vtadmin@example.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    VehicleTypeService().create(code="900", name="Test Type",
                                category="LIGHT", description="desc here")
    db.session.commit()

    token = _token(client, "vtadmin")
    status, body = _get(client, "/api/v1/vehicle-types", token)
    assert status == 200
    row = next(i for i in body["items"] if i["code"] == "900")
    assert row["is_active"] is True
    assert row["description"] == "desc here"


def test_dropdown_endpoint_moved_and_still_needs_only_vehicle_view(db, client):
    sync_permissions()
    db.session.commit()
    role = Role(name="Fleet Viewer")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view"])).all()
    user = User(username="dropdownuser", email="dd@example.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    VehicleTypeService().create(code="901", name="Dropdown Type",
                                category="LIGHT")
    db.session.commit()

    token = _token(client, "dropdownuser")
    status, body = _get(client, "/api/v1/vehicle-types/dropdown", token)
    assert status == 200
    assert body["items"]
    assert set(body["items"][0]) >= {"id", "name"}
