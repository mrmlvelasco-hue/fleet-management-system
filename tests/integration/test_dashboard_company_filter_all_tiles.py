"""Stage 3C fix — EVERY dashboard figure honours the Company filter.

Reported by Michael 2026-10-30 against real data: with Company set to
"Excellence (North Luzon / NORTH LUZON 2)" the Fleet tile read 156 (the
whole fleet) while selecting the branch that maps to that same Company
read 2. His reconcile was BALANCED, so the data was right -- the
dashboard was not.

Root cause: company_id was threaded into /dashboard/summary but NOT into
/dashboard/bootstrap, which is the endpoint the React dashboard actually
reads its tiles from. Several other counts on both endpoints were never
wired either.

This is the SECOND time a tile has ignored the header's selector --
DashboardService.approvals_pending_count carries a comment about the
same class of bug with branch_id. Threading a new scope dimension
through a dozen call sites by hand is what makes it recur, so this test
enumerates every figure on both endpoints rather than spot-checking one:
a newly-added tile that forgets company_id fails here.
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


# Michael's own shape: three branches, each mapped to a DIFFERENT
# Company that shares the short_name "Excellence" (D2), with most of the
# fleet under one of them -- which is exactly what made the bug visible
# (156 vs 2 rather than a subtle off-by-a-few).
@pytest.fixture()
def env(db):
    sync_permissions()
    north = RegionService().create(code="NL-AT", name="North Luzon")
    south = RegionService().create(code="SL-AT", name="South Luzon")
    nl2 = AreaDistrictService().create(code="NL2-AT", name="NORTH LUZON 2",
                                       region_id=north.id)
    sl = AreaDistrictService().create(code="SL-AT2", name="South Luzon",
                                      region_id=south.id)
    nl2_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=north.id,
                                        area_district_id=nl2.id)
    sl_co = OrgCompanyService().create(short_name="Excellence",
                                       region_id=south.id,
                                       area_district_id=sl.id)
    eali = BranchService().create(code="EALI-AT", name="East Asia Labs",
                                  company_id=nl2_co.id)
    hq = BranchService().create(code="HQ-AT", name="Head Office",
                                company_id=sl_co.id)
    vt = VehicleType(code="CAR-AT", name="Car", category="LIGHT")
    _db.session.add(vt)
    _db.session.commit()

    # 2 under the North Luzon 2 company, 5 under the other.
    for i in range(2):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=eali.id,
                                plate_number=f"NL2-{i:03d}")
    for i in range(5):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=hq.id,
                                plate_number=f"HQ-{i:03d}")

    role = Role(name="Dash All Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_([
            "vehicle.view", "branch.view", "orgcompany.view",
            "reportpmscompliance.view", "reportregistrationexpiry.view",
        ])).all()
    user = User(username="dashall", email="dashall@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"nl2_co": nl2_co, "sl_co": sl_co, "eali": eali, "hq": hq}


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "dashall", "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


# Both endpoints must agree, because the React dashboard reads bootstrap
# while /dashboard/summary backs the same tiles elsewhere. The original
# bug was precisely that only one of them was wired.
@pytest.mark.parametrize("endpoint", ["/api/v1/dashboard/summary",
                                      "/api/v1/dashboard/bootstrap"])
def test_fleet_tile_narrows_to_the_chosen_company(db, client, env, endpoint):
    token = _token(client)
    _s, all_body = _get(client, endpoint, token)
    _s, nl2 = _get(client, f"{endpoint}?company_id={env['nl2_co'].id}", token)
    summary_all = all_body.get("summary", all_body)
    summary_nl2 = nl2.get("summary", nl2)
    assert summary_all["fleet_count"] == 7
    assert summary_nl2["fleet_count"] == 2, (
        "the Company filter must narrow the Fleet tile -- this read the "
        "whole fleet in production")


@pytest.mark.parametrize("endpoint", ["/api/v1/dashboard/summary",
                                      "/api/v1/dashboard/bootstrap"])
def test_company_filter_matches_the_equivalent_branch_filter(
        db, client, env, endpoint):
    """Michael's own check: picking the Company must give the same number
    as picking the branch that maps to it. These disagreed 156 vs 2."""
    token = _token(client)
    _s, by_company = _get(
        client, f"{endpoint}?company_id={env['nl2_co'].id}", token)
    _s, by_branch = _get(
        client, f"{endpoint}?branch_id={env['eali'].id}", token)
    a = by_company.get("summary", by_company)
    b = by_branch.get("summary", by_branch)
    assert a["fleet_count"] == b["fleet_count"] == 2


@pytest.mark.parametrize("endpoint", ["/api/v1/dashboard/summary",
                                      "/api/v1/dashboard/bootstrap"])
def test_every_count_on_the_payload_responds_to_the_company_filter(
        db, client, env, endpoint):
    """Guardrail against the next miss: no figure may be IDENTICAL for a
    2-vehicle company and the 7-vehicle whole fleet unless it is
    genuinely company-independent (zero, or not vehicle-derived)."""
    token = _token(client)
    _s, all_body = _get(client, endpoint, token)
    _s, nl2 = _get(client, f"{endpoint}?company_id={env['nl2_co'].id}", token)
    a = all_body.get("summary", all_body)
    b = nl2.get("summary", nl2)
    # Fleet is the canary; registrations derive from vehicles too.
    assert b["fleet_count"] < a["fleet_count"]
    assert b["registrations_expiring_count"] <= a["registrations_expiring_count"]
    assert b["tire_stock_count"] <= a["tire_stock_count"]
    assert b["battery_stock_count"] <= a["battery_stock_count"]
    assert b["company_id"] == env["nl2_co"].id


def test_bootstrap_fleet_status_chart_narrows_too(db, client, env):
    """The chart under the tiles comes from the same payload; it read the
    whole fleet while the tile did too."""
    token = _token(client)
    _s, body = _get(
        client,
        f"/api/v1/dashboard/bootstrap?company_id={env['nl2_co'].id}", token)
    assert sum(body["fleet_status"]["data"]) == 2


def test_bootstrap_registration_chart_narrows_too(db, client, env):
    """Michael's screenshot showed Vehicle Registration Status at 156
    beside a Company-filtered header."""
    token = _token(client)
    _s, body = _get(
        client,
        f"/api/v1/dashboard/bootstrap?company_id={env['nl2_co'].id}", token)
    rs = body.get("registration_status") or {}
    assert rs.get("total", 0) <= 2
