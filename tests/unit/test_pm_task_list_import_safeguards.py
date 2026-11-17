"""PM Task List import -- safeguards added 2026-11-17.

Reported: the client copied one model's PMS (Mitsubishi Strada) into a
new workbook for another model (Mitsubishi Triton), changed the Model
on the main sheet and uploaded it. Three things about that file would
each have produced a wrong PM catalog with nothing on screen to say
so:

  1. The checklist sheet was named "Sheet2", not "Scope Details", so
     the join found nothing and every package's checklist silently
     became the Scope column's own instruction sentence ("See Sheet
     Scope Details with Parameter of ...").
  2. 982 of its 1,064 checklist lines still said Model = Strada, so
     even with the sheet found, 21 of the 25 packages had no checklist.
  3. It reused the Strada's own Task_CD values. Task_CD becomes the PMS
     Profile code, so the Triton's packages would have been filed
     inside the Strada's profile.

These tests lock in what the importer now does about each one.
"""
import openpyxl
import pytest

from import_pm_task_list import import_pm_task_list

MAIN_HEADERS = ["Make", "Model", "Description", "Task_CD", "PM_CD",
                "WorkDescription", "Scope", "KM Reading", "KMDesc",
                "Calendar", "CALDesc", "Hourly", "HourDesc",
                "PMDescription", "Planned_CD", "Planned", "Sort"]
SCOPE_HEADERS = ["Make", "Model", "PM_CD", "Task_CD", "Line_No",
                 "Scope_Detail"]


def _instruction(make, model, task_cd, pm_cd):
    return (f"See Sheet Scope Details with Parameter of Brand ={make} "
            f"Model {model} Task_CD ='{task_cd}' and PM_CD ='{pm_cd}'")


def _main_row(make, model, task_cd, pm_cd, km_text, km_code, sort,
              category="Vehicle Preventive Maintenance"):
    return [make, model, f"{category} {make} {model} {km_text}", task_cd,
            pm_cd, km_text, _instruction(make, model, task_cd, pm_cd),
            km_code, "", "3MTH", "Every 3 Months", 0, "NULL", category,
            task_cd[:3], category, sort]


