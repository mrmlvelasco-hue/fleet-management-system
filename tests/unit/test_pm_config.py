import pytest

from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService, InvalidScopeError)
from app.modules.maintenance_config.models import PMSchedule, PMScopeTemplate
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)


@pytest.fixture()
def env(db):
    vt = VehicleTypeService().create(code="LV-PM", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(
        code="PMS-5K", name="5,000 KM PMS", category="PREVENTIVE")
    return vt, mt


def test_create_km_schedule(db, env):
    vt, mt = env
    svc = PMScheduleService()
    sched = svc.create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                       trigger_mode="KM", interval_km=5000)
    assert sched.id is not None
    assert sched.interval_km == 5000


def test_create_hybrid_schedule_requires_both_intervals(db, env):
    vt, mt = env
    from app.modules.maintenance_config.service import InvalidScheduleError
    with pytest.raises(InvalidScheduleError):
        PMScheduleService().create(
            vehicle_type_id=vt.id, maintenance_type_id=mt.id,
            trigger_mode="HYBRID", interval_km=10000)  # missing interval_days


def test_create_scope_template_with_items(db, env):
    vt, mt = env
    svc = PMScopeTemplateService()
    tmpl = svc.create(maintenance_type_id=mt.id, name="5,000 KM PMS Scope",
                      items=[
                          {"activity_code": "OIL", "activity_description": "Change Engine Oil",
                           "sort_order": 1},
                          {"activity_code": "FILTER", "activity_description": "Replace Oil Filter",
                           "sort_order": 2},
                      ])
    assert len(tmpl.items) == 2
    assert tmpl.items[0].activity_code == "OIL"


def test_scope_template_requires_at_least_one_item(db, env):
    vt, mt = env
    with pytest.raises(InvalidScopeError):
        PMScopeTemplateService().create(
            maintenance_type_id=mt.id, name="Empty", items=[])


def test_update_scope_template_replaces_items(db, env):
    vt, mt = env
    svc = PMScopeTemplateService()
    tmpl = svc.create(maintenance_type_id=mt.id, name="Template",
                      items=[{"activity_code": "A", "activity_description": "A desc",
                             "sort_order": 1}])
    svc.update(tmpl.id, items=[
        {"activity_code": "B", "activity_description": "B desc", "sort_order": 1},
        {"activity_code": "C", "activity_description": "C desc", "sort_order": 2},
    ])
    assert len(tmpl.items) == 2
    assert tmpl.items[0].activity_code == "B"


def test_deactivate_schedule(db, env):
    vt, mt = env
    svc = PMScheduleService()
    sched = svc.create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                       trigger_mode="KM", interval_km=5000)
    svc.deactivate(sched.id)
    assert db.session.get(PMSchedule, sched.id).is_active is False
