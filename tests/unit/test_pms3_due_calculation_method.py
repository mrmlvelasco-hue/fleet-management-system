from datetime import date

import pytest

from app.core.maintenance.due_calculation_service import PMDueCalculationService
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-PMS3", name="PMS3 Branch")
    vt = VehicleTypeService().create(code="LV-PMS3", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS3-5K", name="5,000 KM PMS",
                                         category="PREVENTIVE")
    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      requires_approval=False, auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="PMS3-000",
        current_odometer=6000)
    return branch, vt, mt, vehicle


def test_actual_completion_method_adds_interval_to_actual_km(db, env):
    branch, vt, mt, vehicle = env
    PMScheduleService().create(
        vehicle_type_id=vt.id, maintenance_type_id=mt.id, trigger_mode="KM",
        interval_km=5000, next_due_calculation_method="ACTUAL_COMPLETION")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), odometer_at_service=5280, user=None)
    order.status = "COMPLETED"
    order.completed_date = date.today()
    from app.extensions import db as _db
    _db.session.commit()

    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["next_due_km"] == 10280  # 5280 + 5000


def test_original_schedule_method_rounds_to_nearest_interval_multiple(db, env):
    branch, vt, mt, vehicle = env
    PMScheduleService().create(
        vehicle_type_id=vt.id, maintenance_type_id=mt.id, trigger_mode="KM",
        interval_km=5000, next_due_calculation_method="ORIGINAL_SCHEDULE")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), odometer_at_service=5280, user=None)
    order.status = "COMPLETED"
    order.completed_date = date.today()
    from app.extensions import db as _db
    _db.session.commit()

    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["next_due_km"] == 10000  # next multiple of 5000, not 5280+5000


def test_default_calculation_method_is_actual_completion(db, env):
    branch, vt, mt, vehicle = env
    sched = PMScheduleService().create(
        vehicle_type_id=vt.id, maintenance_type_id=mt.id, trigger_mode="KM",
        interval_km=5000)
    assert sched.next_due_calculation_method == "ACTUAL_COMPLETION"
