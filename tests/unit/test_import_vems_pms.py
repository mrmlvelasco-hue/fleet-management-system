import openpyxl

from import_vems_pms import import_pms, _parse_step, _split_scope_into_items


def _make_pms_workbook(path):
    """A small, self-contained workbook shaped exactly like the real
    VEMS PMS export -- same column positions, same category strings --
    so the tests exercise the real import_pms() code paths without
    depending on a real fleet's uploaded file.

    Three of these tests previously hardcoded
    "/mnt/user-data/uploads/VEMS_Masterdata_for_vehicle.xlsx" -- a file
    that only ever existed in one past working session's sandbox. That
    made them unrunnable on any other machine, including a fresh clone,
    which is exactly the failure this fixture replaces.

    Column order (0-indexed, matching scripts/import_vems_pms.py):
      0 Task_CD, 1 task description, 2 make, 3 model, 4 category,
      10 scope text, 11 km step, 12 calendar step, 14 sort.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "PMS"
    ws.append(["Task_CD", "Task_Desc", "Make", "Model", "Category",
              "c5", "c6", "c7", "c8", "c9", "Scope", "KM_Step",
              "Cal_Step", "c13", "Sort"])

    def row(task_cd, desc, make, model, category, scope, km_step,
           cal_step, sort_val):
        return [task_cd, desc, make, model, category, None, None, None,
               None, None, scope, km_step, cal_step, None, sort_val]

    # Two PM packages for a Toyota Hilux, each with a two-step numbered
    # scope -- exercises grouping, scope-item splitting, and the km/cal
    # step parsing together.
    ws.append(row("T-001", "5,000 km Service", "Toyota", "Hilux",
                  "Vehicle Preventive Maintenance",
                  "1. Change engine oil. 2. Check brake pads.",
                  "5KMS", "0", 1))
    ws.append(row("T-001", "5,000 km Service", "Toyota", "Hilux",
                  "Vehicle Preventive Maintenance",
                  "1. Rotate tires. 2. Inspect suspension.",
                  "5KMS", "0", 2))

    # A tire-replacement package for a different make/model.
    ws.append(row("T-002", "Tire Change", "Isuzu", "Elf",
                  "Tire Replacement",
                  "1. Replace all four tires.", "40KMS", "0", 1))

    # A row whose category must be EXCLUDED outright -- this is what
    # test_vehicle_registration_category_excluded checks for.
    ws.append(row("T-003", "LTO Renewal", "Toyota", "Hilux",
                  "Vehicle Registration",
                  "1. Renew LTO registration.", "0", "1YRS", 1))

    wb.save(path)
    return path


def test_dry_run_reports_stats_without_writing(tmp_path, db):
    path = _make_pms_workbook(tmp_path / "pms.xlsx")
    stats = import_pms(str(path), dry_run=True, limit_groups=20)
    assert stats["groups_processed"] > 0
    assert stats["packages_created"] > 0

    from app.modules.maintenance_config.models import PMSchedule
    assert PMSchedule.query.count() == 0


def test_vehicle_registration_category_excluded(tmp_path, db):
    path = _make_pms_workbook(tmp_path / "pms.xlsx")
    stats = import_pms(str(path), dry_run=True, limit_groups=None)
    assert stats["groups_skipped_excluded"] > 0


def test_real_import_limited_creates_schedules_and_scope_items(tmp_path, db):
    # This vehicle-registration exclusion check happened to be the ONLY
    # thing test_real_import_limited previously verified beyond what the
    # dry-run tests above already cover, so brand/model FK resolution is
    # asserted directly here instead via a real Vehicle Brand/Model
    # created ahead of the import.
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService, VehicleModelService)
    brand = VehicleBrandService().create(name="Toyota")
    VehicleModelService().create(name="Hilux", brand_id=brand.id)

    path = _make_pms_workbook(tmp_path / "pms.xlsx")
    stats = import_pms(str(path), dry_run=False, limit_groups=10)
    assert stats["packages_created"] > 0

    from app.modules.maintenance_config.models import (
        PMSchedule, PMScopeTemplate)
    assert PMSchedule.query.count() == stats["packages_created"]
    assert PMScopeTemplate.query.count() > 0

    # Confirm at least one schedule resolved via a real FK brand/model
    # match -- the Toyota Hilux rows should hit the brand/model created
    # above.
    fk_matched = PMSchedule.query.filter(
        PMSchedule.vehicle_brand_id.isnot(None)).count()
    assert fk_matched > 0


def test_parse_step_km():
    assert _parse_step("5KMS", {"KM": 1000, "KMS": 1000}) == 5000
    assert _parse_step("1KM", {"KM": 1000, "KMS": 1000}) == 1000
    assert _parse_step("0", {"KM": 1000, "KMS": 1000}) == 0
    assert _parse_step(None, {"KM": 1000, "KMS": 1000}) == 0


def test_parse_step_calendar():
    assert _parse_step("3MTH", {"MTH": 30, "YRS": 365}) == 90
    assert _parse_step("2YRS", {"MTH": 30, "YRS": 365}) == 730


def test_split_scope_into_items():
    text = "1. Replace oil.  2. Check brakes.  3. Inspect tires."
    items = _split_scope_into_items(text)
    assert items == ["Replace oil.", "Check brakes.", "Inspect tires."]


def test_split_scope_handles_two_digit_numbers():
    text = "01. First step. 02. Second step. 23. Last step."
    items = _split_scope_into_items(text)
    assert len(items) == 3
    assert items[-1] == "Last step."
