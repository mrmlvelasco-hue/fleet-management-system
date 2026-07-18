import pytest

from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.master_data.reference.service import VehicleTypeService


@pytest.fixture()
def env(db):
    vt = VehicleTypeService().create(code="LV-PMFILTER", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMFILTER-MT", name="PM Filter Test",
                                         category="PM")
    return vt, mt


def test_filters_by_brand_and_model_text_match(db, env):
    vt, mt = env
    ford_sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=1000,
        vehicle_make="Ford", vehicle_model="Escape")
    honda_sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_make="Honda", vehicle_model="City")

    results = PMScheduleService().list_applicable_for_criteria(
        brand_name="Ford", model_name="Escape")
    ids = [r.id for r in results]
    assert ford_sched.id in ids
    assert honda_sched.id not in ids


def test_falls_back_to_vehicle_type_when_no_brand_model_match(db, env):
    vt, mt = env
    type_sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=2000,
        vehicle_type_id=vt.id)

    results = PMScheduleService().list_applicable_for_criteria(
        brand_name="SomeUnknownBrand", model_name="SomeUnknownModel",
        vehicle_type_id=vt.id)
    assert type_sched.id in [r.id for r in results]


def test_filters_by_maintenance_type_too(db, env):
    vt, mt = env
    mt2 = MaintenanceTypeService().create(code="PMFILTER-MT2", name="Different Type",
                                          category="CM")
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=1000,
        vehicle_make="Ford", vehicle_model="Escape")
    results = PMScheduleService().list_applicable_for_criteria(
        brand_name="Ford", model_name="Escape", maintenance_type_id=mt2.id)
    assert results == []


def test_filters_by_brand_and_model_via_real_fk_match(db, env):
    """Most VEMS-imported PM Templates are stored with real
    vehicle_brand_id/vehicle_model_id FKs, not free-text vehicle_make/
    vehicle_model -- confirms the criteria-based matcher checks both."""
    vt, mt = env
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService, VehicleModelService)
    ford = VehicleBrandService().create(name="Ford CriteriaTest")
    escape = VehicleModelService().create(brand_id=ford.id, name="Escape CriteriaTest")
    honda = VehicleBrandService().create(name="Honda CriteriaTest")
    city = VehicleModelService().create(brand_id=honda.id, name="City CriteriaTest")

    ford_sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=1000,
        vehicle_brand_id=ford.id, vehicle_model_id=escape.id)
    honda_sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_brand_id=honda.id, vehicle_model_id=city.id)

    results = PMScheduleService().list_applicable_for_criteria(
        brand_name="Ford CriteriaTest", model_name="Escape CriteriaTest")
    ids = [r.id for r in results]
    assert ford_sched.id in ids
    assert honda_sched.id not in ids


def test_empty_criteria_returns_empty(db, env):
    results = PMScheduleService().list_applicable_for_criteria()
    assert results == []
