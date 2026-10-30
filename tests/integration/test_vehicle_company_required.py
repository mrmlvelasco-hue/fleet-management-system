"""Company is the required org field on Vehicle creation (2026-10-30).

Michael's decision: Branch comes off the Vehicle form and Company takes
its place as the required field. Requiring Company is not cosmetic -- it
is what keeps a branchless vehicle from having NO org anchor at all,
which would make it visible to every user (see
tests/unit/test_nullable_branch_visibility.py).

Rejected explicitly, with a field-level message, rather than being left
to fail as an IntegrityError: the same reasoning the Year check beside
it already carries -- a database error surfaces to the user as "a unique
field is already used", which is both wrong and unactionable.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.master_data.org.service import (
    RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.reference.models import VehicleType
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    north = RegionService().create(code="NL-CR", name="North Luzon")
    nl2 = AreaDistrictService().create(code="NL2-CR", name="NORTH LUZON 2",
                                       region_id=north.id)
    co = OrgCompanyService().create(short_name="Excellence",
                                    region_id=north.id,
                                    area_district_id=nl2.id)
    vt = VehicleType(code="CAR-CR", name="Car", category="LIGHT")
    # strict=True: brand and model must resolve against the master list,
    # same rule the Jinja route applies.
    from app.modules.master_data.vehicle_brand.models import (
        VehicleBrand, VehicleModel)
    brand = VehicleBrand(name="Toyota")
    _db.session.add_all([vt, brand])
    _db.session.commit()
    _db.session.add(VehicleModel(brand_id=brand.id, name="Hilux"))
    _db.session.commit()
    role = Role(name="Veh Create Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view", "vehicle.create"])).all()
    user = User(username="vcuser", email="vcuser@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"co": co, "vt": vt}


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "vcuser", "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _post(client, payload, token):
    r = client.post("/api/v1/vehicles", json=payload,
                    headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _base(env):
    return {"vehicle_type_id": env["vt"].id, "brand": "Toyota",
            "model": "Hilux", "year": 2024, "plate_number": "CR-001"}


def test_a_vehicle_is_created_with_a_company_and_no_branch(db, client, env):
    """Exactly what the new form posts."""
    status, body = _post(
        client, {**_base(env), "company_id": env["co"].id}, _token(client))
    assert status == 201
    assert body["company_id"] == env["co"].id


def test_creation_without_a_company_is_refused_with_a_field_message(
        db, client, env):
    status, body = _post(client, _base(env), _token(client))
    assert status == 400
    assert "company_id" in body["fields"]
    assert "Company is required." == body["fields"]["company_id"]


def test_the_refusal_names_company_not_a_database_error(db, client, env):
    """Guards the reason this check exists: without it the failure
    surfaced as a duplicate-key message naming the wrong field."""
    _status, body = _post(client, _base(env), _token(client))
    assert "unique" not in body["message"].lower()
    assert "already" not in body["message"].lower()


def test_a_string_company_id_from_the_browser_form_is_accepted(
        db, client, env):
    """React posts every field as a string (the v278 shape)."""
    status, body = _post(
        client, {**_base(env), "company_id": str(env["co"].id)},
        _token(client))
    assert status == 201
    assert body["company_id"] == env["co"].id
