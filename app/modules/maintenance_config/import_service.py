"""CSV bulk-import for PM Schedules and PM Scope Templates, so a client's
existing PM register (per make/model, from OEM manuals) can be migrated in
bulk instead of entered screen-by-screen.

Expected CSV columns — see docs/superpowers/pm_import_template.md for the
full reference and example files.

Idempotent: re-running an import with the same rows won't create
duplicates (schedules matched on vehicle_type+maintenance_type+trigger_mode;
scope templates matched on name+maintenance_type, items replaced wholesale
per template on each import so edits in the source file are picked up).
"""
import csv

from app.extensions import db
from app.modules.maintenance_config.models import (
    PMSchedule, PMScopeTemplate, PMScopeItem)
from app.modules.master_data.reference.models import (
    VehicleType, MaintenanceType)


def _get_maintenance_type(code: str):
    return MaintenanceType.query.filter_by(code=code).first()


def _get_vehicle_type(code: str):
    if not code:
        return None
    return VehicleType.query.filter_by(code=code).first()


class PMScheduleImportService:
    def import_csv(self, file_obj) -> dict:
        reader = csv.DictReader(file_obj)
        created, skipped, errors = 0, 0, []

        for i, row in enumerate(reader, start=2):  # row 1 = header
            mt_code = (row.get("maintenance_type_code") or "").strip()
            mt = _get_maintenance_type(mt_code)
            if mt is None:
                errors.append(
                    f"Row {i}: unknown maintenance_type_code '{mt_code}'.")
                continue

            vt_code = (row.get("vehicle_type_code") or "").strip()
            vt = _get_vehicle_type(vt_code)
            if vt_code and vt is None:
                errors.append(
                    f"Row {i}: unknown vehicle_type_code '{vt_code}'.")
                continue

            trigger_mode = (row.get("trigger_mode") or "").strip().upper()
            interval_km = int(row["interval_km"]) if row.get("interval_km") else None
            interval_days = int(row["interval_days"]) if row.get("interval_days") else None
            priority = (row.get("priority") or "MEDIUM").strip().upper()

            existing = PMSchedule.query.filter_by(
                vehicle_type_id=vt.id if vt else None,
                maintenance_type_id=mt.id,
                trigger_mode=trigger_mode).first()
            if existing:
                skipped += 1
                continue

            db.session.add(PMSchedule(
                vehicle_type_id=vt.id if vt else None,
                maintenance_type_id=mt.id, trigger_mode=trigger_mode,
                interval_km=interval_km, interval_days=interval_days,
                priority=priority))
            created += 1

        db.session.commit()
        return {"created": created, "skipped": skipped, "errors": errors}


class PMScopeImportService:
    def import_csv(self, file_obj) -> dict:
        reader = csv.DictReader(file_obj)
        rows_by_template = {}
        errors = []

        for i, row in enumerate(reader, start=2):
            mt_code = (row.get("maintenance_type_code") or "").strip()
            mt = _get_maintenance_type(mt_code)
            if mt is None:
                errors.append(
                    f"Row {i}: unknown maintenance_type_code '{mt_code}'.")
                continue
            key = (mt.id, (row.get("scope_template_name") or "").strip())
            rows_by_template.setdefault(key, []).append(row)

        templates_created = 0
        items_created = 0

        for (mt_id, name), rows in rows_by_template.items():
            tmpl = PMScopeTemplate.query.filter_by(
                maintenance_type_id=mt_id, name=name).first()
            if tmpl is None:
                tmpl = PMScopeTemplate(maintenance_type_id=mt_id, name=name)
                db.session.add(tmpl)
                db.session.flush()
                templates_created += 1
            else:
                tmpl.items.clear()
                db.session.flush()

            for row in rows:
                tmpl.items.append(PMScopeItem(
                    activity_code=row.get("activity_code", ""),
                    activity_description=row.get("activity_description", ""),
                    standard_labor_hours=row.get("standard_labor_hours") or None,
                    estimated_cost=row.get("estimated_cost") or None,
                    required_parts=row.get("required_parts") or None,
                    vendor_recommendation=row.get("vendor_recommendation") or None,
                    sort_order=int(row["sort_order"]) if row.get("sort_order") else 0))
                items_created += 1

        db.session.commit()
        return {"templates_created": templates_created,
               "items_created": items_created, "errors": errors}
