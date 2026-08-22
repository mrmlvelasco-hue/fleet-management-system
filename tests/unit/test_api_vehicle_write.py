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
from datetime import date
from decimal import Decimal

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


# ── Detail payload completeness ─────────────────────────────────────────────

def _detail(client, token, vehicle_id):
    r = client.get(f"/api/v1/vehicles/{vehicle_id}",
                   headers={"Authorization": f"Bearer {token}"})
    return json.loads(r.get_data(as_text=True))


def _a_vehicle(write_env):
    """One saved vehicle to read back. write_env yields the actors and
    reference data; these tests need a row."""
    from app.modules.master_data.vehicle.service import VehicleService
    _writer, branch, vt = write_env
    return VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2021,
        branch_id=branch.id, plate_number="DET-0001", strict=True)


def test_detail_returns_every_writable_field(db, client, write_env):
    """A field the form can WRITE but cannot READ BACK is worse than a
    missing field.

    `cr_number` was exactly this: the React form rendered the input and
    read `v.cr_number` from this payload, which never contained it. The
    box was blank on every edit regardless of what was stored, so the
    only way to discover the real value was to open the Jinja screen.

    This asserts the payload covers the write allowlist, so adding a
    writable column without a way to see it fails here rather than in
    front of a user.
    """
    from app.modules.api.vehicles import _WRITABLE

    body = _detail(client, _token(client), _a_vehicle(write_env).id)
    flat = dict(body)
    # Insurance is nested by design -- four covers with their own dates.
    for key, value in (body.get("insurance") or {}).items():
        if key == "covers":
            for cover in value:
                flat[f"has_{cover['code'].lower()}"] = cover["active"]
                flat[f"{cover['code'].lower()}_from_date"] = cover["from"]
                flat[f"{cover['code'].lower()}_to_date"] = cover["to"]
        else:
            flat[key] = value
    flat["insurance_reference_number"] = flat.get("reference_number")
    flat["comprehensive_insurance_provider"] = flat.get(
        "comprehensive_provider")
    flat["ctpl_insurance_provider"] = flat.get("ctpl_provider")

    missing = [f for f in _WRITABLE if f not in flat]
    assert not missing, f"writable but not readable: {sorted(missing)}"


