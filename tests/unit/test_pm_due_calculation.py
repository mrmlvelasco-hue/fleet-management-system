from datetime import date, timedelta

import pytest

from app.core.maintenance.due_calculation_service import (
    PMDueCalculationService)
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-DUE", name="Due Branch")
    vt = VehicleTypeService().create(code="LV-DUE", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(
        code="PMS-5K-DUE", name="5,000 KM PMS", category="PREVENTIVE")
    user = User(username="due_user", email="due@x.com", password_hash="x")
    db.session.add(user)
    db.session.commit()

    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    PMScheduleService().create(vehicle_type_id=vt.id,
                              maintenance_type_id=mt.id,
                              trigger_mode="KM", interval_km=5000)
    return vt, mt, user


def _make_vehicle(branch_id_provider, vt, odometer):
    from app.modules.master_data.org.service import BranchService
    branch = BranchService().list()[0]
    return VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number=f"DUE-{odometer}",
        current_odometer=odometer)


def test_good_status_when_far_from_due(db, env):
    vt, mt, user = env
    vehicle = _make_vehicle(None, vt, odometer=1000)
    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "GOOD"


def test_due_soon_when_within_threshold(db, env):
    vt, mt, user = env
    vehicle = _make_vehicle(None, vt, odometer=4700)  # 300km from 5000 due
    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "DUE_SOON"


def test_overdue_when_past_due_km(db, env):
    vt, mt, user = env
    vehicle = _make_vehicle(None, vt, odometer=5200)
    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "OVERDUE"


def test_due_calculation_resets_after_completed_order(db, env):
    vt, mt, user = env
    vehicle = _make_vehicle(None, vt, odometer=5200)
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), odometer_at_service=5200, user=user)
    MaintenanceOrderService().submit(order.id, user=user)
    MaintenanceOrderService().start_work(order.id)
    MaintenanceOrderService().complete(order.id, actual_cost=1000,
                                       completed_date=date.today())
    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "GOOD"
    assert status["next_due_km"] == 5200 + 5000


def test_calendar_trigger_overdue(db, env):
    vt, mt, user = env
    from app.modules.maintenance_config.service import PMScheduleService
    mt2 = MaintenanceTypeService().create(
        code="OIL-CAL", name="Oil Change", category="PREVENTIVE")
    PMScheduleService().create(vehicle_type_id=vt.id,
                               maintenance_type_id=mt2.id,
                               trigger_mode="CALENDAR", interval_days=180)
    vehicle = _make_vehicle(None, vt, odometer=100)
    status = PMDueCalculationService().get_due_status(
        vehicle, maintenance_type_id=mt2.id,
        as_of_date=date.today() + timedelta(days=200))
    assert status["status"] == "OVERDUE"


def test_get_all_due_vehicles_excludes_good(db, env):
    vt, mt, user = env
    _make_vehicle(None, vt, odometer=100)   # GOOD
    _make_vehicle(None, vt, odometer=5300)  # OVERDUE
    due_list = PMDueCalculationService().get_all_due_vehicles()
    statuses = {d["status"] for d in due_list}
    assert "OVERDUE" in statuses
    assert all(d["status"] != "GOOD" for d in due_list)
