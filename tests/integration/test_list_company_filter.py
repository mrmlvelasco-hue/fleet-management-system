"""Stage 3C — every module and transaction list filters by Company.

Michael, 2026-10-30: "please proceed in the next update for the company
filter we need to apply this in the system."

Enumerated rather than sampled, deliberately. The two Company bugs so
far were both a MISSED call site, not broken logic: the Master Data
Branch screen never got the field because a second Branch UI existed,
and the dashboard read the whole fleet because /dashboard/bootstrap was
never wired while /dashboard/summary was. Listing every endpoint here
means the next addition that forgets company_id fails in CI.

Every transaction list pages through BaseTransactionService.
list_filtered, so they share one implementation -- but they are still
each asserted, because sharing an implementation is exactly what made
the last miss invisible.
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
    north = RegionService().create(code="NL-LF", name="North Luzon")
    south = RegionService().create(code="SL-LF", name="South Luzon")
    nl2 = AreaDistrictService().create(code="NL2-LF", name="NORTH LUZON 2",
                                       region_id=north.id)
    sl = AreaDistrictService().create(code="SL-LF2", name="South Luzon",
                                      region_id=south.id)
    # 2026-11-16: a filter by Company now covers every Region/Area row of
    # the SAME company (same short name AND company name). These two are
    # deliberately two DIFFERENT companies that share a short name, so
    # the tests below still prove one company's records are kept apart
    # from another's.
    nl2_co = OrgCompanyService().create(short_name="Excellence",
                                        company_name="Excellence Farms Inc",
                                        region_id=north.id,
                                        area_district_id=nl2.id)
    sl_co = OrgCompanyService().create(short_name="Excellence",
                                       company_name="Excellence Trading Corp",
                                       region_id=south.id,
                                       area_district_id=sl.id)
    eali = BranchService().create(code="EALI-LF", name="East Asia Labs",
                                  company_id=nl2_co.id)
    hq = BranchService().create(code="HQ-LF", name="Head Office",
                                company_id=sl_co.id)
    vt = VehicleType(code="CAR-LF", name="Car", category="LIGHT")
    _db.session.add(vt)
    _db.session.commit()
    for i in range(2):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=eali.id,
                                plate_number=f"LFN-{i:03d}")
    for i in range(5):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=hq.id,
                                plate_number=f"LFH-{i:03d}")

    role = Role(name="List Role")
    role.permissions = Permission.query.all()   # every list, one account
    user = User(username="listuser", email="listuser@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"nl2_co": nl2_co, "sl_co": sl_co, "eali": eali, "hq": hq}


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "listuser", "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    body = (json.loads(r.get_data(as_text=True))
            if r.mimetype == "application/json" else None)
    return r, body


# Every list that should narrow by Company. Transaction lists carry no
# rows in this fixture, so for them the assertion is that the parameter
# is ACCEPTED and the response stays well-formed -- a 400 or a 500 here
# means an endpoint that silently never got wired.
VEHICLE_BACKED_LISTS = [
    "/api/v1/vehicles",
]

ALL_LISTS = VEHICLE_BACKED_LISTS + [
    "/api/v1/drivers",
    "/api/v1/tires",
    "/api/v1/batteries",
    "/api/v1/maintenance-orders",
    "/api/v1/purchase-requests",
    "/api/v1/atd",
    "/api/v1/tire-transactions",
    "/api/v1/battery-transactions",
    "/api/v1/trip-tickets",
    "/api/v1/vehicle-movements",
    "/api/v1/vehicle-registrations",
]


@pytest.mark.parametrize("path", ALL_LISTS)
def test_list_accepts_company_id_without_breaking(db, client, env, path):
    token = _token(client)
    r, body = _get(client, f"{path}?company_id={env['nl2_co'].id}", token)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    assert isinstance(body, dict) and "items" in body


@pytest.mark.parametrize("path", VEHICLE_BACKED_LISTS)
def test_list_narrows_to_the_company(db, client, env, path):
    token = _token(client)
    _r, everything = _get(client, f"{path}?page_size=100", token)
    _r, narrowed = _get(
        client, f"{path}?page_size=100&company_id={env['nl2_co'].id}", token)
    assert everything["total"] == 7
    assert narrowed["total"] == 2


@pytest.mark.parametrize("path", VEHICLE_BACKED_LISTS)
def test_company_filter_equals_the_equivalent_branch_filter(
        db, client, env, path):
    """The rule that caught the dashboard bug, applied to the lists."""
    token = _token(client)
    _r, by_company = _get(
        client, f"{path}?page_size=100&company_id={env['nl2_co'].id}", token)
    _r, by_branch = _get(
        client, f"{path}?page_size=100&branch_id={env['eali'].id}", token)
    assert by_company["total"] == by_branch["total"] == 2


@pytest.mark.parametrize("path", ALL_LISTS)
def test_a_non_integer_company_is_rejected_not_ignored(db, client, env, path):
    """Silently ignoring a bad id hands back everything under a filter
    the user believes is applied."""
    token = _token(client)
    r, _b = _get(client, f"{path}?company_id=abc", token)
    assert r.status_code in (400, 422), f"{path} accepted a bad company_id"


def test_the_other_companys_records_are_excluded_not_merely_reordered(
        db, client, env):
    token = _token(client)
    _r, body = _get(
        client,
        f"/api/v1/vehicles?page_size=100&company_id={env['nl2_co'].id}",
        token)
    plates = {v["plate_number"] for v in body["items"]}
    assert plates == {"LFN-000", "LFN-001"}


def test_list_filter_covers_every_area_of_the_same_company(db, client, env):
    """2026-11-16: the filter lists each company once, so choosing it has
    to bring back the records of ALL its Region/Area rows -- here a
    second area of "Excellence Farms Inc" holding one more vehicle."""
    north = env["nl2_co"].region
    nl3 = AreaDistrictService().create(code="NL3-LF", name="NORTH LUZON 3",
                                       region_id=north.id)
    second_area = OrgCompanyService().create(
        short_name="Excellence", company_name="Excellence Farms Inc",
        region_id=north.id, area_district_id=nl3.id)
    vt = VehicleType.query.filter_by(code="CAR-LF").one()
    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                            model="Hilux", year=2024, branch_id=env["eali"].id,
                            company_id=second_area.id, plate_number="LFN-900")

    token = _token(client)
    for cid in (env["nl2_co"].id, second_area.id):
        _r, body = _get(
            client, f"/api/v1/vehicles?page_size=100&company_id={cid}", token)
        assert body["total"] == 3            # LFN-000, LFN-001, LFN-900
        assert {v["plate_number"] for v in body["items"]} == {
            "LFN-000", "LFN-001", "LFN-900"}

    # The other company is still kept apart.
    _r, other = _get(
        client,
        f"/api/v1/vehicles?page_size=100&company_id={env['sl_co'].id}", token)
    assert other["total"] == 5
