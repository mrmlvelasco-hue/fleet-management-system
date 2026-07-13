from datetime import date

import pytest

from app.core.maintenance.due_calculation_service import PMDueCalculationService
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-PMS1", name="PMS1 Branch")
    vt = VehicleTypeService().create(code="LV-PMS1", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS1-5K", name="5,000 KM PMS",
                                         category="PREVENTIVE")
    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      requires_approval=False, auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    toyota = VehicleBrandService().create(name="Toyota PMS1")
    hilux = VehicleModelService().create(brand_id=toyota.id, name="Hilux PMS1")

    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota PMS1", model="Hilux PMS1",
        year=2024, branch_id=branch.id, conduction_number="PMS1-000",
        current_odometer=4800)

    return branch, vt, mt, toyota, hilux, vehicle


def test_schedule_can_be_created_with_fk_brand_model(db, env):
    branch, vt, mt, toyota, hilux, vehicle = env
    sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id)
    assert sched.vehicle_brand_id == toyota.id
    assert sched.vehicle_model_id == hilux.id


def test_fk_based_schedule_matches_vehicle_by_brand_model(db, env):
    branch, vt, mt, toyota, hilux, vehicle = env
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id)

    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "DUE_SOON"  # 4800 within 500km of 5000
    assert status["schedule"].vehicle_brand_id == toyota.id


def test_fk_match_takes_precedence_over_free_text_match(db, env):
    branch, vt, mt, toyota, hilux, vehicle = env
    # A free-text schedule with a much looser interval...
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=50000,
        vehicle_make="Toyota PMS1", vehicle_model="Hilux PMS1")
    # ...and a tighter FK-based one for the exact same vehicle.
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id)

    status = PMDueCalculationService().get_due_status(vehicle)
    # The FK-matched (tighter) schedule should win.
    assert status["schedule"].interval_km == 5000


def test_variant_and_profile_fields_stored_correctly(db, env):
    branch, vt, mt, toyota, hilux, vehicle = env
    sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
        variant="2.8 D-4D", engine_type="1GD-FTV", fuel_type="DIESEL",
        transmission="AUTOMATIC", model_year_from=2020, model_year_to=2025,
        profile_code="HILUX-DIESEL-PMS1", profile_description="Hilux Diesel PMS",
        effective_date=date(2026, 1, 1))
    assert sched.variant == "2.8 D-4D"
    assert sched.profile_code == "HILUX-DIESEL-PMS1"
    assert sched.model_year_from == 2020
    assert sched.effective_date == date(2026, 1, 1)


def test_vehicle_master_stores_variant_engine_transmission_hours(db, env):
    branch, vt, mt, toyota, hilux, vehicle = env
    vehicle2 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota PMS1", model="Fortuner PMS1",
        year=2024, branch_id=branch.id, conduction_number="PMS1-001",
        variant="2.8 D-4D", engine_type="1GD-FTV", transmission="AUTOMATIC",
        current_engine_hours=1200)
    assert vehicle2.variant == "2.8 D-4D"
    assert vehicle2.engine_type == "1GD-FTV"
    assert vehicle2.transmission == "AUTOMATIC"
    assert vehicle2.current_engine_hours == 1200


def test_backward_compat_free_text_only_schedule_still_works(db, env):
    """No FK fields set at all — behaves exactly as before PMS-1."""
    branch, vt, mt, toyota, hilux, vehicle = env
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_make="Toyota PMS1", vehicle_model="Hilux PMS1")
    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "DUE_SOON"
