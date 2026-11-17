"""Migration: import PM_Task_List.xlsx ('Sheet1') into PMSchedule (+
PMScopeTemplate/PMScopeItem) — a separate, purpose-built importer for
this specific file, NOT a modification of import_vems_pms.py (which
targets a different source file/sheet layout, 'VEMS_Masterdata_for_
vehicle.xlsx''s 'PMS' sheet, with its own passing tests already
depending on that exact positional structure).

IMPORTANT — how this differs from the other importer's approach:
import_vems_pms.py infers a package's true recurring interval from the
PERIODICITY of repeated identical Scope-text occurrences, because that
source file pre-expands one clean recurring package across many rows
sharing byte-identical checklist text. Checked whether the same pattern
holds here (see the investigation this file's migration prompted) and
it does NOT: the overwhelming majority of rows in THIS file have a
genuinely UNIQUE Scope text per row (Ford Escape's 40 PM rows produced
only 3 scope-text collisions out of ~37 distinct texts, with irregular,
non-uniform gaps between the few that did repeat) -- meaning each row
here really is its own distinct package, not a redundantly pre-expanded
copy of a smaller recurring set. Inferring periodicity from repetition
would therefore be actively WRONG for this file: it silently produced
multiplied, incorrect intervals (e.g. "10,000" instead of "5,000") that
didn't match the row's own WorkDescription text at all when first
tried against this exact data.

The correct, directly-verifiable model for this file: each row's own
KM Reading / Calendar / Hourly code already states its actual, real
interval outright -- no inference required. Exact duplicate rows
(identical Scope + WorkDescription + all three codes) are collapsed,
but that's it.

Three real gaps fixed vs. how this kind of file was previously migrated:

  1. WorkDescription (the per-package, pm2-pm9-tokenized work-order
     description template, e.g. "First 1,000 km servicing of pm2 pm3
     with Plate no. pm4...") was never captured anywhere — only the
     generic category-level Description. Now captured into
     PMSchedule.work_description_template.

  2. The frequency codes (e.g. "10KMS", "4.5MTH") are decoded via a
     corrected, validated reference table (vems_frequency_reference.py)
     instead of trusting the source lookup's Val/ValDesc columns as-is
     (several have real data-entry errors) or fragile regex-parsing the
     code string (which can't handle decimals or Quarter/Week/Hour
     units at all).

  3. Month/Year/Week/Quarter intervals are converted into interval_days
     (30/365/7/90 days respectively), as requested.

Reads columns by HEADER NAME, not positional index.

"Vehicle Registration" rows are excluded — LTO annual renewal, already
covered by the dedicated Vehicle Registration transaction module.
"""
import os
import re
import sys
from collections import defaultdict

# Make sure THIS project's app/ package is what gets imported, regardless
# of the current working directory or PYTHONPATH -- and regardless of
# whether some unrelated pip-installed package also happens to be named
# "app" (this really happened during testing: an ImportStringError
# traceback showed Python resolving `app` to
# .venv\Lib\site-packages\app\__init__.py instead of this project's own
# app/ folder). Inserting the real project root at sys.path[0] (the
# directory ABOVE this scripts/ folder) makes the local package win
# every time, and removes any dependency on how the script happens to be
# invoked (bare `python scripts\...py`, a different cwd, no PYTHONPATH
# set at all, etc.) -- the previous version of this script assumed the
# caller had already arranged that, which silently wasn't true the
# moment it was actually run standalone from PowerShell.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The `flask` CLI (flask db upgrade, flask seed all, ...) auto-loads a
# .env file for you -- that's WHY `flask db upgrade` correctly reached
# the real MySQL database and added the new columns there. A raw
# `python scripts\...py` invocation does NOT go through that CLI
# bootstrapping at all, so DATABASE_URL/DB_* env vars from .env were
# simply never set in that process -- config.py's own fallback then
# silently defaulted to a brand-new, never-migrated
# sqlite:///fms_dev.db, which is exactly the "no such column:
# pm_schedules.interval_hours" error: that column really does exist on
# the real MySQL database (confirmed via SHOW CREATE TABLE), just not on
# this accidental, empty, unrelated SQLite file this script ended up
# talking to instead. Loading .env here ourselves makes this script
# connect to the exact same database `flask` commands do, every time.
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
from vems_frequency_reference import (
    to_interval_km, to_interval_days, to_interval_hours)

