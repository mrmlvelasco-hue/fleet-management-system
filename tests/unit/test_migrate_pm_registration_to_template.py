import pytest

from scripts.migrate_pm_registration_to_template import migrate_registration_pm_templates
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService)
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.registration_config.models import RegistrationTemplate


@pytest.fixture()
def env(db):
    vt = VehicleTypeService().create(code="LV-MIGRATEREG", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="MIGRATEREG-MT", name="Vehicle Registration PM",
                                         category="PM")
    schedule = PMScheduleService().create(
        vehicle_type_id=vt.id, maintenance_type_id=mt.id, trigger_mode="CALENDAR",
        interval_days=360, priority="HIGH", notify_before_days=None)
    scope = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Vehicle Registration",
        pm_schedule_id=schedule.id,
        items=[
            {"activity_code": "1", "activity_description": "Preparation of list of Motor Vehicles due for renewal.", "sort_order": 1},
            {"activity_code": "2", "activity_description": "Submit to LTO District office the previous LTO O.R.", "sort_order": 2},
        ])
    return vt, mt, schedule, scope


def test_dry_run_does_not_write_anything(db, env):
    vt, mt, schedule, scope = env
    stats = migrate_registration_pm_templates(dry_run=True)
    assert stats["matched"] == 1
    assert RegistrationTemplate.query.count() == 0


def test_migrates_matched_pm_schedule_to_registration_template(db, env):
    vt, mt, schedule, scope = env
    stats = migrate_registration_pm_templates(dry_run=False)
    assert stats["migrated"] == 1

    tmpl = RegistrationTemplate.query.filter_by(vehicle_type_id=vt.id).first()
    assert tmpl is not None
    assert tmpl.interval_years == 1  # 360 days ~ 1 year
    assert tmpl.priority == "HIGH"
    assert len(tmpl.checklist_items) == 2
    assert tmpl.checklist_items[0].activity_description.startswith(
        "Preparation of list")


def test_deactivates_old_pm_schedule_after_migration(db, env):
    vt, mt, schedule, scope = env
    migrate_registration_pm_templates(dry_run=False)

    from app.extensions import db as _db
    _db.session.refresh(schedule)
    _db.session.refresh(scope)
    assert schedule.is_active is False
    assert scope.is_active is False


def test_idempotent_does_not_duplicate_on_rerun(db, env):
    vt, mt, schedule, scope = env
    migrate_registration_pm_templates(dry_run=False)
    stats2 = migrate_registration_pm_templates(dry_run=False)
    assert stats2["migrated"] == 0  # already-deactivated schedule not re-matched
    assert RegistrationTemplate.query.count() == 1


def test_no_matching_templates_returns_zero(db):
    stats = migrate_registration_pm_templates(dry_run=True)
    assert stats["matched"] == 0
