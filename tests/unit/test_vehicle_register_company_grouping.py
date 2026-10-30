"""Vehicle Register Details groups by Company (2026-10-30).

Michael: "The report register has a branches grouping and i want to
change into company and for the additional column kindly include to the
column the Region and Area ... and group the report by Company name."

Region and Area are RESOLVED from the Company record, never stored on
the row -- the same rule Stage 3A set for the vehicle itself, so they
cannot drift from the master.

A vehicle whose Company cannot be resolved is NOT dropped: this report
is a complete register/audit trail, so it keeps an explicit
"Unassigned" group rather than silently omitting rows -- a register
that quietly loses vehicles is worse than one that shows a gap.
"""
import pytest

from app.extensions import db
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.service import VehicleService


@pytest.fixture()
def env(db):
    north = RegionService().create(code="NL-RG", name="North Luzon")
    south = RegionService().create(code="SL-RG", name="South Luzon")
    nl2 = AreaDistrictService().create(code="NL2-RG", name="NORTH LUZON 2",
                                       region_id=north.id)
    cav = AreaDistrictService().create(code="CAV-RG", name="CAVITE 2",
                                       region_id=south.id)
    nl2_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=north.id,
                                        area_district_id=nl2.id)
    cav_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=south.id,
                                        area_district_id=cav.id)
    eali = BranchService().create(code="EALI-RG", name="East Asia Labs",
                                  company_id=nl2_co.id)
    hq = BranchService().create(code="HQ-RG", name="Head Office",
                                company_id=cav_co.id)
    orphan = BranchService().create(code="ORP-RG", name="Unmapped")
    vt = VehicleType(code="CAR-RG", name="Car", category="LIGHT")
    db.session.add(vt)
    db.session.commit()
    for plate, br in (("RG-001", eali), ("RG-002", eali),
                      ("RG-003", hq), ("RG-004", orphan)):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=br.id,
                                plate_number=plate)
    return {"nl2_co": nl2_co, "cav_co": cav_co}


def _svc():
    from app.modules.master_data.vehicle.report_service import (
        VehicleRegisterReportService)
    return VehicleRegisterReportService()


def test_each_row_carries_company_region_and_area(db, env):
    rows = {r["plate_number"]: r for r in _svc().get_rows()}
    r = rows["RG-001"]
    assert r["company"] == "Excellence"
    assert r["region"] == "North Luzon"
    assert r["area_district"] == "NORTH LUZON 2"


def test_rows_resolve_company_through_the_branch(db, env):
    """No vehicle here has its own company_id -- they inherit it, which
    is the whole point of mapping branches."""
    rows = {r["plate_number"]: r for r in _svc().get_rows()}
    assert rows["RG-003"]["area_district"] == "CAVITE 2"


def test_groups_are_by_company_not_branch(db, env):
    groups = _svc().get_grouped()
    # Two mapped Companies (same short_name, different Area) + the
    # unmapped group. Branch grouping would have produced three mapped
    # groups, one per branch.
    labels = sorted(g["company_label"] for g in groups)
    assert labels == ["Excellence (North Luzon / NORTH LUZON 2)",
                      "Excellence (South Luzon / CAVITE 2)",
                      "Unassigned"]


def test_two_companies_sharing_a_short_name_stay_separate(db, env):
    """Per D2 the short_name repeats, so grouping on it alone would
    merge two distinct Companies into one section."""
    groups = {g["company_label"]: g for g in _svc().get_grouped()}
    north = groups["Excellence (North Luzon / NORTH LUZON 2)"]
    south = groups["Excellence (South Luzon / CAVITE 2)"]
    assert len(north["vehicles"]) == 2
    assert len(south["vehicles"]) == 1


def test_a_vehicle_with_no_company_is_kept_not_dropped(db, env):
    groups = {g["company_label"]: g for g in _svc().get_grouped()}
    plates = {v["plate_number"] for v in groups["Unassigned"]["vehicles"]}
    assert plates == {"RG-004"}


def test_every_vehicle_appears_exactly_once(db, env):
    """A register that loses or duplicates a vehicle is not a register."""
    groups = _svc().get_grouped()
    plates = [v["plate_number"] for g in groups for v in g["vehicles"]]
    assert sorted(plates) == ["RG-001", "RG-002", "RG-003", "RG-004"]