EXCLUDED_CATEGORIES = {"Vehicle Registration"}

CATEGORY_TO_MTYPE = {
    "Vehicle Preventive Maintenance": ("PMS-PREV", "Preventive Maintenance Service"),
    "Tire Replacement": ("PMS-TIRE", "Tire Replacement"),
    "Battery Replacement": ("PMS-BATT", "Battery Replacement"),
    "Aircon Servicing": ("PMS-AIRCON", "Aircon Servicing"),
}


SCOPE_SHEET_NAME = "Scope Details"
SCOPE_SHEET_HEADERS = ["Make", "Model", "PM_CD", "Task_CD", "Line_No",
                       "Scope_Detail"]

# Task_CD values starting with this are the fill-in template's own
# example rows. They are never imported, so a person who forgets to
# delete them does not end up with an "EXAMPLE" PMS Profile.
EXAMPLE_TASK_PREFIX = "EXAMPLE"

# How many packages the review screen is sent. A full VEMS export runs
# to a few thousand; the counts above the table are always complete.
REVIEW_PACKAGE_LIMIT = 300

_INSTRUCTION_SENTENCE = re.compile(r"^\s*see\s+sheet\s+scope\s+details",
                                   re.IGNORECASE)


def _scope_key(make, model, pm_cd, task_cd):
    return (str(make or "").strip().lower(),
            str(model or "").strip().lower(),
            str(pm_cd or "").strip(), str(task_cd or "").strip())


def _find_scope_sheet(wb):
    """The checklist sheet, or None.

    Normally it is named "Scope Details". A workbook rebuilt by hand
    (copy one model's rows into a new file, as the Strada -> Triton
    file was) usually leaves it as "Sheet2", and the importer used to
    treat that as "this file has no checklist sheet" and carry on --
    every package then got the Scope column's own instruction sentence
    as its one checklist line. So: the sheet with that name if it has
    the right columns, otherwise any sheet after the first that does.
    The first sheet is always the main sheet and is never considered.
    """
    def _has_headers(ws):
        header = [c.value for c in ws[1]]
        return all(h in header for h in SCOPE_SHEET_HEADERS)

    by_name = {name.strip().lower(): name for name in wb.sheetnames[1:]}
    exact = by_name.get(SCOPE_SHEET_NAME.lower())
    if exact is not None and _has_headers(wb[exact]):
        return wb[exact]
    if SCOPE_SHEET_NAME in wb.sheetnames:
        # Present but not this sheet's expected shape -- degrade
        # cleanly, exactly as before.
        return None
    for name in wb.sheetnames[1:]:
        if _has_headers(wb[name]):
            return wb[name]
    return None


def _read_scope_details(wb):
    """`(index, labels, sheet_title)` from the checklist sheet.

    index:  {(make, model, pm_cd, task_cd) lowercased: [line text, ...]}
            sorted by Line_No.
    labels: the same keys -> (Make, Model) as typed, for messages.
    sheet_title: the sheet the lines came from, or None.

    This is the NEW file format's real checklist source: the main
    sheet's Scope column no longer contains checklist text at all --
    it contains an instruction sentence for a human reader ("See Sheet
    Scope Details with Parameter of Brand=... Task_CD=... PM_CD=..."),
    telling them to look the real steps up in this sheet.

    The join key doesn't need to be extracted from that sentence by
    regex, though -- the main sheet's own row ALREADY carries Make,
    Model, Task_CD and PM_CD as separate columns, so this indexes
    directly on those and the instruction sentence is never parsed at
    all. Returns empty results if there is no such sheet, so a file in
    the OLDER inline-text format degrades cleanly to the previous
    behaviour rather than failing.
    """
    ws = _find_scope_sheet(wb)
    if ws is None:
        return {}, {}, None
    header = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(header)}

    raw = defaultdict(list)
    labels = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row is None or row[idx["Scope_Detail"]] is None:
            continue
        key = _scope_key(row[idx["Make"]], row[idx["Model"]],
                         row[idx["PM_CD"]], row[idx["Task_CD"]])
        line_no = row[idx["Line_No"]] or 0
        raw[key].append((line_no, str(row[idx["Scope_Detail"]]).strip()))
        labels.setdefault(key, (str(row[idx["Make"]] or "").strip(),
                                str(row[idx["Model"]] or "").strip()))

    index = {key: [text for _n, text in sorted(lines, key=lambda t: t[0])]
             for key, lines in raw.items()}
    return index, labels, ws.title


