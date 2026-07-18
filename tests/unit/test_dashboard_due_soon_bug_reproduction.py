from datetime import date, timedelta

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
    branch = BranchService().create(code="BR-DASHBUG", name="Dashboard Bug Branch")
    vt = VehicleTypeService().create(code="LV-DASHBUG", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="DASHBUG-PM", name="Preventive Maintenance Service",
                                         category="PM")
    # A HYBRID schedule matching the Dashboard screenshot's "Next Due
    # (KM): 1000" pattern shared across several unrelated vehicles --
    # likely a Vehicle-Type-level or global schedule, not a brand-
    # specific one.
    PMScheduleService().create(
        vehicle_type_id=vt.id, maintenance_type_id=mt.id, trigger_mode="HYBRID",
        interval_km=1000, interval_days=360)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="DASHBUG-000",
        plate_number="FRD0001")
    return branch, vt, mt, vehicle


def test_completed_mo_with_blank_odometer_still_clears_due_soon_via_date(db, env):
    """Reproduces the reported bug: an MO completed today with Odometer
    at Service left BLANK (as shown in the user's print report) should
    still push the CALENDAR dimension's next_due_date forward from the
    real completion date, even though the KM dimension can't advance
    without a real reading."""
    branch, vt, mt, vehicle = env
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    order.status = "COMPLETED"
    order.completed_date = date.today()
    order.odometer_at_service = None  # left blank, exactly as reported
    db.session.commit()

    status = PMDueCalculationService().get_due_status(vehicle)
    print("STATUS:", status)
    assert status["next_due_date"] == date.today() + timedelta(days=360)
    assert status["status"] == "GOOD"


def test_completed_mo_updates_last_service_lookup_correctly(db, env):
    branch, vt, mt, vehicle = env
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    order.status = "COMPLETED"
    order.completed_date = date.today()
    order.odometer_at_service = None
    db.session.commit()

    last_km, last_date = PMDueCalculationService()._last_service(vehicle.id, mt.id)
    print("LAST SERVICE:", last_km, last_date)
    assert last_date == date.today()


def test_brand_specific_schedule_beats_generic_and_mo_must_match_its_type(db, env):
    """Tests the theory that actually explains the reported symptom: if
    a BRAND-SPECIFIC schedule exists (e.g. VEMS-imported 'Ford Escape
    Package 1', matched via the Brand+Model tier) alongside the generic
    Vehicle-Type-level one, _applicable_schedules() correctly prefers
    the brand-specific one -- but if the completed MO was created
    against a DIFFERENT MaintenanceType row than that specific schedule
    uses (even one with an identical display name), _last_service()
    will never find it, and the due status never clears."""
    branch, vt, mt, vehicle = env
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService, VehicleModelService)
    from app.modules.master_data.reference.service import MaintenanceTypeService

    ford = VehicleBrandService().create(name="FordUniqueDashBug")
    escape = VehicleModelService().create(brand_id=ford.id, name="EscapeUniqueDashBug")
    # Point the actual vehicle's free-text brand/model at these exact
    # names so the Brand+Model FK-resolution tier in
    # _resolve_vehicle_brand_model_ids() actually matches.
    vehicle.brand = "FordUniqueDashBug"
    vehicle.model = "EscapeUniqueDashBug"
    db.session.commit()
    # A SEPARATE MaintenanceType row with the same display concept but a
    # different code/id -- easy to end up with two of these in a real
    # system (one from VEMS import, one created manually with a similar
    # name) without realizing they're not actually the same record.
    mt2 = MaintenanceTypeService().create(code="DASHBUG-PM2",
                                          name="Preventive Maintenance Service",
                                          category="PM")
    PMScheduleService().create(
        vehicle_brand_id=ford.id, vehicle_model_id=escape.id,
        maintenance_type_id=mt2.id, trigger_mode="HYBRID",
        interval_km=1000, interval_days=360)

    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,  # the OTHER MaintenanceType
        scheduled_date=date.today(), user=None)
    order.status = "COMPLETED"
    order.completed_date = date.today()
    order.odometer_at_service = None
    db.session.commit()

    status = PMDueCalculationService().get_due_status(vehicle)
    print("MISMATCH STATUS:", status, "schedule mt_id:",
         status["schedule"].maintenance_type_id if status["schedule"] else None,
         "order mt_id:", order.maintenance_type_id)
    # If the theory is correct, this reproduces the bug: status stays
    # bad/GOOD-by-accident rather than genuinely reflecting the real
    # completion, because the matched schedule's maintenance_type_id
    # doesn't equal the completed order's maintenance_type_id.
