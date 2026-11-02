"""Fuel Company filter (2026-11-02).

Reported: Michael has been going module-by-module confirming every
screen filters by Company (Stage 3D/3C) instead of the retired Branch,
and Fuel is the one he found still showing a Branch dropdown.

Root cause: FuelTransaction carries no branch_id of its own -- it is
always scoped through the vehicle it was raised against, the same
"follow the vehicle" rule every branch-scoped transaction uses when it
has no branch field of its own (see CompanyBackfillService.
_by_branch_then_vehicle). Every OTHER list already resolves Company the
same way, through company_filter(Vehicle, company_id); Fuel's list
endpoint (GET /api/v1/fuel) and its KPI summary (FuelAnalyticsService.
summary()) were simply never given the company_id half of that -- only
branch_id, left from before the Company axis existed.

This adds `company_id` as an explicit, additional filter on both,
alongside (not replacing) the existing branch_id-based user-access
scope check (fuel_scope_ids), which is a separate, pre-existing concern
left untouched here.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import (
    BranchService, OrgCompanyService, RegionService, AreaDistrictService)
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


def _company(suffix):
    region = RegionService().create(code=f"RG-FCF{suffix}",
                                    name=f"Region FCF {suffix}")
    area = AreaDistrictService().create(code=f"AD-FCF{suffix}",
                                        name=f"Area FCF {suffix}",
                                        region_id=region.id)
    return OrgCompanyService().create(short_name=f"Co FCF-{suffix}",
                                      region_id=region.id,
                                      area_district_id=area.id)


@pytest.fixture()
def env(db):
    sync_permissions()
    company_a = _company("A")
    company_b = _company("B")
    branch_a = BranchService().create(code="BR-FCF-A", name="Branch A",
                                      company_id=company_a.id)
    branch_b = BranchService().create(code="BR-FCF-B", name="Branch B",
                                      company_id=company_b.id)
    vt = VehicleTypeService().create(code="LV-FCF", name="Light",
                                     category="LIGHT")
    vehicle_a = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch_a.id, conduction_number="FCF-A-1")
    vehicle_b = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="D-Max", year=2022,
        branch_id=branch_b.id, conduction_number="FCF-B-1")

    role = Role(name="fcf-role")
    role.permissions = Permission.query.all()
    user = User(username="fcfuser", email="fcf@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()

    from app.modules.transactions.fuel.models import FuelTransaction
    from datetime import datetime
    txn_a = FuelTransaction(
        vehicle_id=vehicle_a.id, transaction_date=datetime(2026, 11, 1, 9, 0),
        station="Petron A", fuel_type="DIESEL", litres=40, total_amount=2400)
    txn_b = FuelTransaction(
        vehicle_id=vehicle_b.id, transaction_date=datetime(2026, 11, 1, 10, 0),
        station="Petron B", fuel_type="DIESEL", litres=30, total_amount=1800)
    db.session.add_all([txn_a, txn_b])
    db.session.commit()

    return {"company_a": company_a, "company_b": company_b,
           "vehicle_a": vehicle_a, "vehicle_b": vehicle_b}


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "fcfuser", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_fuel_list_with_no_company_id_returns_every_company(client, db, env):
    hdr = _hdr(client)
    r = client.get("/api/v1/fuel", headers=hdr)
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["total"] == 2


def test_fuel_list_filtered_by_company_id_returns_only_that_companys_fills(
        client, db, env):
    hdr = _hdr(client)
    r = client.get(f"/api/v1/fuel?company_id={env['company_a'].id}",
                   headers=hdr)
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["total"] == 1
    assert body["items"][0]["station"] == "Petron A"


def test_fuel_summary_tiles_match_the_company_filtered_list(client, db, env):
    """The regression this project already fixed once for branch_id
    (summary() previously ignored it entirely, so the KPI tiles beside a
    branch-filtered list kept showing the whole fleet's figures) -- the
    same must hold for company_id, or Company-filtering the list will
    silently reopen it."""
    hdr = _hdr(client)
    r = client.get(f"/api/v1/fuel?company_id={env['company_a'].id}",
                   headers=hdr)
    body = json.loads(r.get_data(as_text=True))
    assert body["summary"]["fills"] == 1
    assert body["summary"]["litres"] == 40.0


def test_unknown_company_id_yields_no_rows_rather_than_erroring(client, db, env):
    hdr = _hdr(client)
    r = client.get("/api/v1/fuel?company_id=999999", headers=hdr)
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["total"] == 0


def test_fuel_export_filtered_by_company_id_contains_only_that_companys_rows(
        client, db, env):
    hdr = _hdr(client)
    r = client.get(f"/api/v1/fuel/export.xlsx?company_id={env['company_a'].id}",
                   headers=hdr)
    assert r.status_code == 200
    from io import BytesIO
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(r.data))
    ws = wb.active
    stations = [row[0].value for row in ws.iter_rows(min_row=2)
               if row[0].value]
    # Whatever column layout export_fuel uses, "Petron B" must not
    # appear anywhere on a sheet filtered to Company A.
    all_text = " ".join(str(c.value) for row in ws.iter_rows(min_row=2)
                        for c in row if c.value is not None)
    assert "Petron A" in all_text
    assert "Petron B" not in all_text


def test_fuel_analysis_filtered_by_company_id_lists_only_that_companys_vehicles(
        client, db, env):
    hdr = _hdr(client)
    r = client.get(f"/api/v1/fuel/analysis?company_id={env['company_a'].id}"
                   "&year=2026", headers=hdr)
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    vehicle_ids = {row["vehicle_id"] for row in body["rows"]}
    assert vehicle_ids == {env["vehicle_a"].id}
