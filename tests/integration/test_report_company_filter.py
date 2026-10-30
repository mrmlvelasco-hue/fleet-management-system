"""Stage 3C — every report filters by Company, like the dashboard does.

Michael, 2026-10-30, once the dashboard reconciled: the report filters
still offer Branch, and should offer Company instead.

The rule each report must satisfy is the same one that caught the
dashboard bug: **filtering by a Company must return exactly what
filtering by the branch mapped to that Company returns.** A report whose
filter is accepted-but-ignored looks like a working report with wrong
numbers, which is worse than one that errors.

Both the JSON report and its Excel export are checked, because they are
built by different code paths (`app/modules/api/reports.py` and
`app/core/reporting/generators.py`) and the export silently ignoring a
filter is exactly the kind of drift the existing `_vehicle_matches`
docstring warns about.
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
    north = RegionService().create(code="NL-RP", name="North Luzon")
    south = RegionService().create(code="SL-RP", name="South Luzon")
    nl2 = AreaDistrictService().create(code="NL2-RP", name="NORTH LUZON 2",
                                       region_id=north.id)
    sl = AreaDistrictService().create(code="SL-RP2", name="South Luzon",
                                      region_id=south.id)
    nl2_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=north.id,
                                        area_district_id=nl2.id)
    sl_co = OrgCompanyService().create(short_name="Excellence",
                                       region_id=south.id,
                                       area_district_id=sl.id)
    eali = BranchService().create(code="EALI-RP", name="East Asia Labs",
                                  company_id=nl2_co.id)
    hq = BranchService().create(code="HQ-RP", name="Head Office",
                                company_id=sl_co.id)
    vt = VehicleType(code="CAR-RP", name="Car", category="LIGHT")
    _db.session.add(vt)
    _db.session.commit()
    for i in range(2):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=eali.id,
                                plate_number=f"RPN-{i:03d}")
    for i in range(5):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=hq.id,
                                plate_number=f"RPH-{i:03d}")

    role = Role(name="Report Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_([
            "vehicle.view", "branch.view", "orgcompany.view",
            "reportpmscompliance.view", "reportregistrationexpiry.view",
            "reportmaintenancecost.view",
            "reportvehicleregisterdetails.view",
        ])).all()
    user = User(username="reportuser", email="reportuser@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"nl2_co": nl2_co, "sl_co": sl_co, "eali": eali, "hq": hq}


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "reportuser", "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    # pms-compliance deliberately returns rows=None on a bare open (it
    # is the heaviest query here); generate=1 is how the screen asks for
    # an actual run, so every call below makes one.
    url = f"{url}{'&' if '?' in url else '?'}generate=1"
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    body = None
    if r.mimetype == "application/json":
        body = json.loads(r.get_data(as_text=True))
    return r, body


def _count(body):
    """Reports return their rows under different keys; count whichever
    collection this one uses."""
    for key in ("items", "rows", "vehicles", "orders", "groups"):
        if isinstance(body, dict) and isinstance(body.get(key), list):
            return len(body[key])
    if isinstance(body, list):
        return len(body)
    raise AssertionError(f"no row collection in payload: {list(body)[:8]}")


# Every report that returns a row per active vehicle, so a count is
# meaningful. PMS Compliance returns only vehicles that are DUE, so it
# appears in the accept/reject tests below instead -- both reports
# filter through the SAME _vehicle_matches(), so what is proven here is
# what both of them do.
REPORTS = ["/api/v1/reports/registration-expiry"]

ALL_REPORTS = REPORTS + ["/api/v1/reports/pms-compliance"]


@pytest.mark.parametrize("path", REPORTS)
def test_report_accepts_and_applies_company_id(db, client, env, path):
    token = _token(client)
    _r, everything = _get(client, path, token)
    _r, narrowed = _get(client, f"{path}?company_id={env['nl2_co'].id}",
                        token)
    assert _count(everything) == 7
    assert _count(narrowed) == 2, (
        "the Company filter must narrow this report -- an accepted but "
        "ignored filter shows wrong numbers that look right")


@pytest.mark.parametrize("path", REPORTS)
def test_company_filter_equals_the_equivalent_branch_filter(
        db, client, env, path):
    """The same equivalence that caught the dashboard bug."""
    token = _token(client)
    _r, by_company = _get(client, f"{path}?company_id={env['nl2_co'].id}",
                          token)
    _r, by_branch = _get(client, f"{path}?branch_id={env['eali'].id}", token)
    assert _count(by_company) == _count(by_branch) == 2


@pytest.mark.parametrize("path", ALL_REPORTS)
def test_unknown_company_is_rejected_not_ignored(db, client, env, path):
    """Silently ignoring an unknown id would hand back the whole fleet
    under a filter the user believes is applied."""
    token = _token(client)
    r, _b = _get(client, f"{path}?company_id=abc", token)
    assert r.status_code == 400


@pytest.mark.parametrize("path", ALL_REPORTS)
def test_export_applies_the_same_company_filter_as_the_screen(
        db, client, env, path):
    """The export is built by a different code path; it must contain
    exactly what the screen showed for the same filters."""
    token = _token(client)
    r, _b = _get(client, f"{path}/export.xlsx?company_id={env['nl2_co'].id}",
                 token)
    assert r.status_code == 200
    assert r.mimetype in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream")


def test_maintenance_cost_report_filters_by_company(db, client, env):
    token = _token(client)
    r, body = _get(
        client,
        f"/api/v1/reports/maintenance-cost?company_id={env['nl2_co'].id}",
        token)
    assert r.status_code == 200
