"""New MO form's Vehicle Information Summary still showed "BRANCH /
OFFICE" with the vehicle's Branch, not its Company (Michael's bug
report, 2026-11-08 screenshot: "BRANCH / OFFICE: Head Office" on the
New Maintenance Order form).

GET /maintenance-orders/new-prefill -- which feeds that panel -- never
carried a Company field for the vehicle at all, unlike the rest of the
MO API (maintenance_order_json's own "vehicle_company", mirroring
vehicles.py's serializer: own company_id only, no branch fallback).
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import (
    AreaDistrictService, BranchService, OrgCompanyService, RegionService)
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()
    region = RegionService().create(code="R-PC", name="Region PC")
    area = AreaDistrictService().create(
        code="A-PC", name="Area PC", region_id=region.id)
    co = OrgCompanyService().create(
        short_name="Excellence", region_id=region.id, area_district_id=area.id)
    branch = BranchService().create(code="BR-PC", name="Head Office")
    vt = VehicleTypeService().create(code="LV-PC", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Camry", year=2024,
        branch_id=branch.id, company_id=co.id, conduction_number="PC-001")
    role = Role(name="pc-role")
    role.permissions = Permission.query.all()
    user = User(username="pcuser", email="pc@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()
    return {"vehicle": vehicle, "company": co}


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "pcuser", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_new_prefill_carries_the_vehicle_company(client, db, env):
    r = client.get(
        f"/api/v1/maintenance-orders/new-prefill?vehicle_id={env['vehicle'].id}",
        headers=_hdr(client))
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["vehicle"]["company"] == env["company"].short_name
