"""Stage 3D — reconciling the Company axis against the Branch axis.

The checkpoint between backfilling Company data and letting any code
read it. For each table it answers one question: *does filtering by
Company return the same records as filtering by the Branch it came
from?* If a count disagrees, the data is wrong and nothing should switch
axis yet.

Michael has three branches and five companies, so this is small in data
terms -- but it is the only thing standing between "the backfill said it
worked" and actually knowing it did, which is exactly the gap that makes
ERP cutovers go wrong.
"""
import pytest

from app.extensions import db
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.service import VehicleService


@pytest.fixture()
def env(db):
    south = RegionService().create(code="SL-RC", name="South Luzon")
    cav = AreaDistrictService().create(code="CAV-RC", name="CAVITE 2",
                                        region_id=south.id)
    co = OrgCompanyService().create(short_name="Excellence",
                                     region_id=south.id,
                                     area_district_id=cav.id)
    mapped = BranchService().create(code="BR-RC", name="Cavite Branch",
                                     company_id=co.id)
    unmapped = BranchService().create(code="BR-RC2", name="Legacy")
    vt = VehicleType(code="CAR-RC", name="Car", category="LIGHT")
    db.session.add(vt)
    db.session.commit()
    for plate, br in (("RC-001", mapped), ("RC-002", mapped),
                      ("RC-003", unmapped)):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=br.id,
                                plate_number=plate)
    return {"co": co, "mapped": mapped, "unmapped": unmapped}


def _svc():
    from app.modules.master_data.org.company_backfill import (
        CompanyReconciliationService)
    return CompanyReconciliationService()


def test_flags_records_still_missing_a_company_before_backfill(db, env):
    report = _svc().run()
    assert report["tables"]["vehicles"]["missing_company"] == 3
    assert report["balanced"] is False


def test_balances_once_every_mappable_record_is_backfilled(db, env):
    """The two vehicles under the mapped branch reconcile; the one under
    the unmapped branch is reported as outstanding work, not as an
    imbalance -- there is no Company for it to disagree with yet."""
    from app.modules.master_data.org.company_backfill import (
        CompanyBackfillService)
    CompanyBackfillService().run(dry_run=False)
    report = _svc().run()
    assert report["tables"]["vehicles"]["missing_company"] == 1
    assert report["tables"]["vehicles"]["mismatched"] == 0
    assert report["unmapped_branches"]


def test_detects_a_company_that_disagrees_with_its_branch(db, env):
    """The failure this whole step exists to catch: a record whose
    Company is not the one its branch maps to. Legitimate for a
    deliberate Stage 3A override, which is why it is reported rather
    than 'fixed'."""
    from app.modules.master_data.org.company_backfill import (
        CompanyBackfillService)
    other = OrgCompanyService().create(
        short_name="Excellence",
        region_id=env["co"].region_id,
        area_district_id=AreaDistrictService().create(
            code="SL9-RC", name="SOUTH LUZON 9",
            region_id=env["co"].region_id).id)
    CompanyBackfillService().run(dry_run=False)
    from app.modules.master_data.vehicle.models import Vehicle
    v = Vehicle.query.filter_by(plate_number="RC-001").first()
    v.company_id = other.id
    db.session.commit()
    report = _svc().run()
    assert report["tables"]["vehicles"]["mismatched"] == 1
    assert report["balanced"] is False


def test_covers_every_table_the_backfill_writes(db, env):
    """A table the backfill fills but reconciliation never checks would
    be migrated with nobody verifying it."""
    from app.modules.master_data.org.company_backfill import (
        CompanyBackfillService)
    filled = set(CompanyBackfillService().run()["tables"])
    checked = set(_svc().run()["tables"])
    # vendor_companies is an association, counted differently; every
    # column-based table must be verified.
    assert (filled - {"vendor_companies"}) <= checked


def test_is_read_only(db, env):
    """Reconciliation must never change what it is measuring."""
    before = _svc().run()
    db.session.expire_all()
    from app.modules.master_data.vehicle.models import Vehicle
    assert all(v.company_id is None for v in Vehicle.query.all())
    assert before["tables"]["vehicles"]["missing_company"] == 3