def _load_scope_details_index(wb):
    """Just the index from `_read_scope_details` (kept for callers that
    only need the join)."""
    return _read_scope_details(wb)[0]


def _existing_profiles():
    """{profile_code: {"vehicle": "Brand Model", "packages": n}} for
    every ACTIVE PMS Profile already in the catalog.

    Task_CD becomes the profile code, and a profile is nothing more
    than the packages sharing one code. Importing a Task_CD that is
    already in use therefore does not create a second profile -- it
    files the new packages inside the existing one. That is how a
    Triton file carrying the Strada's Task_CD would have ended up
    inside the Strada's profile, and how uploading the same file twice
    doubled every package. A deactivated profile does not count: its
    code is free to be imported again.
    """
    from sqlalchemy.orm import joinedload
    from app.modules.maintenance_config.models import PMSchedule
    rows = (PMSchedule.query
            .filter(PMSchedule.profile_code.isnot(None),
                    PMSchedule.is_active.is_(True))
            .options(joinedload(PMSchedule.vehicle_brand),
                     joinedload(PMSchedule.vehicle_model_ref))
            .all())
    found = {}
    for r in rows:
        entry = found.get(r.profile_code)
        if entry is None:
            brand = (r.vehicle_brand.name if r.vehicle_brand
                     else r.vehicle_make) or ""
            model = (r.vehicle_model_ref.name if r.vehicle_model_ref
                     else r.vehicle_model) or ""
            entry = found[r.profile_code] = {
                "vehicle": f"{brand} {model}".strip(), "packages": 0}
        entry["packages"] += 1
    return found


