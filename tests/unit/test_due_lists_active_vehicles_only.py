"""Due for Maintenance / Due for Registration: which statuses qualify.

Both due calculations previously included every vehicle that wasn't
DISPOSED, which let INACTIVE units through -- a vehicle taken out of
service is not one to raise a PM order or an LTO renewal against.

IN_REPAIR is deliberately IN. A vehicle sitting in the shop is exactly
one that maintenance and registration still apply to; excluding it
would hide work that is actively in hand. (An earlier pass had it
excluded; the client corrected that, and these tests now pin the
intended behaviour so it cannot drift back.)
"""
import pytest
from datetime import date, timedelta

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def admin(app, db):
    from app.modules.user_management.models import User
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    return User.query.filter_by(username="admin").first()


@pytest.fixture()
def fleet(app, db, admin):
    """One vehicle per status, all equally overdue, so the ONLY thing
    that can differ between them is the status filter."""
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.extensions import db as _db

    vt = VehicleTypeService().create(code="VT-ST", name="Truck",
                                     category="HEAVY")
    br = BranchService().create(code="BR-ST", name="Carmona")
    made = {}
    for i, status in enumerate(
            ["ACTIVE", "INACTIVE", "IN_REPAIR", "DISPOSED"]):
        v = VehicleService().create(
            vehicle_type_id=vt.id, branch_id=br.id, brand="Isuzu",
            model="Elf", year=2018, plate_number=f"ST-{i}000",
            conduction_number=f"CN-ST{i}",
            acquisition_date=date(2018, 1, 1))
        v.status = status
        made[status] = v
    _db.session.commit()
    return made


# ── Registration ────────────────────────────────────────────────────

def _registration_due_plates(**kw):
    from app.modules.registration_config.service import (
        RegistrationDueCalculationService)
    rows = RegistrationDueCalculationService().get_all_due_vehicles(
        statuses=("NO_RECORD", "DUE_SOON", "OVERDUE"), **kw)
    return {r["vehicle"].plate_number for r in rows}


def test_registration_due_includes_active_vehicles(app, db, fleet):
    assert fleet["ACTIVE"].plate_number in _registration_due_plates()


def test_registration_due_excludes_inactive_vehicles(app, db, fleet):
    assert fleet["INACTIVE"].plate_number not in _registration_due_plates()


def test_registration_due_includes_in_repair_vehicles(app, db, fleet):
    """A vehicle in the shop still has an LTO expiry date, and its
    registration still has to be renewed on time."""
    assert fleet["IN_REPAIR"].plate_number in _registration_due_plates()


def test_registration_due_still_excludes_disposed(app, db, fleet):
    assert fleet["DISPOSED"].plate_number not in _registration_due_plates()


def test_registration_due_excludes_soft_deleted(app, db, fleet):
    """is_active is the soft-delete flag and is separate from the
    business status -- a deleted record must stay out regardless."""
    fleet["ACTIVE"].is_active = False
    db.session.commit()
    assert fleet["ACTIVE"].plate_number not in _registration_due_plates()


# ── Preventive maintenance ──────────────────────────────────────────

def _pm_due_plates():
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService)
    rows = PMDueCalculationService().get_all_due_vehicles()
    out = set()
    for r in rows:
        v = r.get("vehicle") if isinstance(r, dict) else None
        if v is not None:
            out.add(v.plate_number)
    return out


def test_pm_due_never_includes_retired_or_out_of_service_vehicles(
        app, db, fleet):
    """Whatever the PM schedules happen to produce, a DISPOSED or
    INACTIVE vehicle may never appear."""
    plates = _pm_due_plates()
    for status in ("INACTIVE", "DISPOSED"):
        assert fleet[status].plate_number not in plates, (
            f"{status} vehicle appeared in the PM due list")


def test_both_calculations_share_one_eligible_status_set(app, db):
    """The PM and Registration paths build their vehicle queries
    separately, so without a shared constant a change to one silently
    leaves the other behind."""
    from app.core.maintenance.due_calculation_service import (
        DUE_ELIGIBLE_STATUSES)
    assert set(DUE_ELIGIBLE_STATUSES) == {"ACTIVE", "IN_REPAIR"}
    from pathlib import Path
    import app.modules.registration_config.service as reg
    assert "DUE_ELIGIBLE_STATUSES" in Path(reg.__file__).read_text()
