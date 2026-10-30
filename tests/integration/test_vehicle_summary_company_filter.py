"""Vehicle stat tiles must honour the Company filter (2026-10-30).

Michael's screenshot: Company set to "Excellence (North Luzon / NORTH
LUZON 2)", the table correctly reads "2 records (filtered)", and the
tiles above it read 156 / 155 / 1 / 3 / 153 -- the whole fleet.

Cause: GET /vehicles/summary parses branch_id but never company_id.
Identical to the /dashboard/bootstrap miss: a filter accepted by one
endpoint and ignored by its sibling, so the screen shows two different
answers to the same question at the same time.

The disposed count is asserted separately because it is a SECOND
list_page call in the same handler -- exactly the kind of sibling call
that gets missed when a parameter is threaded by hand.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    north = RegionService().create(code="NL-VS", name="North Luzon")
    south = RegionService().create(code="SL-VS", name="South Luzon")
    nl2 = AreaDistrictService().create(code="NL2-VS", name="NORTH LUZON 2",
                                       region_id=north.id)
    sl = AreaDistrictService().create(code="SL-VS2", name="South Luzon",
                                      region_id=south.id)
    nl2_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=north.id,
                                        area_district_id=nl2.id)
    sl_co = OrgCompanyService().create(short_name="Excellence",
                                       region_id=south.id,
                                       area_district_id=sl.id)
    eali = BranchService().create(code="EALI-VS", name="East Asia Labs",
                                  company_id=nl2_co.id)
    hq = BranchService().create(code="HQ-VS", name="Head Office",
                                company_id=sl_co.id)
    vt = VehicleType(code="CAR-VS", name="Car", category="LIGHT")
    _db.session.add(vt)
    _db.session.commit()

    for i in range(2):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=eali.id,
                                plate_number=f"VSN-{i:03d}")
    for i in range(5):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=hq.id,
                                plate_number=f"VSH-{i:03d}")
    # One disposed vehicle under each company, so the disposed badge has
    # something to get wrong.
    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                            model="Old", year=2008, branch_id=eali.id,
                            plate_number="VSN-DISP", status="DISPOSED")
    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                            model="Old", year=2008, branch_id=hq.id,
                            plate_number="VSH-DISP", status="DISPOSED")

    role = Role(name="Veh Summary Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view"])).all()
    user = User(username="vsuser", email="vsuser@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"nl2_co": nl2_co, "sl_co": sl_co, "eali": eali, "hq": hq}


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "vsuser", "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def test_tiles_narrow_to_the_chosen_company(db, client, env):
    token = _token(client)
    _s, everything = _get(client, "/api/v1/vehicles/summary", token)
    _s, narrowed = _get(
        client, f"/api/v1/vehicles/summary?company_id={env['nl2_co'].id}",
        token)
    assert everything["total"] == 7
    assert narrowed["total"] == 2, (
        "the tiles read the whole fleet while the table beside them "
        "showed the filtered set")


def test_tiles_match_the_equivalent_branch_filter(db, client, env):
    """The rule that caught every previous miss."""
    token = _token(client)
    _s, by_company = _get(
        client, f"/api/v1/vehicles/summary?company_id={env['nl2_co'].id}",
        token)
    _s, by_branch = _get(
        client, f"/api/v1/vehicles/summary?branch_id={env['eali'].id}", token)
    assert by_company["total"] == by_branch["total"] == 2


def test_the_disposed_badge_is_scoped_too(db, client, env):
    """A second list_page call in the same handler -- the sibling most
    likely to be missed when threading a parameter by hand."""
    token = _token(client)
    _s, everything = _get(client, "/api/v1/vehicles/summary", token)
    _s, narrowed = _get(
        client, f"/api/v1/vehicles/summary?company_id={env['nl2_co'].id}",
        token)
    assert everything["disposed"] == 2
    assert narrowed["disposed"] == 1


def test_a_malformed_company_is_rejected_not_ignored(db, client, env):
    token = _token(client)
    status, _b = _get(client, "/api/v1/vehicles/summary?company_id=abc",
                      token)
    assert status == 400
