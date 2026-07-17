"""One-time migration: moves any 'Vehicle Registration' PM Template
(PMSchedule + linked PMScopeTemplate) that was mistakenly created in the
Maintenance PMS system over to the proper, dedicated Registration
Template system — so LTO renewal history stays cleanly separated from
Maintenance Order history, per the user's explicit request.

Matches PMScopeTemplate rows by name containing "Vehicle Registration"
(case-insensitive) — that's the identifiable marker for this specific
kind of misplaced data (confirmed from the reported screenshot: a PM
Template with Trigger Mode=CALENDAR, linked to a Scope Template literally
named "Vehicle Registration" with 8 LTO renewal activities).

Idempotent: once migrated, the source PMSchedule/PMScopeTemplate are
deactivated (is_active=False), so a second run finds nothing left to
migrate. Safe to dry-run first.
"""
from app.extensions import db
from app.modules.maintenance_config.models import PMSchedule, PMScopeTemplate
from app.modules.registration_config.service import RegistrationTemplateService


def migrate_registration_pm_templates(dry_run: bool = True) -> dict:
    stats = {"matched": 0, "migrated": 0, "samples": []}

    candidates = (PMScopeTemplate.query
                 .filter(PMScopeTemplate.name.ilike("%vehicle registration%"))
                 .filter_by(is_active=True)
                 .all())

    for scope in candidates:
        schedule = scope.pm_schedule
        if schedule is None or not schedule.is_active:
            continue
        stats["matched"] += 1
        stats["samples"].append({
            "pm_schedule_id": schedule.id, "scope_template_id": scope.id,
            "scope_name": scope.name, "activity_count": len(scope.items),
        })

        if dry_run:
            continue

        interval_years = max(1, round((schedule.interval_days or 365) / 365))
        items = [{
            "activity_code": i.activity_code,
            "activity_description": i.activity_description,
            "sort_order": i.sort_order,
        } for i in sorted(scope.items, key=lambda x: x.sort_order)]

        RegistrationTemplateService().create(
            vehicle_type_id=schedule.vehicle_type_id,
            vehicle_brand_id=schedule.vehicle_brand_id,
            vehicle_model_id=schedule.vehicle_model_id,
            interval_years=interval_years,
            notify_before_days=schedule.notify_before_days,
            priority=schedule.priority,
            items=items)

        # Deactivate the source records so they stop contributing to
        # Maintenance PMS due-calculation/notifications — the same
        # real-world activity is now correctly tracked by the new
        # Registration Template system instead.
        schedule.is_active = False
        scope.is_active = False
        stats["migrated"] += 1

    if not dry_run:
        db.session.commit()

    return stats
