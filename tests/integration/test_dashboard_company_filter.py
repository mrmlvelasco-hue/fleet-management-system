"""Stage 3C — Company filter on the Dashboard, ALONGSIDE the Branch one.

Michael's decision (2026-10-29): Branch is pure legacy and Company is the
organisational axis going forward, but the switch is additive first —
both filters coexist so the two can be reconciled against each other
before anything is removed.

The counts resolve a vehicle's EFFECTIVE Company (its own, else its
branch's — see app/core/company_resolution.py), which is what lets this
read real numbers today: no vehicle has `company_id` set, but mapping a
handful of branches to Companies gives every vehicle under them one.
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
    south = RegionService().create(code="SL-DF", name="South Luzon")
    cav = AreaDistrictService().create(code="CAV-DF", name="CAVITE 2",
                                       region_id=south.id)
    sl3 = AreaDistrictService().create(code="SL3-DF", name="SOUTH LUZON 3",
                                       region_id=south.id)
    cav_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=south.id,
                                        area_district_id=cav.id)
    sl3_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=south.id,
                                        area_district_id=sl3.id)
    cav_br = BranchService().create(code="BR-CAV-DF", name="Cavite Branch",
                                    company_id=cav_co.id)
    sl3_br = BranchService().create(code="BR-SL3-DF", name="SL3 Branch",
                                    company_id=sl3_co.id)
    legacy = BranchService().create(code="BR-LEG-DF", name="Unmapped Legacy")
    vt = VehicleType(code="CAR-DF", name="Car", category="LIGHT")
    _db.session.add(vt)
    _db.session.commit()

    for plate, br in (("CAV-001", cav_br), ("CAV-002", cav_br),
                      ("SL3-001", sl3_br), ("LEG-001", legacy)):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=br.id,
                                plate_number=plate)

    role = Role(name="Dash Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view", "branch.view",
                             "orgcompany.view"])).all()
    user = User(username="dashuser", email="dashuser@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"cav_co": cav_co, "sl3_co": sl3_co, "cav_br": cav_br,
            "legacy": legacy}


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "dashuser", "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def test_summary_counts_vehicles_of_the_requested_company(db, client, env):
    """Two vehicles sit under a branch mapped to CAVITE 2's company and
    neither has a company of its own — they still count, inherited
    through the branch."""
    status, body = _get(
        client, f"/api/v1/dashboard/summary?company_id={env['cav_co'].id}",
        _token(client))
    assert status == 200
    assert body["fleet_count"] == 2
    assert body["company_id"] == env["cav_co"].id


def test_summary_without_a_company_is_unchanged(db, client, env):
    """Existing callers pass no company_id and must see the whole fleet,
    exactly as before — this filter is additive."""
    status, body = _get(client, "/api/v1/dashboard/summary", _token(client))
    assert status == 200
    assert body["fleet_count"] == 4
    assert body["company_id"] is None


def test_company_and_branch_filters_compose(db, client, env):
    """Both filters coexist during the transition, so asking for a branch
    that is NOT under the requested company must narrow to nothing rather
    than quietly ignoring one of them."""
    status, body = _get(
        client,
        f"/api/v1/dashboard/summary?company_id={env['sl3_co'].id}"
        f"&branch_id={env['cav_br'].id}",
        _token(client))
    assert status == 200
    assert body["fleet_count"] == 0


def test_unknown_company_is_rejected(db, client, env):
    status, body = _get(client, "/api/v1/dashboard/summary?company_id=999999",
                        _token(client))
    assert status == 400


def test_non_integer_company_is_rejected(db, client, env):
    status, _ = _get(client, "/api/v1/dashboard/summary?company_id=abc",
                     _token(client))
    assert status == 400


def test_companies_endpoint_feeds_the_filter_with_distinguishable_labels(
        db, client, env):
    """Same rule as every other Company picker: per D2 the short_name
    repeats, so the label must carry Region and Area/District."""
    status, body = _get(client, "/api/v1/dashboard/companies", _token(client))
    assert status == 200
    labels = sorted(c["label"] for c in body["items"])
    assert labels == ["Excellence (South Luzon / CAVITE 2)",
                      "Excellence (South Luzon / SOUTH LUZON 3)"]


def test_fleet_status_honours_the_company_filter(db, client, env):
    status, body = _get(
        client,
        f"/api/v1/dashboard/fleet-status?company_id={env['cav_co'].id}",
        _token(client))
    assert status == 200
    # {labels, data, colors} -- the shape the Jinja dashboard's Chart.js
    # already consumes, kept identical for the React one.
    assert sum(body["data"]) == 2
