"""One-time data migration: import VEMS_Masterdata_for_vehicle.xlsx's 'PMS'
sheet into PMSchedule (+ PMScopeTemplate/PMScopeItem), as PMS-2 Profiles.

VEMS's raw sheet is a pre-expanded calendar of every future milestone
(e.g. 80 rows out to 400,000km for one vehicle) rather than a compact
recurring rule. This script deduplicates by exact checklist ("Scope")
text within each Task_CD group to recover the real distinct packages,
and derives each package's true recurring interval from the periodicity
of its repeated occurrences (a package appearing every 4th Sort step on
a 5,000km base = a 20,000km recurring interval).

"Vehicle Registration" rows are intentionally excluded — that's LTO
annual renewal, already covered by our dedicated Vehicle Registration
transaction module, not a workshop PM task.
"""
import os
import re
import sys
from collections import defaultdict

# See import_pm_task_list.py for why this is needed -- running this
# script standalone (not through the test suite, which handles the path
# itself) otherwise fails to import `app` at all, or worse, silently
# resolves it to an unrelated pip-installed package of the same name.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# See import_pm_task_list.py for the full explanation: without this, a
# raw script invocation silently falls back to an unmigrated local
# SQLite file instead of the real configured (e.g. MySQL) database,
# since only the `flask` CLI auto-loads .env, not a bare `python
# script.py` run.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import openpyxl

from app.extensions import db
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService)
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)

EXCLUDED_CATEGORIES = {"Vehicle Registration"}

CATEGORY_TO_MTYPE = {
    "Vehicle Preventive Maintenance": ("PMS-PREV", "Preventive Maintenance Service"),
    "Tire Replacement": ("PMS-TIRE", "Tire Replacement"),
    "Battery Replacement": ("PMS-BATT", "Battery Replacement"),
    "Aircon Servicing": ("PMS-AIRCON", "Aircon Servicing"),
}


def _parse_step(value, unit_multiplier: dict) -> int:
    """'5KMS' -> 5000 (with {'KM':1000,'KMS':1000}), '3MTH' -> 90 days
    (with {'MTH':30,'YRS':365}), '0' -> 0."""
    if value is None:
        return 0
    s = str(value).strip().upper()
    m = re.match(r"^(\d+)([A-Z]+)$", s)
    if not m:
        return 0
    number, unit = m.groups()
    mult = unit_multiplier.get(unit, 0)
    return int(number) * mult


def _split_scope_into_items(scope_text: str) -> list:
    """Splits VEMS's numbered checklist text ('1. Foo  2. Bar') into
    individual activity strings."""
    if not scope_text:
        return []
    parts = re.split(r"\d{1,2}\.\s+", scope_text)
    return [p.strip() for p in parts if p.strip()]


def _get_or_create_maintenance_type(code: str, name: str, cache: dict):
    if code in cache:
        return cache[code]
    from app.modules.master_data.reference.models import MaintenanceType
    existing = MaintenanceType.query.filter_by(code=code).first()
    if existing:
        cache[code] = existing
        return existing
    created = MaintenanceTypeService().create(code=code, name=name,
                                               category="PM")
    cache[code] = created
    return created


def _resolve_brand_model(make: str, model: str, brand_cache: dict):
    key = (make or "").strip().lower()
    if key not in brand_cache:
        brand_cache[key] = VehicleBrandService().get_by_name(make)
    brand = brand_cache[key]
    if not brand:
        return None, None
    model_obj = VehicleModelService().get_by_name_and_brand(model, brand.id)
    return brand.id, (model_obj.id if model_obj else None)


