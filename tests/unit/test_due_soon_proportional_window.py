from datetime import date

import pytest

from app.core.maintenance.due_calculation_service import PMDueCalculationService
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-DUESOON", name="Due Soon Branch")
    vt = VehicleTypeService().create(code="LV-DUESOON", name="Light", category="LIGHT")
    return branch, vt


def _complete_order(vehicle_id, mt_id, completed_date):
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle_id, maintenance_type_id=mt_id,
        scheduled_date=completed_date, user=None)
    order.status = "COMPLETED"
    order.completed_date = completed_date
    from app.extensions import db
    db.session.commit()
    return order


def test_short_interval_schedule_does_not_show_due_soon_immediately_after_service(db, env):
    """Reproduces the exact reported bug: a 30-day-interval schedule
    showed DUE_SOON on the literal same day service was completed,
    because the default 'notify before' window (30 days) was exactly as
    wide as the interval itself -- meaning the vehicle was 'due soon'
    for its ENTIRE lifecycle between services, not just when actually
    getting close."""
    branch, vt = env
    mt = MaintenanceTypeService().create(code="DUESOON-MT", name="Due Soon Test",
                                         category="PM")
    PMScheduleService().create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                               trigger_mode="HYBRID", interval_km=1000, interval_days=30)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="DUESOON-000")
    _complete_order(vehicle.id, mt.id, date(2026, 7, 18))

    status = PMDueCalculationService().get_due_status(vehicle, as_of_date=date(2026, 7, 18))
    assert status["status"] == "GOOD"


def test_short_interval_schedule_eventually_shows_due_soon_as_it_approaches(db, env):
    """The proportional cap shouldn't just always say GOOD -- it should
    still correctly flag DUE_SOON once actually close to the (shorter,
    proportional) window."""
    branch, vt = env
    mt = MaintenanceTypeService().create(code="DUESOON-MT2", name="Due Soon Test 2",
                                         category="PM")
    PMScheduleService().create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                               trigger_mode="HYBRID", interval_km=1000, interval_days=30)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="DUESOON-001")
    _complete_order(vehicle.id, mt.id, date(2026, 7, 18))

    # 28 days later -- 2 days from the 30-day due date, well within any
    # reasonable proportional window (e.g. 10 days for a 30-day interval).
    status = PMDueCalculationService().get_due_status(vehicle, as_of_date=date(2026, 8, 15))
    assert status["status"] == "DUE_SOON"


def test_explicit_schedule_notify_before_days_always_takes_precedence(db, env):
    """An admin explicitly configuring notify_before_days on a specific
    schedule is a deliberate choice and must never be overridden by the
    proportional cap, even if it's wider than 1/3 of the interval."""
    branch, vt = env
    mt = MaintenanceTypeService().create(code="DUESOON-MT3", name="Due Soon Test 3",
                                         category="PM")
    PMScheduleService().create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                               trigger_mode="HYBRID", interval_km=1000, interval_days=30,
                               notify_before_days=25)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="DUESOON-002")
    _complete_order(vehicle.id, mt.id, date(2026, 7, 18))

    # 6 days later -- 24 days remaining, within the EXPLICIT 25-day
    # window even though it's much wider than a proportional cap would be.
    status = PMDueCalculationService().get_due_status(vehicle, as_of_date=date(2026, 7, 24))
    assert status["status"] == "DUE_SOON"


def test_long_interval_schedule_still_uses_full_default_window(db, env):
    """A long interval (e.g. 1 year) shouldn't have its default window
    shrunk unnecessarily -- the proportional cap should only kick in
    when the flat default would actually exceed a sensible fraction of
    the interval, not reduce an already-reasonable default."""
    branch, vt = env
    mt = MaintenanceTypeService().create(code="DUESOON-MT4", name="Due Soon Test 4",
                                         category="PM")
    PMScheduleService().create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                               trigger_mode="CALENDAR", interval_days=365)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="DUESOON-003")
    _complete_order(vehicle.id, mt.id, date(2026, 7, 18))

    # 340 days later -- 25 days remaining, within the default 30-day
    # window, which is well under 1/3 of a 365-day interval.
    status = PMDueCalculationService().get_due_status(vehicle, as_of_date=date(2027, 6, 23))
    assert status["status"] == "DUE_SOON"


def test_short_km_interval_does_not_show_due_soon_right_after_service(db, env):
    """Same proportional-cap fix, applied to the KM side too -- a 500km
    default 'due soon' window against a 1000km interval means the
    vehicle is flagged 'due soon' for literally half its service life."""
    branch, vt = env
    mt = MaintenanceTypeService().create(code="DUESOON-MT5", name="Due Soon Test 5",
                                         category="PM")
    PMScheduleService().create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                               trigger_mode="KM", interval_km=1000)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="DUESOON-004",
        current_odometer=600)  # 60% through a 1000km interval
    _complete_order(vehicle.id, mt.id, date(2026, 7, 18))

    status = PMDueCalculationService().get_due_status(vehicle, as_of_date=date(2026, 7, 18))
    assert status["status"] == "GOOD"
