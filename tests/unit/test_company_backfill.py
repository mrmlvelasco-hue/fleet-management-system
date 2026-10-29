"""Stage 3D — backfilling company_id across masters and transactions.

The script Michael asked for (2026-10-29): having mapped each Branch to
a Company in Master Data, propagate that Company onto every
branch-scoped master and transaction record, so the data survives
Branch's retirement.

Design rules, all pinned below:
  * DRY RUN BY DEFAULT -- same convention as the fuel importer and the
    Stage 3B migration tool. Nothing is written unless asked.
  * IDEMPOTENT -- re-running changes nothing further.
  * NEVER OVERWRITES an already-set company_id. A vehicle deliberately
    assigned a Company different from its branch's (Stage 3A allows
    exactly this) must survive the backfill untouched.
  * A record under an UNMAPPED branch is reported, never guessed.
  * Transactions take the vehicle's Company where they have no branch of
    their own, because that is the org context the document belongs to.
"""
import pytest

from app.extensions import db
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.service import VehicleService


@pytest.fixture()
def env(db):
    south = RegionService().create(code="SL-BF", name="South Luzon")
    cav = AreaDistrictService().create(code="CAV-BF", name="CAVITE 2",
                                        region_id=south.id)
    sl3 = AreaDistrictService().create(code="SL3-BF", name="SOUTH LUZON 3",
                                        region_id=south.id)
    cav_co = OrgCompanyService().create(short_name="Excellence",
                                         region_id=south.id,
                                         area_district_id=cav.id)
    sl3_co = OrgCompanyService().create(short_name="Excellence",
                                         region_id=south.id,
                                         area_district_id=sl3.id)
    mapped = BranchService().create(code="BR-CAV-BF", name="Cavite Branch",
                                     company_id=cav_co.id)
    unmapped = BranchService().create(code="BR-LEG-BF", name="Legacy Branch")
    vt = VehicleType(code="CAR-BF", name="Car", category="LIGHT")
    db.session.add(vt)
    db.session.commit()

    def _veh(plate, branch, company_id=None):
        return VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
            branch_id=branch.id, plate_number=plate, company_id=company_id)

    plain = _veh("BF-001", mapped)
    overridden = _veh("BF-002", mapped, sl3_co.id)
    orphan = _veh("BF-003", unmapped)
    return {"cav_co": cav_co, "sl3_co": sl3_co, "mapped": mapped,
            "unmapped": unmapped, "plain": plain,
            "overridden": overridden, "orphan": orphan}


def _svc():
    from app.modules.master_data.org.company_backfill import (
        CompanyBackfillService)
    return CompanyBackfillService()


def test_dry_run_writes_nothing(db, env):
    report = _svc().run()
    assert report["dry_run"] is True
    db.session.expire_all()
    assert env["plain"].company_id is None


def test_dry_run_still_reports_what_it_would_do(db, env):
    """The point of a dry run is a decision-grade preview, so the counts
    must match what an apply would actually change."""
    report = _svc().run()
    assert report["tables"]["vehicles"]["would_update"] == 1


def test_apply_backfills_from_the_branch_mapping(db, env):
    _svc().run(dry_run=False)
    db.session.expire_all()
    assert env["plain"].company_id == env["cav_co"].id


def test_apply_never_overwrites_a_deliberate_override(db, env):
    """Stage 3A lets a vehicle be assigned a Company other than its
    branch's. The backfill must leave that alone and count it
    separately, on both dry run and apply."""
    _svc().run(dry_run=False)
    db.session.expire_all()
    assert env["overridden"].company_id == env["sl3_co"].id
    report = _svc().run()
    assert report["tables"]["vehicles"]["already_set"] >= 1


def test_records_under_an_unmapped_branch_are_reported_not_guessed(db, env):
    report = _svc().run(dry_run=False)
    db.session.expire_all()
    assert env["orphan"].company_id is None
    codes = {b["code"] for b in report["unmapped_branches"]}
    assert "BR-LEG-BF" in codes


def test_rerunning_changes_nothing_further(db, env):
    first = _svc().run(dry_run=False)
    second = _svc().run(dry_run=False)
    assert first["tables"]["vehicles"]["updated"] == 1
    assert second["tables"]["vehicles"]["updated"] == 0


def test_every_company_axis_table_is_covered(db, env):
    """A table silently missing from the backfill would look migrated
    while its Company stayed empty -- the kind of gap only found later,
    in production, by a report returning nothing."""
    report = _svc().run()
    assert set(report["tables"]) >= {
        "vehicles", "drivers", "departments", "tires", "batteries",
        "users", "vehicle_handover_checklists", "vehicle_checklists",
        "approval_tasks", "maintenance_orders", "vendor_companies",
    }


def test_transactions_take_their_company_from_the_vehicle(db, env):
    """A document with no branch of its own belongs to the org context of
    the vehicle it was raised against."""
    from app.modules.transactions.vehicle_checklist.models import (
        VehicleChecklist, ChecklistTemplate)
    tpl = ChecklistTemplate(name="Daily")
    db.session.add(tpl)
    db.session.commit()
    cl = VehicleChecklist(vehicle_id=env["plain"].id, template_id=tpl.id)
    db.session.add(cl)
    db.session.commit()
    _svc().run(dry_run=False)
    db.session.expire_all()
    assert cl.company_id == env["cav_co"].id