def _workbook(path, *, main_rows, scope_rows, scope_sheet="Scope Details"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(MAIN_HEADERS)
    for r in main_rows:
        ws.append(r)
    if scope_sheet is not None:
        sc = wb.create_sheet(scope_sheet)
        sc.append(SCOPE_HEADERS)
        for r in scope_rows:
            sc.append(r)
    wb.save(path)
    return str(path)


def _triton_two_packages(path, *, scope_sheet="Scope Details",
                         second_scope_model="Triton",
                         task_cd="S02-90001"):
    """Two Triton packages. The second package's checklist lines can be
    left under another model, as in the reported file."""
    return _workbook(
        path,
        main_rows=[
            _main_row("Mitsubishi", "Triton", task_cd, "018-149-00001",
                      "1,000 km", "1KM", 1),
            _main_row("Mitsubishi", "Triton", task_cd, "018-149-00002",
                      "5,000 km", "5KMS", 2),
        ],
        scope_rows=[
            ["Mitsubishi", "Triton", "018-149-00001", task_cd, 1,
             "Change engine oil."],
            ["Mitsubishi", "Triton", "018-149-00001", task_cd, 2,
             "Check tire pressure."],
            ["Mitsubishi", second_scope_model, "018-149-00002", task_cd, 1,
             "Rotate tires."],
            ["Mitsubishi", second_scope_model, "018-149-00002", task_cd, 2,
             "Inspect brake pads."],
            ["Mitsubishi", second_scope_model, "018-149-00002", task_cd, 3,
             "Top up coolant."],
        ],
        scope_sheet=scope_sheet)


# ── 1. The checklist sheet under another name ───────────────────────────────

def test_checklist_sheet_is_found_even_when_it_is_not_named_scope_details(
        db, tmp_path):
    path = _triton_two_packages(tmp_path / "f.xlsx", scope_sheet="Sheet2")
    stats = import_pm_task_list(path, dry_run=True)
    assert stats["scope_sheet"] == "Sheet2"
    assert stats["scope_items_created"] == 5
    assert stats["packages_without_checklist"] == []


def test_checklist_sheet_named_scope_details_is_reported_by_name(
        db, tmp_path):
    path = _triton_two_packages(tmp_path / "f.xlsx")
    stats = import_pm_task_list(path, dry_run=True)
    assert stats["scope_sheet"] == "Scope Details"


def test_no_checklist_sheet_at_all_is_reported_as_none(db, tmp_path):
    path = _triton_two_packages(tmp_path / "f.xlsx", scope_sheet=None)
    stats = import_pm_task_list(path, dry_run=True)
    assert stats["scope_sheet"] is None


# ── 2. The instruction sentence is never a checklist ────────────────────────

def test_a_package_with_no_checklist_lines_gets_no_checklist_not_the_sentence(
        db, tmp_path):
    path = _triton_two_packages(tmp_path / "f.xlsx",
                                second_scope_model="Strada")
    stats = import_pm_task_list(path, dry_run=False)

    from app.modules.maintenance_config.models import PMScopeItem
    texts = [i.activity_description for i in PMScopeItem.query.all()]
    assert texts == ["Change engine oil.", "Check tire pressure."]
    assert not any("See Sheet Scope Details" in t for t in texts)
    assert stats["scope_items_created"] == 2
    assert stats["packages_created"] == 2


def test_packages_left_without_a_checklist_are_listed(db, tmp_path):
    path = _triton_two_packages(tmp_path / "f.xlsx",
                                second_scope_model="Strada")
    stats = import_pm_task_list(path, dry_run=True)
    missing = stats["packages_without_checklist"]
    assert len(missing) == 1
    assert missing[0]["task_cd"] == "S02-90001"
    assert missing[0]["pm_cd"] == "018-149-00002"
    assert missing[0]["model"] == "Triton"


def test_checklist_lines_filed_under_a_model_not_on_the_main_sheet_are_counted(
        db, tmp_path):
    path = _triton_two_packages(tmp_path / "f.xlsx",
                                second_scope_model="Strada")
    stats = import_pm_task_list(path, dry_run=True)
    assert stats["scope_rows_unmatched"] == 3
    assert stats["scope_unmatched_models"] == [
        {"make": "Mitsubishi", "model": "Strada", "rows": 3}]


def test_a_clean_file_reports_no_unmatched_checklist_lines(db, tmp_path):
    path = _triton_two_packages(tmp_path / "f.xlsx")
    stats = import_pm_task_list(path, dry_run=True)
    assert stats["scope_rows_unmatched"] == 0
    assert stats["scope_unmatched_models"] == []


def test_checklist_lines_of_an_excluded_category_are_not_called_unmatched(
        db, tmp_path):
    """Vehicle Registration rows are skipped on purpose. Their checklist
    lines DO have a main-sheet row, so they are not an error to fix."""
    path = _workbook(
        tmp_path / "f.xlsx",
        main_rows=[
            _main_row("Mitsubishi", "Triton", "S02-90001", "018-149-00001",
                      "1,000 km", "1KM", 1),
            _main_row("Mitsubishi", "Triton", "S01-90001", "018-149-00001",
                      "Annual renewal", "45KMS", 1,
                      category="Vehicle Registration"),
        ],
        scope_rows=[
            ["Mitsubishi", "Triton", "018-149-00001", "S02-90001", 1,
             "Change engine oil."],
            ["Mitsubishi", "Triton", "018-149-00001", "S01-90001", 1,
             "Renew registration."],
        ])
    stats = import_pm_task_list(path, dry_run=True)
    assert stats["groups_skipped_excluded"] == 1
    assert stats["scope_rows_unmatched"] == 0


# ── 3. A Task_CD that is already a PMS Profile ──────────────────────────────

def _strada_then_triton_same_code(tmp_path):
    strada = _workbook(
        tmp_path / "strada.xlsx",
        main_rows=[_main_row("Mitsubishi", "Strada", "S02-00032",
                             "018-149-00001", "1,000 km", "1KM", 1)],
        scope_rows=[["Mitsubishi", "Strada", "018-149-00001", "S02-00032",
                     1, "Change engine oil."]])
    triton = _workbook(
        tmp_path / "triton.xlsx",
        main_rows=[_main_row("Mitsubishi", "Triton", "S02-00032",
                             "018-149-00001", "1,000 km", "1KM", 1)],
        scope_rows=[["Mitsubishi", "Triton", "018-149-00001", "S02-00032",
                     1, "Change engine oil."]])
    return strada, triton


def test_a_task_cd_already_in_use_is_not_imported_into_that_profile(
        db, tmp_path):
    strada, triton = _strada_then_triton_same_code(tmp_path)
    import_pm_task_list(strada, dry_run=False)
    stats = import_pm_task_list(triton, dry_run=False)

    from app.modules.maintenance_config.models import PMSchedule
    rows = PMSchedule.query.filter_by(profile_code="S02-00032").all()
    assert len(rows) == 1
    assert rows[0].vehicle_model == "Strada"
    assert stats["packages_created"] == 0
    assert stats["groups_skipped_existing"] == 1


def test_the_dry_run_names_the_profile_that_already_owns_the_task_cd(
        db, tmp_path):
    strada, triton = _strada_then_triton_same_code(tmp_path)
    import_pm_task_list(strada, dry_run=False)
    stats = import_pm_task_list(triton, dry_run=True)

    assert stats["groups_skipped_existing"] == 1
    assert stats["existing_profiles"] == [{
        "task_cd": "S02-00032", "make": "Mitsubishi", "model": "Triton",
        "existing_vehicle": "Mitsubishi Strada", "packages": 1}]
    assert stats["packages_created"] == 0


def test_importing_the_same_file_twice_does_not_duplicate_its_packages(
        db, tmp_path):
    strada, _ = _strada_then_triton_same_code(tmp_path)
    import_pm_task_list(strada, dry_run=False)
    import_pm_task_list(strada, dry_run=False)
    from app.modules.maintenance_config.models import PMSchedule
    assert PMSchedule.query.count() == 1


def test_a_deactivated_profile_does_not_block_its_task_cd(db, tmp_path):
    strada, _ = _strada_then_triton_same_code(tmp_path)
    import_pm_task_list(strada, dry_run=False)
    from app.extensions import db as _db
    from app.modules.maintenance_config.models import PMSchedule
    PMSchedule.query.update({"is_active": False})
    _db.session.commit()

    stats = import_pm_task_list(strada, dry_run=False)
    assert stats["groups_skipped_existing"] == 0
    assert stats["packages_created"] == 1


def test_existing_profiles_are_ignored_when_the_caller_is_replacing_everything(
        db, tmp_path):
    """'Replace existing data' deletes the catalog first, so a preview
    of that run must not report everything as already existing."""
    strada, _ = _strada_then_triton_same_code(tmp_path)
    import_pm_task_list(strada, dry_run=False)
    stats = import_pm_task_list(strada, dry_run=True,
                                skip_existing_profiles=False)
    assert stats["groups_skipped_existing"] == 0
    assert stats["packages_created"] == 1


def test_a_new_task_cd_imports_normally_beside_existing_profiles(
        db, tmp_path):
    strada, _ = _strada_then_triton_same_code(tmp_path)
    import_pm_task_list(strada, dry_run=False)
    triton = _triton_two_packages(tmp_path / "t.xlsx")
    stats = import_pm_task_list(triton, dry_run=False)
    assert stats["groups_skipped_existing"] == 0
    assert stats["packages_created"] == 2


# ── 4. Template leftovers ───────────────────────────────────────────────────

def test_example_rows_from_the_template_are_never_imported(db, tmp_path):
    path = _workbook(
        tmp_path / "f.xlsx",
        main_rows=[
            _main_row("Mitsubishi", "Strada", "EXAMPLE-0001",
                      "000-000-00001", "5,000 km", "5KMS", 1),
            _main_row("Mitsubishi", "Triton", "S02-90001", "018-149-00001",
                      "1,000 km", "1KM", 1),
        ],
        scope_rows=[
            ["Mitsubishi", "Strada", "000-000-00001", "EXAMPLE-0001", 1,
             "Example step."],
            ["Mitsubishi", "Triton", "018-149-00001", "S02-90001", 1,
             "Change engine oil."],
        ])
    stats = import_pm_task_list(path, dry_run=False)
    from app.modules.maintenance_config.models import PMSchedule
    assert [p.profile_code for p in PMSchedule.query.all()] == ["S02-90001"]
    assert stats["example_rows_ignored"] == 1
    assert stats["scope_rows_unmatched"] == 0


def test_rows_with_no_task_cd_are_ignored_not_counted_as_a_group(
        db, tmp_path):
    """A note typed under the data, or blank rows left by deleting in
    Excel, must not appear as a skipped group."""
    note = [None] * len(MAIN_HEADERS)
    note[0] = "^ Delete the example row before uploading."
    path = _workbook(
        tmp_path / "f.xlsx",
        main_rows=[
            _main_row("Mitsubishi", "Triton", "S02-90001", "018-149-00001",
                      "1,000 km", "1KM", 1),
            note,
            [None] * len(MAIN_HEADERS),
        ],
        scope_rows=[["Mitsubishi", "Triton", "018-149-00001", "S02-90001",
                     1, "Change engine oil."]])
    stats = import_pm_task_list(path, dry_run=True)
    assert stats["groups_processed"] == 1
    assert stats["groups_skipped_no_category"] == 0


# ── 5. What the review screen needs ─────────────────────────────────────────

def test_every_package_is_listed_for_the_review_screen(db, tmp_path):
    path = _triton_two_packages(tmp_path / "f.xlsx")
    stats = import_pm_task_list(path, dry_run=True)
    assert [p["pm_cd"] for p in stats["packages"]] == [
        "018-149-00001", "018-149-00002"]
    first = stats["packages"][0]
    assert first["task_cd"] == "S02-90001"
    assert first["model"] == "Triton"
    assert first["interval_km"] == 1000
    assert first["interval_days"] == 90
    assert first["activity_count"] == 2
    assert stats["packages_truncated"] is False


def test_a_model_missing_from_the_vehicle_models_master_is_reported(
        db, tmp_path):
    path = _triton_two_packages(tmp_path / "f.xlsx")
    stats = import_pm_task_list(path, dry_run=True)
    assert stats["unknown_models"] == [
        {"make": "Mitsubishi", "model": "Triton"}]


def test_a_model_present_in_the_master_is_not_reported(db, tmp_path):
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService, VehicleModelService)
    brand = VehicleBrandService().create("Mitsubishi")
    VehicleModelService().create(brand.id, "Triton")

    path = _triton_two_packages(tmp_path / "f.xlsx")
    stats = import_pm_task_list(path, dry_run=True)
    assert stats["unknown_models"] == []


