"""Every new record must resolve to a Company (2026-10-30).

When Branch came off the Vehicle and Driver forms, branch_id became
nullable. UserOrgScopeService.covers() treats "no org context" as "no
restriction", so a record with NEITHER Branch nor Company would be
visible to every user regardless of scope.

The rule is deliberately "must RESOLVE to a Company", not "must send
company_id". A caller supplying a branch that is mapped to a Company has
given a perfectly good anchor and the record inherits Company through it
(the §20 rule every list already uses). Demanding the column explicitly
would add no safety while breaking API callers, integrations and the
Excel importers -- all of which supply a branch, not a company.

Asserted for BOTH Vehicle and Driver, because they are separate handlers
and this project's recurring failure has been a rule applied to one
call site and missed on its sibling.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.reference.models import VehicleType
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    region = RegionService().create(code="NL-OA", name="North Luzon")
    area = AreaDistrictService().create(code="NL2-OA", name="NORTH LUZON 2",
                                        region_id=region.id)
    co = OrgCompanyService().create(short_name="Excellence",
                                    region_id=region.id,
                                    area_district_id=area.id)
    mapped = BranchService().create(code="MAP-OA", name="Mapped Branch",
                                    company_id=co.id)
    unmapped = BranchService().create(code="UNM-OA", name="Unmapped Branch")

    vt = VehicleType(code="CAR-OA", name="Car", category="LIGHT")
    from app.modules.master_data.vehicle_brand.models import (
        VehicleBrand, VehicleModel)
    brand = VehicleBrand(name="Toyota")
    _db.session.add_all([vt, brand])
    _db.session.commit()
    _db.session.add(VehicleModel(brand_id=brand.id, name="Hilux"))

    from app.modules.system_admin.models import SystemParameter
    row = SystemParameter.query.filter_by(
        code="REQUIRE_ASSIGNEE_PHOTO").first()
    if row is None:
        row = SystemParameter(code="REQUIRE_ASSIGNEE_PHOTO", value="NO",
                              data_type="BOOLEAN", group_name="DRIVER",
                              description="Migration switch",
                              is_active=True)
        _db.session.add(row)
    else:
        row.value = "NO"

    role = Role(name="Anchor Role")
    role.permissions = Permission.query.all()
    user = User(username="anchoruser", email="anchor@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"co": co, "mapped": mapped, "unmapped": unmapped, "vt": vt}


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "anchoruser", "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _post(client, path, payload, token):
    r = client.post(path, json=payload,
                    headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _vehicle(**extra):
    return {"vehicle_type_id": None, "brand": "Toyota", "model": "Hilux",
            "year": 2024, "plate_number": "OA-001", **extra}


def _driver(**extra):
    return {"first_name": "Juan", "last_name": "Cruz",
            "employee_number": "EMP-OA1", "assignee_type": "EMPLOYEE",
            **extra}


def _tire(**extra):
    return {"serial_number": "T-OA1", "brand": "Bridgestone",
            "size": "185/65R15", "tire_type": "RADIAL", **extra}


def _battery(**extra):
    return {"serial_number": "B-OA1", "brand": "Motolite", **extra}


# (label, path, payload builder)
CASES = [
    ("vehicle", "/api/v1/vehicles", _vehicle),
    ("driver", "/api/v1/drivers", _driver),
    ("tire", "/api/v1/tires", _tire),
    ("battery", "/api/v1/batteries", _battery),
]


@pytest.mark.parametrize("label,path,build", CASES)
def test_an_explicit_company_anchors_the_record(db, client, env, label,
                                                path, build):
    payload = build(company_id=env["co"].id)
    if label == "vehicle":
        payload["vehicle_type_id"] = env["vt"].id
    status, body = _post(client, path, payload, _token(client))
    assert status == 201, body


@pytest.mark.parametrize("label,path,build", CASES)
def test_a_mapped_branch_anchors_the_record_without_a_company_column(
        db, client, env, label, path, build):
    """What every API caller, integration and Excel importer sends."""
    payload = build(branch_id=env["mapped"].id)
    if label == "vehicle":
        payload["vehicle_type_id"] = env["vt"].id
    status, body = _post(client, path, payload, _token(client))
    assert status == 201, body
    # company_id stays null -- the record owns no Company of its own.
    # What it HAS is an anchor: the branch resolves to one, which is
    # what every list, count and report filters on.
    assert body.get("company_id") is None
    assert body.get("branch_id") == env["mapped"].id


@pytest.mark.parametrize("label,path,build", CASES)
def test_no_org_context_at_all_is_refused(db, client, env, label, path,
                                          build):
    """THE case this rule exists for: such a record would be visible to
    every user, because covers() reads 'no context' as 'no
    restriction'."""
    payload = build()
    if label == "vehicle":
        payload["vehicle_type_id"] = env["vt"].id
    status, body = _post(client, path, payload, _token(client))
    assert status == 400
    assert body["fields"]["company_id"] == "Company is required."


@pytest.mark.parametrize("label,path,build", CASES)
def test_an_unmapped_branch_is_refused_and_says_why(db, client, env, label,
                                                    path, build):
    """A branch with no Company resolves to nothing, so it is not an
    anchor. The message has to say that, or the user re-picks the same
    branch and sees the same refusal."""
    payload = build(branch_id=env["unmapped"].id)
    if label == "vehicle":
        payload["vehicle_type_id"] = env["vt"].id
    status, body = _post(client, path, payload, _token(client))
    assert status == 400
    assert "not mapped to a Company" in body["fields"]["company_id"]
