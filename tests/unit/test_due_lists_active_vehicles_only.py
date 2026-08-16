"""Due for Maintenance / Due for Registration: ACTIVE vehicles only.

Both due calculations previously included every vehicle that wasn't
DISPOSED, which let INACTIVE and IN_REPAIR units through. That produces
work nobody can act on: raising a PM order or an LTO renewal against a
unit that is off the road wastes the approver's time and inflates the
due counts the dashboard is judged by.

DISPOSED was already excluded. This narrows it to ACTIVE only.
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


def test_registration_due_excludes_in_repair_vehicles(app, db, fleet):
    """A unit off the road for repair isn't one someone should be sent
    to the LTO for."""
    assert fleet["IN_REPAIR"].plate_number not in _registration_due_plates()


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


def test_pm_due_never_includes_non_active_vehicles(app, db, fleet):
    """Whatever the PM schedules happen to produce, no vehicle that is
    not ACTIVE may appear."""
    plates = _pm_due_plates()
    for status in ("INACTIVE", "IN_REPAIR", "DISPOSED"):
        assert fleet[status].plate_number not in plates, (
            f"{status} vehicle appeared in the PM due list")


def test_pm_due_query_filters_on_active_status(app, db, fleet):
    """Asserted at the source too: the PM path builds its vehicle query
    separately from the registration one, so a fix to either does not
    imply the other."""
    from pathlib import Path
    import app.core.maintenance.due_calculation_service as mod
    src = Path(mod.__file__).read_text()
    assert 'Vehicle.status == "ACTIVE"' in src
