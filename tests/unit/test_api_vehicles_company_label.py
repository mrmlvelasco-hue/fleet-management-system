"""Vehicle list/detail JSON carries a disambiguated Company label
(2026-11-05).

Michael asked for Branch to come off the Vehicle view/edit screens and
the Dashboard so Company is the only org field shown there, mirroring
the 2026-10-30 cutover already done on the Vehicle form, the Vehicles
list filter, and the Vehicle Register Details report.

The list (`_serialise`) and detail (`detail_json`) payloads already
carry `company` as the bare `short_name`, which is ambiguous on its
own: per D2, the same short_name legitimately repeats across different
Region/Area-District rows (Michael's own fleet has five "Excellence"
companies). Every other screen that renders a Company to a person --
the Company picker, the dashboard chart legend, the Vehicle Register
Details report -- already solves this with a `company_label` field
built from the one shared helper (`app.core.company_resolution.
company_label`). The Vehicle list and detail screens are the one place
left that would show a bare, possibly-ambiguous short_name once Branch
comes off them, so this adds the same `company_label` field here too,
alongside (not replacing) the existing `company`/`company_id`.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.org.service import (
    RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import User, Role, Permission
from app.core.security.registry import sync_permissions
from app.extensions import db


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Fleet Viewer CL")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view"])).all()
    user = User(username="vehcluser", email="vehcl@example.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])

    # Two Companies sharing one short_name, differing only by Region +
    # Area/District -- the exact ambiguity D2 exists to resolve.
    north = RegionService().create(code="NL-CL", name="North Luzon")
    south = RegionService().create(code="SL-CL", name="South Luzon")
    nl2 = AreaDistrictService().create(code="NL2-CL", name="NORTH LUZON 2",
                                       region_id=north.id)
    cav = AreaDistrictService().create(code="CAV-CL", name="CAVITE 2",
                                       region_id=south.id)
    co_a = OrgCompanyService().create(short_name="Excellence",
                                      region_id=north.id,
                                      area_district_id=nl2.id)
    co_b = OrgCompanyService().create(short_name="Excellence",
                                      region_id=south.id,
                                      area_district_id=cav.id)

    vt = VehicleType(code="CAR-CL", name="Car", category="LIGHT")
    db.session.add(vt)
    db.session.commit()

    v = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024,
                                company_id=co_a.id, plate_number="CL-001")
    db.session.commit()
    return {"vehicle": v, "co_a": co_a, "co_b": co_b}


def _token(client, username="vehcluser"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_list_row_carries_a_disambiguated_company_label(db, client, env):
    token = _token(client)
    r = client.get("/api/v1/vehicles", headers=_auth(token))
    body = json.loads(r.get_data(as_text=True))
    row = next(i for i in body["items"] if i["plate_number"] == "CL-001")
    assert row["company"] == "Excellence"
    assert row["company_label"] == "Excellence (North Luzon / NORTH LUZON 2)"


def test_detail_carries_a_disambiguated_company_label(db, client, env):
    token = _token(client)
    vid = env["vehicle"].id
    r = client.get(f"/api/v1/vehicles/{vid}", headers=_auth(token))
    body = json.loads(r.get_data(as_text=True))
    assert body["company"] == "Excellence"
    assert body["company_label"] == "Excellence (North Luzon / NORTH LUZON 2)"
