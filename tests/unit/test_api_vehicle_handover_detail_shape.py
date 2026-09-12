"""GET /vehicle-handovers/<id> carries BOTH the print-shape (report
fields, no ids -- react-v191's exact prop contract) and an editable
flat items/damages list WITH ids, so the edit screen can PATCH a
specific item without the print component ever seeing an id it has no
business rendering."""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def rig(db, app):
    sync_permissions()
    db.session.commit()
    branch = Branch(name="HQ", code="HQ")
    db.session.add(branch)
    db.session.flush()
    vtype = VehicleType(code="CARL", name="Car Light", category="CAR")
    db.session.add(vtype)
    db.session.flush()
    vehicle = Vehicle(plate_number="NAG 1234", brand="Toyota", model="Hilux",
                      year=2022, vehicle_type_id=vtype.id,
                      branch_id=branch.id, is_active=True, status="ACTIVE")
    db.session.add(vehicle)
    role = Role(name="Full")
    role.permissions = Permission.query.filter(
        Permission.code.like("vehiclehandover.%")).all()
    user = User(username="officer", email="o@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add(user)
    db.session.commit()
    return {"vehicle": vehicle}


def _headers(client):
    tok = client.post("/api/v1/auth/token",
                      json={"username": "officer", "password": "secret123"})
    return {"Authorization": f"Bearer {json.loads(tok.get_data(as_text=True))['access_token']}"}


def test_detail_carries_editable_item_ids_alongside_report_shape(client, app, rig):
    headers = _headers(client)
    created = client.post("/api/v1/vehicle-handovers",
                          json={"vehicle_id": rig["vehicle"].id},
                          headers=headers)
    hid = json.loads(created.get_data(as_text=True))["id"]

    detail = json.loads(
        client.get(f"/api/v1/vehicle-handovers/{hid}", headers=headers)
        .get_data(as_text=True))

    # The editable list has real ids to PATCH against.
    assert len(detail["items"]) >= 30
    assert all(isinstance(i["id"], int) for i in detail["items"])

    # The print-shape columns are STILL present and STILL id-free --
    # the report route (/report) reuses the exact same to_report(), so
    # this also guards that endpoint by construction.
    assert len(detail["columns"]) == 3
    for col in detail["columns"]:
        for line in col["lines"]:
            assert "id" not in line


def test_editable_item_id_actually_patches(client, app, rig):
    headers = _headers(client)
    created = client.post("/api/v1/vehicle-handovers",
                          json={"vehicle_id": rig["vehicle"].id},
                          headers=headers)
    hid = json.loads(created.get_data(as_text=True))["id"]
    detail = json.loads(
        client.get(f"/api/v1/vehicle-handovers/{hid}", headers=headers)
        .get_data(as_text=True))
    item_id = detail["items"][0]["id"]

    resp = client.patch(
        f"/api/v1/vehicle-handovers/{hid}/items/{item_id}",
        json={"phase": "issuance", "mark": "OK", "qty": 1},
        headers=headers)
    assert resp.status_code == 200
    assert json.loads(resp.get_data(as_text=True))["issuance_mark"] == "OK"