def import_pms(xlsx_path: str, dry_run: bool = True, limit_groups: int = None) -> dict:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["PMS"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    groups = defaultdict(list)
    for r in rows:
        groups[r[0]].append(r)

    mtype_cache = {}
    brand_cache = {}
    stats = {
        "groups_processed": 0, "groups_skipped_excluded": 0,
        "groups_skipped_no_category": 0, "packages_created": 0,
        "scope_items_created": 0, "samples": [],
    }

    group_items = list(groups.items())
    if limit_groups:
        group_items = group_items[:limit_groups]

    for task_cd, group_rows in group_items:
        category = group_rows[0][4]
        if category in EXCLUDED_CATEGORIES:
            stats["groups_skipped_excluded"] += 1
            continue
        if category not in CATEGORY_TO_MTYPE:
            stats["groups_skipped_no_category"] += 1
            continue

        make = group_rows[0][2]
        model = group_rows[0][3]
        task_description = group_rows[0][1]

        # Deduplicate by exact Scope text; track every Sort occurrence.
        by_scope = defaultdict(list)
        for r in group_rows:
            scope_text = r[10]
            sort_val = int(r[14]) if r[14] not in (None, "") else 1
            by_scope[scope_text].append((sort_val, r))

        km_step = _parse_step(group_rows[0][11], {"KM": 1000, "KMS": 1000})
        cal_step = _parse_step(group_rows[0][12], {"MTH": 30, "YRS": 365,
                                                    "YR": 365, "DAY": 1,
                                                    "DAYS": 1})

        packages = []  # (periodicity, scope_text, sample_row)
        for scope_text, occurrences in by_scope.items():
            sorts = sorted(o[0] for o in occurrences)
            if len(sorts) == 1:
                periodicity = sorts[0]
            else:
                gaps = [sorts[i + 1] - sorts[i] for i in range(len(sorts) - 1)]
                periodicity = min(gaps) if gaps else sorts[0]
            packages.append((periodicity, scope_text, occurrences[0][1]))

        packages.sort(key=lambda p: p[0])

        mtype_code, mtype_name = CATEGORY_TO_MTYPE[category]
        mtype = _get_or_create_maintenance_type(mtype_code, mtype_name, mtype_cache) \
            if not dry_run else mtype_cache.setdefault(mtype_code, None)

        brand_id, model_id = (None, None)
        if not dry_run:
            brand_id, model_id = _resolve_brand_model(make, model, brand_cache)

        for seq_pos, (periodicity, scope_text, sample_row) in enumerate(packages, start=1):
            interval_km = periodicity * km_step if km_step else None
            interval_days = periodicity * cal_step if cal_step else None
            trigger_mode = ("HYBRID" if interval_km and interval_days
                           else "KM" if interval_km else "CALENDAR")
            if not interval_km and not interval_days:
                continue  # nothing usable to schedule on

            activity_texts = _split_scope_into_items(scope_text)
            if len(stats["samples"]) < 8:
                stats["samples"].append({
                    "task_cd": task_cd, "make": make, "model": model,
                    "category": category, "sequence": seq_pos,
                    "interval_km": interval_km, "interval_days": interval_days,
                    "trigger_mode": trigger_mode,
                    "activity_count": len(activity_texts),
                    "first_activity": activity_texts[0][:60] if activity_texts else "",
                })

            if not dry_run:
                sched = PMScheduleService().create(
                    maintenance_type_id=mtype.id, trigger_mode=trigger_mode,
                    vehicle_make=str(make) if brand_id is None else None,
                    vehicle_model=str(model) if brand_id is None else None,
                    vehicle_brand_id=brand_id, vehicle_model_id=model_id,
                    profile_code=str(task_cd),
                    profile_description=str(task_description),
                    sequence_position=seq_pos,
                    interval_km=interval_km, interval_days=interval_days,
                    priority="MEDIUM")
                if activity_texts:
                    items = [{
                        "activity_code": f"{task_cd}-{i+1:03d}",
                        "activity_description": text[:255],
                        "sort_order": i,
                    } for i, text in enumerate(activity_texts)]
                    PMScopeTemplateService().create(
                        maintenance_type_id=mtype.id,
                        name=f"{task_description} - Package {seq_pos}"[:120],
                        description=str(task_description),
                        pm_schedule_id=sched.id, items=items)
            # Counted whether or not we're actually writing — dry-run
            # should preview exactly what a real run would produce.
            stats["scope_items_created"] += len(activity_texts)
            stats["packages_created"] += 1

        stats["groups_processed"] += 1

    if not dry_run:
        db.session.commit()

    return stats


if __name__ == "__main__":
    import sys
    from app import create_app
    path = sys.argv[1] if len(sys.argv) > 1 else "VEMS_Masterdata_for_vehicle.xlsx"
    dry = "--dry-run" in sys.argv
    app = create_app()
    with app.app_context():
        result = import_pms(path, dry_run=dry)
        for k, v in result.items():
            if k != "samples":
                print(k, ":", v)
        print("\nSample packages:")
        for s in result["samples"]:
            print(s)
