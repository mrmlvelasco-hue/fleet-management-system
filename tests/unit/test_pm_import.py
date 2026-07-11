import io

import pytest

from app.modules.maintenance_config.import_service import (
    PMScheduleImportService, PMScopeImportService)
from app.modules.maintenance_config.models import PMSchedule, PMScopeTemplate
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)


@pytest.fixture()
def env(db):
    vt = VehicleTypeService().create(code="Hilux", name="Toyota Hilux",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS-5K", name="5,000 KM PMS",
                                         category="PREVENTIVE")
    return vt, mt


SCHEDULE_CSV = """vehicle_type_code,maintenance_type_code,trigger_mode,interval_km,interval_days,priority
Hilux,PMS-5K,HYBRID,5000,180,MEDIUM
,PMS-5K,KM,10000,,LOW
"""

SCOPE_CSV = """maintenance_type_code,scope_template_name,activity_code,activity_description,standard_labor_hours,estimated_cost,required_parts,vendor_recommendation,sort_order
PMS-5K,5000 KM PMS Scope,OIL,Change Engine Oil,0.5,800,Oil filter; engine oil 4L,Toyota Genuine Parts,1
PMS-5K,5000 KM PMS Scope,FILTER,Replace Oil Filter,0.2,150,Oil filter,Toyota Genuine Parts,2
"""


def test_import_schedules_with_make_model_columns(db, env):
    vt, mt = env
    csv_with_make_model = (
        "vehicle_type_code,vehicle_make,vehicle_model,maintenance_type_code,"
        "trigger_mode,interval_km,interval_days,priority,notify_before_km,"
        "notify_before_days,escalate_if_overdue\n"
        ",Honda,City,PMS-5K,KM,10000,,MEDIUM,1000,,true\n")
    svc = PMScheduleImportService()
    result = svc.import_csv(io.StringIO(csv_with_make_model))
    assert result["created"] == 1
    sched = PMSchedule.query.filter_by(vehicle_make="Honda").first()
    assert sched.vehicle_model == "City"
    assert sched.notify_before_km == 1000
    assert sched.escalate_if_overdue is True


def test_scope_import_resolves_pm_schedule_id_from_make_model_columns(db, env):
    vt, mt = env
    PMScheduleImportService().import_csv(io.StringIO(
        "vehicle_type_code,vehicle_make,vehicle_model,maintenance_type_code,"
        "trigger_mode,interval_km,interval_days,priority\n"
        ",Honda,City,PMS-5K,KM,10000,,MEDIUM\n"))

    scope_csv = (
        "maintenance_type_code,scope_template_name,vehicle_make,vehicle_model,"
        "activity_code,activity_description,sort_order\n"
        "PMS-5K,Honda City 10K PMS,Honda,City,OIL,Change Oil,1\n")
    result = PMScopeImportService().import_csv(io.StringIO(scope_csv))
    assert result["templates_created"] == 1
    tmpl = PMScopeTemplate.query.filter_by(name="Honda City 10K PMS").first()
    assert tmpl.pm_schedule_id is not None
    assert tmpl.pm_schedule.vehicle_make == "Honda"


def test_import_schedules_from_csv(db, env):
    vt, mt = env
    svc = PMScheduleImportService()
    result = svc.import_csv(io.StringIO(SCHEDULE_CSV))
    assert result["created"] == 2
    assert result["errors"] == []
    assert PMSchedule.query.count() == 2
    specific = PMSchedule.query.filter_by(vehicle_type_id=vt.id).first()
    assert specific.trigger_mode == "HYBRID"
    assert specific.interval_km == 5000


def test_import_schedules_unknown_maintenance_type_reports_error(db, env):
    vt, mt = env
    bad_csv = "vehicle_type_code,maintenance_type_code,trigger_mode,interval_km,interval_days,priority\n,NOPE,KM,5000,,MEDIUM\n"
    svc = PMScheduleImportService()
    result = svc.import_csv(io.StringIO(bad_csv))
    assert result["created"] == 0
    assert len(result["errors"]) == 1
    assert "NOPE" in result["errors"][0]


def test_import_scope_items_groups_by_template_name(db, env):
    vt, mt = env
    svc = PMScopeImportService()
    result = svc.import_csv(io.StringIO(SCOPE_CSV))
    assert result["templates_created"] == 1
    assert result["items_created"] == 2
    tmpl = PMScopeTemplate.query.filter_by(name="5000 KM PMS Scope").first()
    assert tmpl is not None
    assert len(tmpl.items) == 2
    assert tmpl.items[0].activity_code == "OIL"
    assert tmpl.items[0].required_parts == "Oil filter; engine oil 4L"


def test_import_scope_items_unknown_maintenance_type_reports_error(db, env):
    bad_csv = ("maintenance_type_code,scope_template_name,activity_code,"
              "activity_description,standard_labor_hours,estimated_cost,"
              "required_parts,vendor_recommendation,sort_order\n"
              "NOPE,Scope X,A,Desc,0.5,100,Parts,Vendor,1\n")
    svc = PMScopeImportService()
    result = svc.import_csv(io.StringIO(bad_csv))
    assert result["templates_created"] == 0
    assert len(result["errors"]) == 1


def test_import_is_idempotent_on_rerun(db, env):
    vt, mt = env
    svc = PMScheduleImportService()
    svc.import_csv(io.StringIO(SCHEDULE_CSV))
    result2 = svc.import_csv(io.StringIO(SCHEDULE_CSV))
    assert result2["created"] == 0
    assert result2["skipped"] == 2
    assert PMSchedule.query.count() == 2
