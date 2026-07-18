from datetime import date

import pytest

from app.core.maintenance.due_calculation_service import PMDueCalculationService
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.org.service import BranchService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-LEGACY", name="Legacy Vehicle Branch")
    vt = VehicleTypeService().create(code="LV-LEGACY", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="LEGACY-MT", name="Legacy PM Test",
                                         category="PM")
    PMScheduleService().create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                               trigger_mode="KM", interval_km=5000)
    return branch, vt, mt


def test_legacy_vehicle_without_baseline_shows_overdue_immediately(db, env):
    """Confirms the reported bug: a legacy vehicle manually registered
    with a real current_odometer but no service baseline captured shows
    OVERDUE immediately, since there's no completed Maintenance Order to
    establish when it was last actually serviced."""
    branch, vt, mt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2020,
        branch_id=branch.id, conduction_number="LEGACY-000",
        current_odometer=48000)  # a real, well-used legacy vehicle
    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "OVERDUE"  # confirms the bug exists pre-fix


def test_legacy_vehicle_with_baseline_uses_it_instead_of_zero(db, env):
    """With a captured legacy baseline (last known PM odometer/date),
    due-calculation should treat THAT as the starting point, not assume
    the vehicle has never been serviced at all."""
    branch, vt, mt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2020,
        branch_id=branch.id, conduction_number="LEGACY-001",
        current_odometer=48000,
        last_pm_odometer=47000, last_pm_date=date(2026, 7, 1))
    status = PMDueCalculationService().get_due_status(vehicle,
                                                      as_of_date=date(2026, 7, 18))
    # last_pm_odometer(47000) + interval_km(5000) = 52000; vehicle is at
    # 48000 -- still well within the interval, not due at all.
    assert status["status"] == "GOOD"
    assert status["next_due_km"] == 52000


def test_legacy_baseline_ignored_once_a_real_completed_order_exists(db, env):
    """The legacy baseline is only a stand-in until a REAL Maintenance
    Order is completed in this system -- once that happens, the actual
    completion record should take over as the source of truth."""
    branch, vt, mt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2020,
        branch_id=branch.id, conduction_number="LEGACY-002",
        current_odometer=48000,
        last_pm_odometer=10000, last_pm_date=date(2020, 1, 1))  # very stale

    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date(2026, 7, 1), odometer_at_service=47500, user=None)
    order.status = "COMPLETED"
    order.completed_date = date(2026, 7, 1)
    from app.extensions import db as _db
    _db.session.commit()

    status = PMDueCalculationService().get_due_status(vehicle,
                                                      as_of_date=date(2026, 7, 18))
    # Real completion (47500 + 5000 = 52500) takes precedence over the
    # stale legacy baseline (10000 + 5000 = 15000, which would show
    # OVERDUE).
    assert status["next_due_km"] == 52500
    assert status["status"] == "GOOD"
