"""Tests for the vehicle create/update endpoints.

The first WRITE path in the React API surface.

Every rule asserted here is already enforced by VehicleService and is
the reason the Flask data is clean. The endpoints must REUSE those
rules, never restate them: a validation re-implemented in the API layer
drifts from the one the Jinja form uses, and the two would then disagree
about what a valid vehicle is.

Written from docs/parity-vehicle-form.md in the frontend repo, before
the endpoints existed.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import User, Role, Permission
from app.core.security.registry import sync_permissions


@pytest.fixture()
def write_env(db):
    sync_permissions()
    db.session.commit()

    editor = Role(name="Vehicle Editor")
    editor.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view", "vehicle.create",
                             "vehicle.update"])).all()
    writer = User(username="writer", email="w@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    writer.roles = [editor]

    # Can read the fleet but must not be able to change it.
    viewer_role = Role(name="Vehicle Viewer")
    viewer_role.permissions = Permission.query.filter(
        Permission.code == "vehicle.view").all()
    viewer = User(username="viewer", email="v@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]

    db.session.add_all([editor, writer, viewer_role, viewer])

    vt = VehicleTypeService().create(code="LV-W", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-W", name="Write Branch")

    # strict=True means brand and model must resolve against the master
    # list -- the same rule the Jinja form applies. Seeding them here is
    # not test convenience; it mirrors what a real install has.
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService, VehicleModelService)
    brand = VehicleBrandService().create("Toyota")
    VehicleModelService().create(brand.id, "Hilux")
    db.session.commit()
    return writer, branch, vt


def _token(client, username="writer"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _post(client, url, payload, token):
    r = client.post(url, json=payload,
                    headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _put(client, url, payload, token):
    r = client.put(url, json=payload,
                   headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _valid(branch, vt, **over):
    payload = {
        "vehicle_type_id": vt.id,
        "brand": "Toyota",
        "model": "Hilux",
        "branch_id": branch.id,
        "year": 2021,
        "conduction_number": "W-001",
        "plate_number": "WRT-1111",
    }
    payload.update(over)
    return payload


# ── Permission ──────────────────────────────────────────────────────────────

def test_create_requires_a_token(db, client, write_env):
    assert client.post("/api/v1/vehicles", json={}).status_code == 401


def test_create_requires_the_create_permission(db, client, write_env):
    """vehicle.view must not imply the ability to add a vehicle."""
    _, branch, vt = write_env
    token = _token(client, "viewer")
    status, _ = _post(client, "/api/v1/vehicles", _valid(branch, vt), token)
    assert status == 403


def test_update_requires_the_update_permission(db, client, write_env):
    _, branch, vt = write_env
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2021,
        branch_id=branch.id, conduction_number="W-900")
    token = _token(client, "viewer")
    status, _ = _put(client, f"/api/v1/vehicles/{v.id}", {"color": "Red"}, token)
    assert status == 403


# ── Create ──────────────────────────────────────────────────────────────────

def test_create_returns_the_new_vehicle(db, client, write_env):
    _, branch, vt = write_env
    token = _token(client)
    status, body = _post(client, "/api/v1/vehicles", _valid(branch, vt), token)
    assert status == 201
    assert body["plate_number"] == "WRT-1111"
    assert body["id"]


def test_create_persists_every_section_of_the_form(db, client, write_env):
    """The form spans six sections and 64 fields. A create that silently
    drops the ones the API forgot to list would lose data the user
    typed, with no error to show for it."""
    _, branch, vt = write_env
    token = _token(client)
    payload = _valid(branch, vt, conduction_number="W-002",
                     plate_number="WRT-2222",
                     color="White", fuel_type="Diesel", variant="G 4x4",
                     chassis_number="CH-123", engine_number="EN-123",
                     transmission="Manual 6MT", engine_type="2GD",
                     displacement="2400cc",
                     mv_file_number="1315-001", lto_office="LTO Calamba",
                     acquisition_cost="1234567.89", remarks="Night haul")
    status, body = _post(client, "/api/v1/vehicles", payload, token)
    assert status == 201

    from app.modules.master_data.vehicle.models import Vehicle
    v = db.session.get(Vehicle, body["id"])
    assert v.color == "White"
    assert v.chassis_number == "CH-123"
    assert v.mv_file_number == "1315-001"
    assert v.remarks == "Night haul"
    assert str(v.acquisition_cost) == "1234567.89"


# ── Validation: mirrored, not re-implemented ────────────────────────────────

def test_missing_brand_is_a_field_error(db, client, write_env):
    """Field-level, so the form can attach it to the right input rather
    than showing one banner above 64 fields."""
    _, branch, vt = write_env
    token = _token(client)
    payload = _valid(branch, vt)
    payload["brand"] = ""
    status, body = _post(client, "/api/v1/vehicles", payload, token)
    assert status == 400
    assert body["error"] == "validation"
    assert "brand" in body["fields"]


def test_missing_model_is_a_field_error(db, client, write_env):
    _, branch, vt = write_env
    token = _token(client)
    payload = _valid(branch, vt)
    payload["model"] = ""
    status, body = _post(client, "/api/v1/vehicles", payload, token)
    assert status == 400
    assert "model" in body["fields"]


def test_duplicate_conduction_number_is_rejected(db, client, write_env):
    _, branch, vt = write_env
    token = _token(client)
    _post(client, "/api/v1/vehicles",
          _valid(branch, vt, conduction_number="W-DUP",
                 plate_number="WRT-3333"), token)
    status, body = _post(
        client, "/api/v1/vehicles",
        _valid(branch, vt, conduction_number="W-DUP",
               plate_number="WRT-4444"), token)
    assert status == 400
    assert "conduction_number" in body["fields"]


def test_duplicate_plate_number_is_rejected(db, client, write_env):
    _, branch, vt = write_env
    token = _token(client)
    _post(client, "/api/v1/vehicles",
          _valid(branch, vt, conduction_number="W-005",
                 plate_number="WRT-DUP"), token)
    status, body = _post(
        client, "/api/v1/vehicles",
        _valid(branch, vt, conduction_number="W-006",
               plate_number="WRT-DUP"), token)
    assert status == 400
    assert "plate_number" in body["fields"]


def test_duplicate_engine_number_is_rejected(db, client, write_env):
    _, branch, vt = write_env
    token = _token(client)
    _post(client, "/api/v1/vehicles",
          _valid(branch, vt, conduction_number="W-007",
                 plate_number="WRT-7777", engine_number="EN-DUP"), token)
    status, body = _post(
        client, "/api/v1/vehicles",
        _valid(branch, vt, conduction_number="W-008",
               plate_number="WRT-8888", engine_number="EN-DUP"), token)
    assert status == 400
    assert "engine_number" in body["fields"]


def test_a_rejected_create_writes_nothing(db, client, write_env):
    """A failed validation must not leave a half-written row behind."""
    from app.modules.master_data.vehicle.models import Vehicle
    _, branch, vt = write_env
    token = _token(client)
    before = Vehicle.query.count()
    payload = _valid(branch, vt, conduction_number="W-009")
    payload["brand"] = ""
    _post(client, "/api/v1/vehicles", payload, token)
    assert Vehicle.query.count() == before


# ── Update ──────────────────────────────────────────────────────────────────

def test_update_changes_only_what_was_sent(db, client, write_env):
    """A partial update must not blank the fields it omitted -- the form
    can submit a section at a time, and losing the other five would be
    silent data destruction."""
    _, branch, vt = write_env
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2021,
        branch_id=branch.id, conduction_number="W-010",
        plate_number="WRT-1010", color="White", remarks="Keep me")
    token = _token(client)
    status, _ = _put(client, f"/api/v1/vehicles/{v.id}",
                     {"color": "Red"}, token)
    assert status == 200

    from app.modules.master_data.vehicle.models import Vehicle
    fresh = db.session.get(Vehicle, v.id)
    assert fresh.color == "Red"
    assert fresh.remarks == "Keep me"


def test_update_rejects_a_duplicate_engine_number(db, client, write_env):
    _, branch, vt = write_env
    svc = VehicleService()
    svc.create(vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
               year=2021, branch_id=branch.id, conduction_number="W-011",
               engine_number="EN-TAKEN")
    target = svc.create(vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
                        year=2021, branch_id=branch.id,
                        conduction_number="W-012")
    token = _token(client)
    status, body = _put(client, f"/api/v1/vehicles/{target.id}",
                        {"engine_number": "EN-TAKEN"}, token)
    assert status == 400
    assert "engine_number" in body["fields"]


def test_update_of_an_invisible_vehicle_is_not_found(db, client, write_env):
    """Same answer as the detail endpoint, so the id space cannot be
    probed by attempting writes."""
    token = _token(client)
    status, _ = _put(client, "/api/v1/vehicles/999999", {"color": "Red"}, token)
    assert status == 404


def test_missing_year_is_a_field_error(db, client, write_env):
    """Vehicle.year is NOT NULL. Without an explicit check the resulting
    IntegrityError surfaces through the service's generic duplicate
    message -- "a unique field is already used" -- which is both wrong
    and impossible to act on.
    """
    _, branch, vt = write_env
    token = _token(client)
    payload = _valid(branch, vt, conduction_number="W-013")
    payload.pop("year")
    status, body = _post(client, "/api/v1/vehicles", payload, token)
    assert status == 400
    assert "year" in body["fields"]
