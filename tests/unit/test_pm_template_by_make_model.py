from datetime import date

import pytest

from app.core.maintenance.due_calculation_service import PMDueCalculationService
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-MK", name="Make Branch")
    vt = VehicleTypeService().create(code="LV-MK", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS-MK", name="PMS",
                                         category="PREVENTIVE")
    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return branch, vt, mt


def test_make_model_schedule_takes_precedence_over_vehicle_type(db, env):
    branch, vt, mt = env
    # Generic vehicle-type schedule: 10,000 km
    PMScheduleService().create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                              trigger_mode="KM", interval_km=10000)
    # Specific Honda City schedule: 5,000 km (tighter interval)
    PMScheduleService().create(maintenance_type_id=mt.id, trigger_mode="KM",
                              interval_km=5000, vehicle_make="Honda",
                              vehicle_model="City")

    honda = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=branch.id, conduction_number="MK-001",
        current_odometer=5200)
    other = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="MK-002",
        current_odometer=5200)

    honda_status = PMDueCalculationService().get_due_status(honda)
    assert honda_status["status"] == "OVERDUE"  # matched the 5,000km Honda schedule
    assert honda_status["schedule"].interval_km == 5000

    other_status = PMDueCalculationService().get_due_status(other)
    assert other_status["status"] == "GOOD"  # matched the 10,000km generic schedule
    assert other_status["schedule"].interval_km == 10000


def test_vehicle_assigned_schedule_wins_over_everything(db, env):
    branch, vt, mt = env
    generic = PMScheduleService().create(
        vehicle_type_id=vt.id, maintenance_type_id=mt.id,
        trigger_mode="KM", interval_km=10000)
    make_model_match = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=8000,
        vehicle_make="Toyota", vehicle_model="Vios")
    explicit_assignment = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=3000)

    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="MK-003",
        current_odometer=3200, pm_schedule_id=explicit_assignment.id)

    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["schedule"].id == explicit_assignment.id
    assert status["status"] == "OVERDUE"  # 3200 >= 3000


def test_per_schedule_notify_before_km_override(db, env):
    branch, vt, mt = env
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=10000,
        vehicle_make="Chery", vehicle_model="Tiggo",
        notify_before_km=2000)  # wider due-soon window than the 500km default
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Chery", model="Tiggo", year=2024,
        branch_id=branch.id, conduction_number="MK-004",
        current_odometer=8500)  # 1500km away — within the 2000km custom window
    status = PMDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "DUE_SOON"


def test_scope_template_tied_to_specific_schedule(db, env):
    branch, vt, mt = env
    honda_schedule = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=10000,
        vehicle_make="Honda", vehicle_model="City")
    toyota_schedule = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=10000,
        vehicle_make="Toyota", vehicle_model="Hilux")

    honda_scope = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Honda City 10K Scope",
        pm_schedule_id=honda_schedule.id,
        items=[{"activity_code": "OIL", "activity_description": "Change Oil",
               "sort_order": 1}])
    toyota_scope = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Hilux 10K Scope",
        pm_schedule_id=toyota_schedule.id,
        items=[{"activity_code": "OIL", "activity_description": "Change Oil",
               "sort_order": 1},
              {"activity_code": "ALIGN", "activity_description": "Wheel Alignment",
               "sort_order": 2}])

    assert honda_schedule.scope_templates[0].id == honda_scope.id
    assert len(toyota_schedule.scope_templates[0].items) == 2
    assert len(honda_schedule.scope_templates[0].items) == 1