def _cumulative_km_from_work_description(work_desc: str):
    """Extracts the absolute cumulative odometer milestone from a work-
    description like "65,000 km servicing of ..." or "First 1,000 km
    servicing ..." -> 65000 / 1000. This is the number that actually
    identifies WHICH package a vehicle at a given odometer needs (the
    per-package interval_km is a constant 5,000 recurring STEP and can't
    distinguish the 65,000-km package from the 105,000-km one). Returns
    None if no "<number> km" milestone is present (e.g. a purely
    calendar-based package), so the recommendation logic can fall back
    to interval math."""
    if not work_desc:
        return None
    m = re.search(r"([\d,]+)\s*km\b", str(work_desc), re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _split_scope_into_items(scope_text: str) -> list:
    """Splits VEMS's numbered checklist text ('1. Foo  2. Bar') into
    individual activity strings."""
    if not scope_text:
        return []
    parts = re.split(r"\d{1,2}\.\s+", scope_text)
    return [p.strip() for p in parts if p.strip()]


def _hours_from_hourly_column(value):
    """The Hourly column holds either a real frequency code ('500HRS')
    or, in some rows, a bare number ('500') meaning that many hours
    directly. Tries the code table first, then falls back to treating a
    plain digit string as the raw hour count."""
    hrs = to_interval_hours(value)
    if hrs is not None:
        return hrs
    s = str(value).strip() if value is not None else ""
    if s.isdigit() and int(s) > 0:
        return int(s)
    return None


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


def import_pm_task_list(xlsx_path: str, dry_run: bool = True,
                        limit_groups: int = None,
                        skip_existing_profiles: bool = True) -> dict:
    """Import (or, with dry_run, only describe) a PM Task List workbook.

    skip_existing_profiles: leave out any Task_CD that is already an
    active PMS Profile (see `_existing_profiles`). Pass False only when
    the caller is wiping the catalog first ("Replace existing data"),
    where every existing profile is about to be deleted anyway.
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    header_row = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(header_row)}
    required = ["Make", "Model", "Description", "Task_CD", "WorkDescription",
               "Scope", "KM Reading", "Calendar", "Hourly", "PMDescription",
               "Sort"]
    missing = [h for h in required if h not in idx]
    if missing:
        raise ValueError(f"Expected column(s) not found in {xlsx_path}: "
                         f"{missing}. Found: {header_row}")

    # Real per-package checklists, when the file has this sheet (see
    # _read_scope_details's docstring for why). Empty for an
    # older-format file, which then falls back to text-splitting the
    # Scope column itself, exactly as before.
    scope_index, scope_labels, scope_sheet = _read_scope_details(wb)
    has_pm_cd = "PM_CD" in idx

    rows = list(ws.iter_rows(min_row=2, values_only=True))
    groups = defaultdict(list)
    example_rows = 0
    # Every (make, model, pm_cd, task_cd) the main sheet mentions, in
    # ANY category -- so a checklist line is only called "unmatched"
    # when no main-sheet row asks for it at all.
    main_keys = set()
    for r in rows:
        task_cd = r[idx["Task_CD"]]
        if task_cd is None or str(task_cd).strip() == "":
            continue    # blank row, or a note typed under the data
        if has_pm_cd:
            main_keys.add(_scope_key(r[idx["Make"]], r[idx["Model"]],
                                     r[idx["PM_CD"]], task_cd))
        if str(task_cd).strip().upper().startswith(EXAMPLE_TASK_PREFIX):
            example_rows += 1
            continue
        groups[task_cd].append(r)

    existing = _existing_profiles() if skip_existing_profiles else {}

    mtype_cache, brand_cache = {}, {}
    stats = {
        "groups_processed": 0, "groups_skipped_excluded": 0,
        "groups_skipped_no_category": 0, "packages_created": 0,
        "scope_items_created": 0, "unrecognized_frequency_codes": set(),
        "hour_based_packages": 0, "duplicate_rows_collapsed": 0,
        "samples": [],
        # -- added 2026-11-17, all additive --
        "scope_sheet": scope_sheet,
        "example_rows_ignored": example_rows,
        "groups_skipped_existing": 0, "existing_profiles": [],
        "packages_without_checklist": [],
        "scope_rows_unmatched": 0, "scope_unmatched_models": [],
        "unknown_models": [], "unknown_categories": [],
        "packages": [], "packages_truncated": False,
    }
    unknown_models = {}
    unknown_categories = []

    group_items = list(groups.items())
    if limit_groups:
        group_items = group_items[:limit_groups]

    for task_cd, group_rows in group_items:
        first = group_rows[0]
        category = first[idx["PMDescription"]]
        if category in EXCLUDED_CATEGORIES:
            stats["groups_skipped_excluded"] += 1
            continue
        if category not in CATEGORY_TO_MTYPE:
            stats["groups_skipped_no_category"] += 1
            label = str(category).strip() if category is not None else "(blank)"
            if label not in unknown_categories:
                unknown_categories.append(label)
            continue

        make = first[idx["Make"]]
        model = first[idx["Model"]]
        task_description = first[idx["Description"]]

        owner = existing.get(str(task_cd).strip())
        if owner is not None:
            stats["groups_skipped_existing"] += 1
            stats["existing_profiles"].append({
                "task_cd": str(task_cd).strip(),
                "make": str(make or "").strip(),
                "model": str(model or "").strip(),
                "existing_vehicle": owner["vehicle"],
                "packages": owner["packages"],
            })
            continue

        # Collapse only EXACT duplicate rows (identical Scope +
        # WorkDescription + all three frequency codes) -- everything
        # else is treated as its own distinct package, per the
        # investigation in this module's docstring.
        seen = {}
        for r in group_rows:
            key = (r[idx["Scope"]], r[idx["WorkDescription"]],
                  r[idx["KM Reading"]], r[idx["Calendar"]], r[idx["Hourly"]])
            sort_val = int(r[idx["Sort"]]) if r[idx["Sort"]] not in (None, "") else 1
            if key in seen:
                stats["duplicate_rows_collapsed"] += 1
                if sort_val < seen[key][0]:
                    seen[key] = (sort_val, r)
            else:
                seen[key] = (sort_val, r)

        packages = sorted(seen.values(), key=lambda p: p[0])  # by Sort

        mtype_code, mtype_name = CATEGORY_TO_MTYPE[category]
        mtype = _get_or_create_maintenance_type(mtype_code, mtype_name, mtype_cache) \
            if not dry_run else mtype_cache.setdefault(mtype_code, None)

        # Read-only, so it runs in a dry run too: the review screen can
        # then say a make/model is not in the Vehicle Models master
        # (usually a spelling difference) BEFORE anything is saved.
        brand_id, model_id = _resolve_brand_model(make, model, brand_cache)
        if model_id is None:
            # Brand known, model not: fall back to the free-text
            # make/model match for BOTH. Keeping the brand link alone
            # (as this did before) stored a package with a brand, no
            # model and no text -- which the due-date engine reads as
            # "no make/model at all", i.e. a GLOBAL schedule applied to
            # every vehicle without a more specific one.
            brand_id = None
        if brand_id is None:
            unknown_models.setdefault(
                (str(make or "").strip().lower(),
                 str(model or "").strip().lower()),
                {"make": str(make or "").strip(),
                 "model": str(model or "").strip()})

        for seq_pos, (_sort_val, row) in enumerate(packages, start=1):
            scope_text = row[idx["Scope"]]
            work_desc = row[idx["WorkDescription"]]
            km_code = row[idx["KM Reading"]]
            cal_code = row[idx["Calendar"]]
            hourly_raw = row[idx["Hourly"]]

            interval_km = to_interval_km(km_code)
            interval_days = to_interval_days(cal_code)
            interval_hours = _hours_from_hourly_column(hourly_raw)

            for code in (km_code, cal_code):
                if code not in (None, 0, "0") and to_interval_km(code) is None \
                        and to_interval_days(code) is None:
                    stats["unrecognized_frequency_codes"].add(str(code))
            if interval_hours:
                stats["hour_based_packages"] += 1

            trigger_mode = ("HYBRID" if interval_km and interval_days
                           else "KM" if interval_km else "CALENDAR")
            if not interval_km and not interval_days:
                continue  # nothing usable to schedule on

            pm_cd = row[idx["PM_CD"]] if has_pm_cd else None
            activity_texts = None
            if has_pm_cd:
                activity_texts = scope_index.get(
                    _scope_key(make, model, pm_cd, task_cd))
            # Each package's OWN row already carries its own correct,
            # package-specific description ("...1,000 km " for the
            # first-service package, "...5,000 km " for every one
            # after) -- confirmed directly against a real reported
            # case. Using it here, not the group-level task_description
            # frozen from the group's first row, which is what
            # previously made every package after the first display
            # the FIRST package's km figure in its own name/label,
            # regardless of that package's real interval.
            row_description = row[idx["Description"]] or task_description
            if not activity_texts:
                if scope_text and _INSTRUCTION_SENTENCE.match(str(scope_text)):
                    # The Scope column points at the checklist sheet,
                    # and that sheet has no lines for this package.
                    # The sentence itself is an instruction to a human
                    # reader, never a checklist step -- so the package
                    # is created with no checklist and reported, rather
                    # than given one nonsense step nobody would notice.
                    activity_texts = []
                    stats["packages_without_checklist"].append({
                        "task_cd": str(task_cd).strip(),
                        "pm_cd": str(pm_cd or "").strip(),
                        "make": str(make or "").strip(),
                        "model": str(model or "").strip(),
                        "description": str(row_description or "").strip(),
                    })
                else:
                    # Older-format file -- the Scope column holds
                    # literal checklist text directly.
                    activity_texts = _split_scope_into_items(scope_text)

            package = {
                "task_cd": task_cd, "make": make, "model": model,
                "category": category, "sequence": seq_pos,
                "interval_km": interval_km, "interval_days": interval_days,
                "interval_hours": interval_hours,
                "trigger_mode": trigger_mode,
                "activity_count": len(activity_texts),
                "work_description": (str(work_desc)[:80] if work_desc else ""),
            }
            if len(stats["samples"]) < 10:
                stats["samples"].append(package)
            if len(stats["packages"]) < REVIEW_PACKAGE_LIMIT:
                stats["packages"].append({
                    **package,
                    "pm_cd": str(pm_cd or "").strip(),
                    "description": str(row_description or "").strip(),
                })
            else:
                stats["packages_truncated"] = True

            if not dry_run:
                sched = PMScheduleService().create(
                    maintenance_type_id=mtype.id, trigger_mode=trigger_mode,
                    vehicle_make=str(make) if brand_id is None else None,
                    vehicle_model=str(model) if brand_id is None else None,
                    vehicle_brand_id=brand_id, vehicle_model_id=model_id,
                    profile_code=str(task_cd),
                    profile_description=str(row_description),
                    sequence_position=seq_pos,
                    interval_km=interval_km, interval_days=interval_days,
                    interval_hours=interval_hours,
                    cumulative_km=_cumulative_km_from_work_description(work_desc),
                    work_description_template=(str(work_desc) if work_desc else None),
                    priority="MEDIUM")
                if activity_texts:
                    items = [{
                        "activity_code": f"{task_cd}-{i+1:03d}",
                        "activity_description": text[:255],
                        "sort_order": i,
                    } for i, text in enumerate(activity_texts)]
                    PMScopeTemplateService().create(
                        maintenance_type_id=mtype.id,
                        name=f"{row_description} - Package {seq_pos}"[:120],
                        description=str(row_description),
                        pm_schedule_id=sched.id, items=items)
            stats["scope_items_created"] += len(activity_texts)
            stats["packages_created"] += 1

        stats["groups_processed"] += 1

    if not dry_run:
        db.session.commit()

    # Checklist lines no main-sheet row asks for. Almost always the
    # Model (or a code) was changed on the main sheet but not on the
    # checklist sheet -- exactly what left 21 Triton packages without a
    # checklist in the reported file.
    unmatched = defaultdict(int)
    for key, lines in scope_index.items():
        if key not in main_keys:
            unmatched[scope_labels[key]] += len(lines)
    stats["scope_rows_unmatched"] = sum(unmatched.values())
    stats["scope_unmatched_models"] = [
        {"make": make, "model": model, "rows": count}
        for (make, model), count in sorted(unmatched.items())]

    stats["unknown_models"] = list(unknown_models.values())
    stats["unknown_categories"] = unknown_categories
    stats["unrecognized_frequency_codes"] = sorted(
        stats["unrecognized_frequency_codes"])
    return stats


def backfill_cumulative_km() -> dict:
    """Populate cumulative_km on existing PMSchedule rows from their
    already-stored work_description_template, for installs that imported
    BEFORE the cumulative_km field existed -- so they don't have to wipe
    and re-import to get correct package selection. Only touches rows
    where cumulative_km is currently NULL and a milestone can be parsed;
    idempotent and safe to re-run."""
    from app.modules.maintenance_config.models import PMSchedule
    updated = 0
    scanned = 0
    for sched in PMSchedule.query.filter(
            PMSchedule.cumulative_km.is_(None),
            PMSchedule.work_description_template.isnot(None)).all():
        scanned += 1
        km = _cumulative_km_from_work_description(sched.work_description_template)
        if km is not None:
            sched.cumulative_km = km
            updated += 1
    db.session.commit()
    return {"scanned": scanned, "updated": updated}


def reset_pm_data() -> dict:
    """Wipes ALL PMSchedule/PMScopeTemplate/PMScopeItem rows -- for
    starting a clean re-migration, per the explicit request to "delete
    all the template in the table and re-migrate again" (e.g. after
    fixing a database character-set issue that corrupted a previous
    import).

    Real bug fixed here: the first version of this function only
    cleared PMScopeItem -> PMScopeTemplate -> PMSchedule (the three
    tables actually being reset), but missed that OTHER tables
    reference these by foreign key too:
      - Vehicle.pm_schedule_id ("Assigned PM Template" on Vehicle Master)
      - MaintenanceOrder.pm_schedule_id
      - MaintenanceOrder.scope_template_id
    On MySQL (which enforces FK constraints by default, unlike SQLite's
    more lenient default), deleting a referenced pm_scope_templates row
    while a real MaintenanceOrder still points at it via
    scope_template_id fails outright with "Cannot delete or update a
    parent row: a foreign key constraint fails" -- exactly the reported
    error. This does NOT delete those Vehicle/MaintenanceOrder rows (no
    transaction history is lost) -- it only detaches their reference to
    the specific template row being replaced, which is precisely what
    "wipe the PM catalog and re-migrate fresh" means: the actual
    completed work orders and their own recorded checklist data stay
    exactly as they were; only the link to the OLD catalog entry (which
    is about to be replaced by a freshly re-imported one) is cleared.

    This is intentionally its own explicit opt-in step (--reset flag),
    never automatic, since it touches real Vehicle/MaintenanceOrder rows
    (only to null a reference, not delete them) in addition to wiping
    all PM schedule data."""
    from app.modules.maintenance_config.models import (
        PMSchedule, PMScopeTemplate, PMScopeItem)
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)

    counts = {
        "pm_scope_items": PMScopeItem.query.count(),
        "pm_scope_templates": PMScopeTemplate.query.count(),
        "pm_schedules": PMSchedule.query.count(),
        "vehicles_unlinked": Vehicle.query.filter(
            Vehicle.pm_schedule_id.isnot(None)).count(),
        "maintenance_orders_unlinked": MaintenanceOrder.query.filter(
            db.or_(MaintenanceOrder.pm_schedule_id.isnot(None),
                  MaintenanceOrder.scope_template_id.isnot(None))).count(),
    }

    # Detach external references FIRST -- these updates, not deletes, so
    # no Vehicle or MaintenanceOrder row is touched beyond this one
    # column each.
    Vehicle.query.filter(Vehicle.pm_schedule_id.isnot(None)).update(
        {"pm_schedule_id": None})
    MaintenanceOrder.query.filter(
        MaintenanceOrder.pm_schedule_id.isnot(None)).update(
        {"pm_schedule_id": None})
    MaintenanceOrder.query.filter(
        MaintenanceOrder.scope_template_id.isnot(None)).update(
        {"scope_template_id": None})
    db.session.commit()

    # Now the three PM tables themselves, children before parents.
    PMScopeItem.query.delete()
    PMScopeTemplate.query.delete()
    PMSchedule.query.delete()
    db.session.commit()
    return counts


if __name__ == "__main__":
    import sys
    from app import create_app
    path = sys.argv[1] if len(sys.argv) > 1 else "PM_Task_List.xlsx"
    dry = "--dry-run" in sys.argv
    do_reset = "--reset" in sys.argv
    do_backfill = "--backfill-cumulative-km" in sys.argv
    app = create_app()
    with app.app_context():
        if do_backfill:
            # Standalone: fix existing data in place, no re-import needed.
            result = backfill_cumulative_km()
            print(f"Backfilled cumulative_km: scanned {result['scanned']} "
                 f"schedules with a work description, updated "
                 f"{result['updated']} of them.")
            sys.exit(0)
        if do_reset:
            if dry:
                print("--reset ignored during --dry-run (nothing is ever "
                     "deleted in a dry run).")
            else:
                deleted = reset_pm_data()
                print(f"Reset: deleted {deleted['pm_schedules']} PM "
                     f"schedules, {deleted['pm_scope_templates']} scope "
                     f"templates, {deleted['pm_scope_items']} scope items. "
                     f"Detached (not deleted) the old template reference "
                     f"from {deleted['vehicles_unlinked']} vehicles and "
                     f"{deleted['maintenance_orders_unlinked']} maintenance "
                     f"orders that pointed at the old catalog.")
        result = import_pm_task_list(path, dry_run=dry)
        for k, v in result.items():
            if k != "samples":
                print(k, ":", v)
        print("\nSample packages:")
        for s in result["samples"]:
            print(s)