def test_detail_round_trips_the_fields_the_form_lost(db, client, write_env):
    """Write, read back, compare. The specific columns React could not
    reach: none of them appeared in the payload, so all were invisible
    on edit."""
    vehicle_id = _a_vehicle(write_env).id
    token = _token(client)
    payload = {
        "cr_number": "CR-77-123", "far_number": "FAR-9", "supplier": "Acme",
        "leasing_company": "LeaseCo", "component_group": "ENGINE",
        "vehicle_body_type": "PICKUP", "notes": "migrated 2024",
        "assignment": "SECONDARY",
        "assignment_group_classification": "CAR_PLAN",
        "vehicle_usage": "SALES", "last_pm_odometer": 42000,
        "last_pm_date": "2025-03-01", "start_date": "2025-01-01",
        "end_date": "2028-01-01", "top_up_amount": "15000.00",
        "mr_eds": True, "with_vehicle_contract": True,
        "has_inland_marine": True,
    }
    r = client.put(f"/api/v1/vehicles/{vehicle_id}", json=payload,
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.get_data(as_text=True)

    body = _detail(client, token, vehicle_id)
    assert body["cr_number"] == "CR-77-123"
    assert body["far_number"] == "FAR-9"
    assert body["component_group"] == "ENGINE"
    assert body["vehicle_body_type"] == "PICKUP"
    assert body["notes"] == "migrated 2024"
    assert body["assignment"] == "SECONDARY"
    assert body["assignment_group_classification"] == "CAR_PLAN"
    assert body["vehicle_usage"] == "SALES"
    assert body["last_pm_odometer"] == 42000
    assert body["last_pm_date"] == "2025-03-01"
    assert body["mr_eds"] is True
    assert body["has_inland_marine"] is True
    assert body["top_up_amount"] == "15000.00"


def test_detail_carries_ids_not_only_display_names(db, client, write_env):
    """The detail SCREEN needs 'Juan Dela Cruz'; the FORM needs the id to
    preselect the dropdown. Returning only the name would make the
    Assigned Driver box render empty on a vehicle that has one, and the
    next save would clear the assignment."""
    body = _detail(client, _token(client), _a_vehicle(write_env).id)
    for key in ("assigned_driver_id", "department_id", "pm_schedule_id"):
        assert key in body


def test_computed_assured_value_is_offered_not_imposed(db, client, write_env):
    """10% compounding depreciation from Delivery Date, computed by the
    model. React must read it, never recompute it -- a second
    implementation of a money figure is a second answer.

    Offered separately from the stored value so the form can prefill
    without overwriting an appraised override.
    """
    body = _detail(client, _token(client), _a_vehicle(write_env).id)
    assert "computed_assured_value" in body


def test_a_malformed_date_is_a_field_error_not_a_500(db, client, write_env):
    """The value is the caller's mistake, so the caller must be told
    which field and why -- not handed a server error implying the
    request was fine and the server broke.

    Coercion happens during payload parsing, which sits OUTSIDE the
    try/except that shapes write errors, so this needs the parse to be
    inside it.
    """
    _writer, branch, vt = write_env
    r = client.post("/api/v1/vehicles", json={
        "vehicle_type_id": vt.id, "brand": "Toyota", "model": "Hilux",
        "year": 2022, "branch_id": branch.id,
        "plate_number": "BAD-0001", "delivery_date": "31/12/2025",
    }, headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 400, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    assert "delivery_date" in body["fields"]
    assert "YYYY-MM-DD" in body["fields"]["delivery_date"]


def test_a_valid_date_string_round_trips(db, client, write_env):
    """The ordinary case, which used to raise `SQLite Date type only
    accepts Python date objects` -- a 500 on a well-formed request."""
    _writer, branch, vt = write_env
    token = _token(client)
    r = client.post("/api/v1/vehicles", json={
        "vehicle_type_id": vt.id, "brand": "Toyota", "model": "Hilux",
        "year": 2022, "branch_id": branch.id, "plate_number": "GOOD-0001",
        "delivery_date": "2025-12-31", "start_date": "2025-01-01",
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    assert body["delivery_date"] == "2025-12-31"
    assert body["start_date"] == "2025-01-01"


# ── Numeric coercion ────────────────────────────────────────────────────────

def test_update_with_a_pm_schedule_does_not_500(db, client, write_env):
    """Reproduction of the reported 500 on PUT /api/v1/vehicles/91.

        TypeError: '>=' not supported between instances of 'str' and 'int'
        due_calculation_service.py:451  if current_km >= next_due_km

    JSON has no separate integer input, and the React form holds every
    value as a string, so `current_odometer` arrived as "42000".
    SQLAlchemy coerces on FLUSH, so the database is fine and a later GET
    reads back an int -- but the in-memory object still holds the string,
    and detail_json runs the PM due calculation against that same object
    immediately after the update. Hence a write that succeeds and then
    500s while reporting its own result.

    Only fires for vehicles with an applicable PM schedule, which is why
    it survived the suite: nothing exercised the write path and the due
    calculation together.
    """
    from app.modules.maintenance_config.models import PMSchedule
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.master_data.vehicle.service import VehicleService

    _writer, branch, vt = write_env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2021,
        branch_id=branch.id, plate_number="PM-500-1", strict=True)
    mt = MaintenanceTypeService().create(code="PM-500", name="Preventive",
                                         category="PREVENTIVE")
    db.session.add(PMSchedule(maintenance_type_id=mt.id, trigger_mode="KM",
                              interval_km=5000, vehicle_type_id=vt.id,
                              is_active=True))
    db.session.commit()

    r = client.put(f"/api/v1/vehicles/{vehicle.id}",
                   json={"current_odometer": "42000"},
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 200, r.get_data(as_text=True)


def test_integer_fields_are_coerced_before_the_service_sees_them(
        db, client, write_env):
    """The Jinja path int()s every one of these (routes.py 1247-1271).
    The API did not, so the two paths produced objects of different
    types from the same input -- and only one of them crashed."""
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.master_data.vehicle.service import VehicleService

    _writer, branch, vt = write_env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2021,
        branch_id=branch.id, plate_number="NUM-0001", strict=True)
    db.session.commit()

    r = client.put(f"/api/v1/vehicles/{vehicle.id}", json={
        "year": "2023", "current_odometer": "51000",
        "current_engine_hours": "120", "last_pm_odometer": "48000",
    }, headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 200, r.get_data(as_text=True)

    fresh = db.session.get(Vehicle, vehicle.id)
    for field in ("year", "current_odometer", "current_engine_hours",
                  "last_pm_odometer"):
        assert isinstance(getattr(fresh, field), int), (
            f"{field} is {type(getattr(fresh, field)).__name__}, not int")


def test_a_non_numeric_integer_is_a_field_error_not_a_500(db, client,
                                                          write_env):
    """int("abc") raises ValueError, which nothing catches. Coercing
    without shaping the failure would swap one 500 for another."""
    _writer, branch, vt = write_env
    r = client.post("/api/v1/vehicles", json={
        "vehicle_type_id": vt.id, "brand": "Toyota", "model": "Hilux",
        "year": "not-a-year", "branch_id": branch.id,
        "plate_number": "NUM-BAD1",
    }, headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 400, r.get_data(as_text=True)
    assert "year" in json.loads(r.get_data(as_text=True))["fields"]


def test_money_fields_stay_decimal_safe(db, client, write_env):
    """Money is parsed as Decimal, never float. float("0.1") + float("0.2")
    is not 0.3, and an acquisition cost that drifts by a centavo is a
    figure the client will eventually reconcile against."""
    from decimal import Decimal
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.master_data.vehicle.service import VehicleService

    _writer, branch, vt = write_env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2021,
        branch_id=branch.id, plate_number="MON-0001", strict=True)
    db.session.commit()

    r = client.put(f"/api/v1/vehicles/{vehicle.id}",
                   json={"acquisition_cost": "1234567.89"},
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 200, r.get_data(as_text=True)
    fresh = db.session.get(Vehicle, vehicle.id)
    assert fresh.acquisition_cost == Decimal("1234567.89")


def test_an_empty_numeric_string_clears_rather_than_crashing(db, client,
                                                            write_env):
    """int("") raises. A cleared optional field must read as None."""
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.master_data.vehicle.service import VehicleService

    _writer, branch, vt = write_env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2021,
        branch_id=branch.id, plate_number="NUM-EMPT", strict=True,
        current_engine_hours=99)
    db.session.commit()

    r = client.put(f"/api/v1/vehicles/{vehicle.id}",
                   json={"current_engine_hours": ""},
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert db.session.get(Vehicle, vehicle.id).current_engine_hours is None


# ── Clone ───────────────────────────────────────────────────────────────────

def test_clone_clears_the_unique_identifiers(db, client, write_env):
    """Wraps VehicleService.get_clone_data, which already decides what a
    clone may carry. Plate, conduction, chassis and engine numbers are
    excluded there because a clone that inherited them would collide
    with the original -- and a duplicate chassis number on a fleet
    register is a data problem nobody spots until an audit.
    """
    from app.modules.master_data.vehicle.service import VehicleService

    _writer, branch, vt = write_env
    original = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2021,
        branch_id=branch.id, plate_number="CLN-0001",
        conduction_number="CC-1", chassis_number="CHS-1",
        engine_number="ENG-1", color="White", strict=True)
    db.session.commit()

    r = client.get(f"/api/v1/vehicles/{original.id}/clone",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))

    for unique in ("plate_number", "conduction_number", "chassis_number",
                   "engine_number", "id"):
        assert not body.get(unique), f"{unique} survived the clone"
    # Everything else is what makes cloning worth doing.
    assert body["brand"] == "Toyota"
    assert body["model"] == "Hilux"
    assert body["color"] == "White"
    assert body["branch_id"] == branch.id


def test_clone_requires_the_create_permission(db, client, write_env):
    """A clone is a draft of a NEW vehicle, so it is gated on create --
    not view. Gating it on view would let a read-only user pull a
    prefilled form they cannot submit."""
    r = client.get("/api/v1/vehicles/1/clone",
                   headers={"Authorization": f"Bearer {_token(client, 'viewer')}"})
    assert r.status_code == 403


def test_clone_rejects_anonymous(db, client, write_env):
    assert client.get("/api/v1/vehicles/1/clone").status_code == 401


def test_clone_of_a_missing_vehicle_is_404(db, client, write_env):
    r = client.get("/api/v1/vehicles/999999/clone",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 404


def test_clone_serialises_dates_and_money_as_strings(db, client, write_env):
    """get_clone_data returns raw column values -- date and Decimal
    objects, which jsonify cannot encode. Returning them unconverted
    would 500, and converting with str() would produce a Decimal repr
    the form cannot put back in an input."""
    from app.modules.master_data.vehicle.service import VehicleService

    _writer, branch, vt = write_env
    original = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2021,
        branch_id=branch.id, plate_number="CLN-0002",
        acquisition_date=date(2021, 3, 1), strict=True)
    original.acquisition_cost = Decimal("1250000.50")
    db.session.commit()

    r = client.get(f"/api/v1/vehicles/{original.id}/clone",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    assert body["acquisition_date"] == "2021-03-01"
    assert body["acquisition_cost"] == "1250000.50"
