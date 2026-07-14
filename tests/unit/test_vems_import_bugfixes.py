import sys
sys.path.insert(0, "/home/claude/fms/scripts")

import openpyxl
import os

from import_vems_makemodel import import_make_model
from import_vems_pms import import_pms


def _make_test_workbook(path, make_model_rows, pms_rows=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Make and Model"
    ws.append(["Make_Idx", "Make_CD", "Description", "Brand_Idx", "Model_CD",
              "Description", "Make_Pidx", "CommercialYield"])
    for r in make_model_rows:
        ws.append(r)

    ws2 = wb.create_sheet("PMS")
    ws2.append(["Task_CD", "Description", "Make", "Model", "Planned",
               "PM_Pidx", "Task_CD2", "PM_Task_Pidx", "PM_CD", "Description2",
               "Scope", "KM Reading", "Calendar", "Hourly", "Sort"])
    for r in (pms_rows or []):
        ws2.append(r)
    wb.save(path)


def test_dry_run_does_not_inflate_brand_count_for_repeated_new_brand(db, tmp_path):
    """Regression: a brand-new Make with multiple Models used to get
    counted as 'created' once per row instead of once per unique brand,
    because the cache conflated 'not yet seen' with 'cached as None
    because dry-run never actually creates it'."""
    path = str(tmp_path / "test.xlsx")
    _make_test_workbook(path, [
        (1, 1, "Ford", 1, 1, "Escape", 1, "NULL"),
        (1, 1, "Ford", 2, 2, "Everest", 1, "NULL"),
        (1, 1, "Ford", 3, 3, "Explorer", 1, "NULL"),
        (1, 1, "Ford", 4, 4, "Focus", 1, "NULL"),
    ])
    result = import_make_model(path, dry_run=True)
    assert result["brands_created"] == 1  # one unique brand, not 4
    assert result["models_created"] == 4


def test_dry_run_matches_real_run_brand_count(db, tmp_path):
    path = str(tmp_path / "test2.xlsx")
    _make_test_workbook(path, [
        (1, 1, "Toyota", 1, 1, "Vios", 1, "NULL"),
        (1, 1, "Toyota", 2, 2, "Hilux", 1, "NULL"),
        (2, 2, "Honda", 3, 3, "City", 2, "NULL"),
        (2, 2, "Honda", 4, 4, "Civic", 2, "NULL"),
        (2, 2, "Honda", 5, 5, "CRV", 2, "NULL"),
    ])
    dry = import_make_model(path, dry_run=True)
    real = import_make_model(path, dry_run=False)
    assert dry["brands_created"] == real["brands_created"] == 2
    assert dry["models_created"] == real["models_created"] == 5


def test_dry_run_reports_scope_items_that_would_be_created(db, tmp_path):
    """Regression: scope_items_created stayed 0 in dry-run even though
    activity_texts were computed for the samples — the counter increment
    was nested inside the `if not dry_run:` write block."""
    path = str(tmp_path / "test3.xlsx")
    scope = "1. Change oil.  2. Check brakes.  3. Inspect tires."
    _make_test_workbook(path, [
        (1, 1, "Toyota", 1, 1, "Hilux", 1, "NULL"),
    ], pms_rows=[
        ("T-001", "Toyota Hilux PMS", "Toyota", "Hilux",
        "Vehicle Preventive Maintenance", 1, "T-001", 1, "PM-001",
        "desc", scope, "5KMS", "3MTH", "0", 1),
    ])
    stats = import_pms(path, dry_run=True)
    assert stats["scope_items_created"] == 3
