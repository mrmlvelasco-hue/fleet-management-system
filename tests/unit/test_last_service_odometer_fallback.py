from datetime import date

import pytest

from app.core.maintenance.due_calculation_service import PMDueCalculationService
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-ODOFALLBACK", name="Odo Fallback Branch")
    vt = VehicleTypeService().create(code="LV-ODOFALLBACK", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="ODOFALLBACK-MT", name="PM Fallback Test",
                                         category="PM")
    PMScheduleService().create(
        vehicle_type_id=vt.id, maintenance_type_id=mt.id, trigger_mode="KM",
        interval_km=1000)
    return branch, vt, mt


def test_reported_scenario_completed_order_without_odometer_still_clears_due(db, env):
    """Reproduces the exact reported bug: a vehicle at 1000km gets a
    Maintenance Order created and COMPLETED, but the completing user left
    Odometer at Service blank -- the vehicle still showed DUE_SOON
    afterward, as if nothing had been serviced at all. Root cause: the
    due-calculation fell back to a hardcoded 0 when the order's own
    odometer field was missing, ignoring the vehicle's actual known
    current_odometer entirely."""
    branch, vt, mt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="ODOFALLBACK-000",
        current_odometer=1000)

    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    # Completed WITHOUT odometer_at_service -- exactly matching the
    # reported PDF, which showed "Odometer: —" (blank).
    MaintenanceOrderService().complete(order.id, actual_cost=500,
                                       completed_date=date.today())

    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] != "DUE_SOON"
    assert status["status"] != "OVERDUE"
    assert status["status"] == "GOOD"


def test_fallback_uses_vehicle_current_odometer_not_hardcoded_zero(db, env):
    branch, vt, mt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="ODOFALLBACK-001",
        current_odometer=5000)
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    MaintenanceOrderService().complete(order.id, actual_cost=500,
                                       completed_date=date.today())

    status = PMDueCalculationService().get_due_status(vehicle)
    # next_due_km should be based on the vehicle's real current_odometer
    # (5000), not a hardcoded 0 -- so next due should be ~6000, not 1000.
    assert status["next_due_km"] == 6000


def test_recorded_odometer_still_takes_priority_over_vehicle_current(db, env):
    """When the order DOES have its own odometer_at_service recorded,
    that specific value should still be used (it's more precise than the
    vehicle's current reading, which may have moved since)."""
    branch, vt, mt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="ODOFALLBACK-002",
        current_odometer=5000)
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), odometer_at_service=4800, user=None)
    MaintenanceOrderService().complete(order.id, actual_cost=500,
                                       completed_date=date.today())

    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["next_due_km"] == 5800  # 4800 + 1000, not 5000 + 1000
