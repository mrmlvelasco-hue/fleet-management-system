"""Dashboard "Maintenance Management" panel -- Company linkage (2026-11-02).

Reported: Michael, checking every dashboard panel for the Branch ->
Company cutover, found the Maintenance Management panel (PmCompliancePanel
on React, backed by DashboardAnalyticsService.pm_compliance()) still
showing fleet-wide figures regardless of which Company is selected --
"i think is not yet link".

Root cause, found by comparing pm_compliance() against its sibling
registration_status() (same file, same shape, sits right next to it):
registration_status already accepts and applies company_id; pm_compliance
never got the same company_id parameter, even though _chart_builders()
and the GET /dashboard/charts route already thread company_id through to
every OTHER company-aware chart. A second, independent gap: even if the
service accepted it, the route itself never read company_id off the
request at all for /dashboard/charts, and the React chart() helper never
sent it -- three missed call sites for the same feature.
"""
import json

import pytest

from app.core.dashboard_analytics_service import DashboardAnalyticsService
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import (
    BranchService, OrgCompanyService, RegionService, AreaDistrictService)
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


def _company(suffix):
    region = RegionService().create(code=f"RG-PMC{suffix}",
                                    name=f"Region PMC {suffix}")
    area = AreaDistrictService().create(code=f"AD-PMC{suffix}",
                                        name=f"Area PMC {suffix}",
                                        region_id=region.id)
    return OrgCompanyService().create(short_name=f"Co PMC-{suffix}",
                                      region_id=region.id,
                                      area_district_id=area.id)


@pytest.fixture()
def fleet(db):
    sync_permissions()
    company_a = _company("A")
    company_b = _company("B")
    vt = VehicleTypeService().create(code="LV-PMC", name="Light",
                                     category="LIGHT")
    branch_a = BranchService().create(code="BR-PMC-A", name="Branch A",
                                      company_id=company_a.id)
    branch_b = BranchService().create(code="BR-PMC-B", name="Branch B",
                                      company_id=company_b.id)
    v1 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Hilux", year=2022,
                                 branch_id=branch_a.id,
                                 conduction_number="PMC-1")
    v2 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Hilux", year=2022,
                                 branch_id=branch_a.id,
                                 conduction_number="PMC-2")
    v3 = VehicleService().create(vehicle_type_id=vt.id, brand="Ford",
                                 model="Escape", year=2021,
                                 branch_id=branch_b.id,
                                 conduction_number="PMC-3")
    role = Role(name="pmc-role")
    role.permissions = Permission.query.all()
    user = User(username="pmcuser", email="pmc@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()
    return company_a, company_b, v1, v2, v3


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "pmcuser", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_pm_compliance_with_no_company_id_counts_the_whole_fleet(db, fleet):
    result = DashboardAnalyticsService().pm_compliance()
    counts = dict(zip(result["labels"], result["data"]))
    assert counts["Good"] == 3


def test_pm_compliance_filtered_by_company_id_counts_only_that_company(
        db, fleet):
    company_a, company_b, v1, v2, v3 = fleet
    result = DashboardAnalyticsService().pm_compliance(company_id=company_a.id)
    counts = dict(zip(result["labels"], result["data"]))
    assert counts["Good"] == 2


def test_dashboard_charts_route_honours_company_id_for_pm_compliance(
        client, db, fleet):
    company_a, company_b, v1, v2, v3 = fleet
    hdr = _hdr(client)
    r = client.get(f"/api/v1/dashboard/charts?only=pm_compliance"
                   f"&company_id={company_a.id}", headers=hdr)
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    counts = dict(zip(body["pm_compliance"]["labels"],
                      body["pm_compliance"]["data"]))
    assert counts["Good"] == 2