def test_a_category_the_importer_does_not_know_is_named(db, tmp_path):
    path = _workbook(
        tmp_path / "f.xlsx",
        main_rows=[_main_row("Mitsubishi", "Triton", "S09-90001",
                             "018-149-00001", "1,000 km", "1KM", 1,
                             category="Wiper Replacement")],
        scope_rows=[])
    stats = import_pm_task_list(path, dry_run=True)
    assert stats["groups_skipped_no_category"] == 1
    assert stats["unknown_categories"] == ["Wiper Replacement"]


def test_a_known_brand_with_an_unknown_model_is_stored_as_text_not_brand_only(
        db, tmp_path):
    """Brand in the master, model not (a new model such as Triton). The
    package must still name its model. Stored with the brand link alone
    it has no model and no text, and the due-date engine treats a
    schedule with no make/model as GLOBAL -- every other vehicle would
    have inherited the Triton's PMS."""
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService)
    VehicleBrandService().create("Mitsubishi")

    path = _triton_two_packages(tmp_path / "f.xlsx")
    stats = import_pm_task_list(path, dry_run=False)

    from app.modules.maintenance_config.models import PMSchedule
    rows = PMSchedule.query.all()
    assert len(rows) == 2
    for r in rows:
        assert r.vehicle_brand_id is None
        assert r.vehicle_model_id is None
        assert r.vehicle_make == "Mitsubishi"
        assert r.vehicle_model == "Triton"
    assert stats["unknown_models"] == [
        {"make": "Mitsubishi", "model": "Triton"}]


def test_a_known_brand_and_model_are_linked_by_id(db, tmp_path):
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService, VehicleModelService)
    brand = VehicleBrandService().create("Mitsubishi")
    model = VehicleModelService().create(brand.id, "Triton")

    path = _triton_two_packages(tmp_path / "f.xlsx")
    import_pm_task_list(path, dry_run=False)

    from app.modules.maintenance_config.models import PMSchedule
    for r in PMSchedule.query.all():
        assert r.vehicle_brand_id == brand.id
        assert r.vehicle_model_id == model.id
        assert r.vehicle_make is None
