from datetime import date

import pytest

from app.modules.maintenance_config.service import (
    PMScheduleService, PMSProfileService)
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)


@pytest.fixture()
def env(db):
    mt = MaintenanceTypeService().create(code="PMS2-PREV", name="Preventive Maintenance",
                                         category="PREVENTIVE")
    toyota = VehicleBrandService().create(name="Toyota PMS2")
    hilux = VehicleModelService().create(brand_id=toyota.id, name="Hilux PMS2")
    return mt, toyota, hilux


def test_multiple_packages_can_share_a_profile_code(db, env):
    mt, toyota, hilux = env
    svc = PMScheduleService()
    svc.create(maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
               vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
               profile_code="HILUX-DIESEL-PMS2", sequence_position=1,
               profile_description="Hilux Diesel PMS")
    svc.create(maintenance_type_id=mt.id, trigger_mode="KM", interval_km=20000,
               vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
               profile_code="HILUX-DIESEL-PMS2", sequence_position=2,
               profile_description="Hilux Diesel PMS")

    from app.modules.maintenance_config.models import PMSchedule
    matches = PMSchedule.query.filter_by(profile_code="HILUX-DIESEL-PMS2").all()
    assert len(matches) == 2


def test_list_profiles_groups_by_profile_code(db, env):
    mt, toyota, hilux = env
    svc = PMScheduleService()
    svc.create(maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
               vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
               profile_code="HILUX-DIESEL-PMS2A", sequence_position=1,
               profile_description="Hilux Diesel PMS A")
    svc.create(maintenance_type_id=mt.id, trigger_mode="KM", interval_km=20000,
               vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
               profile_code="HILUX-DIESEL-PMS2A", sequence_position=2,
               profile_description="Hilux Diesel PMS A")
    # A standalone schedule with no profile_code should not appear.
    svc.create(maintenance_type_id=mt.id, trigger_mode="KM", interval_km=10000,
               vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id)

    profiles = PMSProfileService().list_profiles()
    codes = {p["profile_code"] for p in profiles}
    assert "HILUX-DIESEL-PMS2A" in codes
    matched = next(p for p in profiles if p["profile_code"] == "HILUX-DIESEL-PMS2A")
    assert matched["package_count"] == 2
    assert matched["description"] == "Hilux Diesel PMS A"


def test_get_profile_returns_packages_ordered_by_sequence(db, env):
    mt, toyota, hilux = env
    svc = PMScheduleService()
    svc.create(maintenance_type_id=mt.id, trigger_mode="KM", interval_km=20000,
               vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
               profile_code="HILUX-DIESEL-PMS2B", sequence_position=2)
    svc.create(maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
               vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
               profile_code="HILUX-DIESEL-PMS2B", sequence_position=1)

    packages = PMSProfileService().get_profile("HILUX-DIESEL-PMS2B")
    assert [p.sequence_position for p in packages] == [1, 2]
    assert [p.interval_km for p in packages] == [5000, 20000]


def test_get_profile_returns_empty_list_for_unknown_code(db, env):
    assert PMSProfileService().get_profile("NONEXISTENT") == []
