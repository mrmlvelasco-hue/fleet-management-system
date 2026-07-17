import pytest

from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService)
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-SCOPEFILTER", name="Scope Filter Branch")
    vt = VehicleTypeService().create(code="LV-SCOPEFILTER", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="SCOPEFILTER-MT", name="PM Test",
                                         category="PM")
    ford = VehicleBrandService().create(name="Ford ScopeFilter")
    escape = VehicleModelService().create(brand_id=ford.id, name="Escape ScopeFilter")
    honda = VehicleBrandService().create(name="Honda ScopeFilter")
    city = VehicleModelService().create(brand_id=honda.id, name="City ScopeFilter")

    ford_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford ScopeFilter", model="Escape ScopeFilter",
        year=2024, branch_id=branch.id, conduction_number="SCOPEFILTER-000")

    # Ford Escape gets 2 sequential packages (matching the reported
    # scenario's "Package 1/2/3/4" pattern)
    ford_sched1 = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=1000,
        vehicle_brand_id=ford.id, vehicle_model_id=escape.id,
        profile_code="FORD-ESCAPE-PROFILE", sequence_position=1)
    ford_sched2 = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=2000,
        vehicle_brand_id=ford.id, vehicle_model_id=escape.id,
        profile_code="FORD-ESCAPE-PROFILE", sequence_position=2)
    PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Ford Escape Package 1",
        pm_schedule_id=ford_sched1.id,
        items=[{"activity_code": "A1", "activity_description": "First service",
               "sort_order": 1}])
    PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Ford Escape Package 2",
        pm_schedule_id=ford_sched2.id,
        items=[{"activity_code": "A2", "activity_description": "Second service",
               "sort_order": 1}])

    # A completely unrelated Honda City template that must NOT show up
    # for the Ford vehicle.
    honda_sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=10000,
        vehicle_brand_id=honda.id, vehicle_model_id=city.id)
    PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Honda City 10,000 KM PMS",
        pm_schedule_id=honda_sched.id,
        items=[{"activity_code": "H1", "activity_description": "Honda service",
               "sort_order": 1}])

    return branch, vt, mt, ford_vehicle, ford_sched1, ford_sched2


def test_scope_templates_filtered_by_vehicle_brand_model(db, env):
    """Reproduces the exact reported bug: selecting a Ford Escape must
    only show Ford Escape scope templates, not an unrelated Honda City
    one."""
    branch, vt, mt, vehicle, sched1, sched2 = env
    results = PMScopeTemplateService().list_applicable_for_vehicle(
        vehicle, maintenance_type_id=mt.id)
    names = [r.name for r in results]
    assert "Ford Escape Package 1" in names
    assert "Ford Escape Package 2" in names
    assert "Honda City 10,000 KM PMS" not in names


def test_scope_templates_filtered_by_maintenance_type_too(db, env):
    branch, vt, mt, vehicle, sched1, sched2 = env
    mt2 = MaintenanceTypeService().create(code="SCOPEFILTER-MT2", name="Different Type",
                                          category="CM")
    results = PMScopeTemplateService().list_applicable_for_vehicle(
        vehicle, maintenance_type_id=mt2.id)
    assert results == []


def test_no_maintenance_type_filter_still_excludes_other_vehicles(db, env):
    branch, vt, mt, vehicle, sched1, sched2 = env
    results = PMScopeTemplateService().list_applicable_for_vehicle(vehicle)
    names = [r.name for r in results]
    assert "Honda City 10,000 KM PMS" not in names


def test_next_due_schedule_identified_correctly(db, env):
    """The 'which package is actually next due' logic -- a vehicle at
    1500km (past Package 1's 1000km threshold, not yet at Package 2's
    2000km) should identify Package 1 as due."""
    branch, vt, mt, vehicle, sched1, sched2 = env
    vehicle.current_odometer = 1500
    db.session.commit()

    due_template = PMScopeTemplateService().get_next_due_scope_template(
        vehicle, maintenance_type_id=mt.id)
    assert due_template is not None
    assert due_template.name == "Ford Escape Package 1"
