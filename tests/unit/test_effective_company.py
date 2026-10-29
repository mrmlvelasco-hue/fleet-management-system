"""Stage 3C — a record's EFFECTIVE Company, and filtering by it.

Michael's fleet has no per-vehicle Company data: `Vehicle.company_id` is
nullable and only the Stage 3B migration tool writes it. Rather than
block the whole Company axis on that migration, a record's effective
Company resolves through its branch:

    effective company = own company_id, else branch.company_id

So mapping a handful of legacy branches to Companies (v280) immediately
gives every vehicle, tire, battery and driver under them a Company,
with no per-record backfill. A vehicle explicitly assigned its own
Company still wins -- that per-vehicle override is what Stage 3A added
the column for, and the migration tool already refuses to overwrite it.

The predicate lives in ONE place (app/core/company_resolution.py) and is
reused by every service that filters by Company, so the dashboard count
and the list screen can never disagree about what "belongs to" means.
"""
import pytest

from app.extensions import db
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import VehicleService


@pytest.fixture()
def env(db):
    from app.modules.master_data.reference.models import VehicleType
    south = RegionService().create(code="SL-EC", name="South Luzon")
    cav = AreaDistrictService().create(code="CAV-EC", name="CAVITE 2",
                                       region_id=south.id)
    sl3 = AreaDistrictService().create(code="SL3-EC", name="SOUTH LUZON 3",
                                       region_id=south.id)
    cav_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=south.id,
                                        area_district_id=cav.id)
    sl3_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=south.id,
                                        area_district_id=sl3.id)
    mapped = BranchService().create(code="BR-CAV", name="Cavite Branch",
                                    company_id=cav_co.id)
    unmapped = BranchService().create(code="BR-NONE", name="Legacy Branch")
    vt = VehicleType(code="CAR-EC", name="Car", category="LIGHT")
    db.session.add(vt)
    db.session.commit()

    def _veh(plate, branch, company_id=None):
        return VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
            branch_id=branch.id, plate_number=plate, company_id=company_id)

    inherits = _veh("INH-001", mapped)                 # via branch
    override = _veh("OVR-001", mapped, sl3_co.id)      # own company wins
    orphan = _veh("ORP-001", unmapped)                 # no company at all
    return {"cav_co": cav_co, "sl3_co": sl3_co, "mapped": mapped,
            "unmapped": unmapped, "inherits": inherits,
            "override": override, "orphan": orphan}


def test_vehicle_inherits_company_from_its_branch(db, env):
    from app.core.company_resolution import effective_company_id
    assert effective_company_id(env["inherits"]) == env["cav_co"].id


def test_own_company_overrides_the_branch(db, env):
    """Stage 3A deliberately allows a per-vehicle Company that differs
    from its branch's; that override must not be undone by the
    fallback."""
    from app.core.company_resolution import effective_company_id
    assert effective_company_id(env["override"]) == env["sl3_co"].id


def test_unmapped_branch_yields_no_company(db, env):
    """A legacy branch nobody has mapped is the normal starting state --
    it resolves to None, never raises."""
    from app.core.company_resolution import effective_company_id
    assert effective_company_id(env["orphan"]) is None


def test_filter_matches_inherited_and_overridden_in_sql(db, env):
    """The same rule as a SQL predicate, so list screens and dashboard
    counts filter identically."""
    from app.core.company_resolution import company_filter
    plates = {v.plate_number for v in
              Vehicle.query.filter(company_filter(Vehicle,
                                                  env["cav_co"].id)).all()}
    assert plates == {"INH-001"}       # not OVR-001: it was overridden away
    plates3 = {v.plate_number for v in
               Vehicle.query.filter(company_filter(Vehicle,
                                                   env["sl3_co"].id)).all()}
    assert plates3 == {"OVR-001"}


def test_vehicle_service_list_filters_by_company(db, env):
    plates = {v.plate_number for v in
              VehicleService().list(company_id=env["cav_co"].id)}
    assert plates == {"INH-001"}


def test_company_filter_is_inert_when_no_company_given(db, env):
    """No Company selected means no narrowing -- the existing behaviour
    of every caller that does not pass one."""
    assert len(VehicleService().list()) == 3
    assert len(VehicleService().list(company_id=None)) == 3
