"""Concrete demonstration of the HYBRID (whichever comes first) rule.

A HYBRID PM Template computes the KM-based due status and the calendar-
based due status INDEPENDENTLY, then reports whichever of the two is more
urgent (GOOD < DUE_SOON < OVERDUE). This means a vehicle can become due
either because it hit the KM threshold first, OR because the calendar
threshold arrived first, OR both at once — whichever gets there first
"wins" and drives the overall status, exactly matching "5,000 KM OR 6
Months, whichever comes first" from the PMS Configuration spec.
"""
from datetime import date, timedelta

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
    branch = BranchService().create(code="BR-HYBRID", name="Hybrid Demo Branch")
    vt = VehicleTypeService().create(code="LV-HYBRID", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="HYBRID-5K6M", name="5,000km / 6mo PMS",
                                         category="PREVENTIVE")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    # 5,000 KM OR 6 months (180 days), whichever comes first.
    PMScheduleService().create(
        vehicle_type_id=vt.id, maintenance_type_id=mt.id,
        trigger_mode="HYBRID", interval_km=5000, interval_days=180)
    return branch, vt, mt


def _complete_a_service(vehicle_id, mt_id, on_date, at_km):
    """Records a COMPLETED Maintenance Order — this becomes the
    'last service' baseline the whichever-comes-first calculation counts
    forward from."""
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle_id, maintenance_type_id=mt_id,
        scheduled_date=on_date, odometer_at_service=at_km, user=None)
    order.status = "COMPLETED"
    order.completed_date = on_date
    from app.extensions import db
    db.session.commit()


def test_scenario_a_high_mileage_low_time_km_triggers_first(db, env):
    """A heavily-used vehicle: last serviced 60 days ago (well within the
    180-day window) but has already driven 5,200 km since — the KM
    threshold arrives FIRST, so it's due even though barely 2 months
    have passed."""
    branch, vt, mt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch.id, conduction_number="HYBRID-A-000",
        current_odometer=5200)
    last_service_date = date.today() - timedelta(days=60)
    _complete_a_service(vehicle.id, mt.id, last_service_date, at_km=0)

    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "OVERDUE"
    assert status["next_due_km"] == 5000       # already exceeded
    assert status["next_due_date"] > date.today()  # calendar side is still fine


def test_scenario_b_low_mileage_high_time_calendar_triggers_first(db, env):
    """A rarely-driven vehicle: only 800 km since its last service (well
    under the 5,000 km threshold), but 190 days have passed — the
    CALENDAR threshold arrives FIRST, so it's due even though the
    odometer barely moved."""
    branch, vt, mt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch.id, conduction_number="HYBRID-B-000",
        current_odometer=800)
    last_service_date = date.today() - timedelta(days=190)
    _complete_a_service(vehicle.id, mt.id, last_service_date, at_km=0)

    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "OVERDUE"
    assert status["next_due_km"] == 5000
    assert status["next_due_km"] > vehicle.current_odometer  # KM side is still fine
    assert status["next_due_date"] <= date.today()  # calendar side is what tripped it


def test_scenario_c_neither_threshold_reached_yet_stays_good(db, env):
    """Low mileage AND recently serviced — neither condition triggers,
    so the overall status stays GOOD even in HYBRID mode."""
    branch, vt, mt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch.id, conduction_number="HYBRID-C-000",
        current_odometer=1000)
    last_service_date = date.today() - timedelta(days=30)
    _complete_a_service(vehicle.id, mt.id, last_service_date, at_km=0)

    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "GOOD"


def test_scenario_d_both_thresholds_reached_simultaneously_still_overdue(db, env):
    """Both conditions crossed at once — still just OVERDUE (not double-
    counted or escalated further); HYBRID never produces a status outside
    the normal GOOD/DUE_SOON/OVERDUE range."""
    branch, vt, mt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch.id, conduction_number="HYBRID-D-000",
        current_odometer=6000)
    last_service_date = date.today() - timedelta(days=200)
    _complete_a_service(vehicle.id, mt.id, last_service_date, at_km=0)

    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "OVERDUE"
